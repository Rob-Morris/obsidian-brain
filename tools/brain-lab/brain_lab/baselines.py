from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from .application import Application, HandlerResult, OperationContext, OperationFailure
from .docker import IMPORTED_LABEL, DockerClient, DockerError
from .model import (
    BaselineRecipe,
    EffectCertainty,
    EvidenceCompleteness,
    Outcome,
    PreparationSpec,
    canonical_json,
    require_keys,
)
from .manifests import read_gzip_json


VAULT_PATH = "/home/brain/vault"
SOURCE_PATH = "/opt/brain-lab/source"


def _stdout(execution) -> str:
    if execution.stdout.truncated:
        raise RuntimeError("command output needed for a gate exceeded its retention bound")
    return Path(execution.stdout.path).read_text(encoding="utf-8")


def _run_required(context: OperationContext, container: str, argv: Sequence[str], name: str, *, timeout: float = 1800):
    execution = context.docker.exec(
        container,
        argv,
        working_directory=VAULT_PATH if name != "install" else "/home/brain",
        environment={"BRAIN_VAULT_ROOT": VAULT_PATH},
        evidence_directory=context.evidence_directory / name,
        timeout_seconds=timeout,
    )
    if not execution.succeeded:
        raise RuntimeError(f"preparation command {name} failed with exit code {execution.returncode}")
    return execution


def _container_manifest(context: OperationContext, container: str, scope: str, sequence: str) -> dict[str, Any]:
    root = "/home/brain" if scope == "prepared" else VAULT_PATH
    execution = _run_required(
        context,
        container,
        [
            "python3.12",
            "/usr/local/lib/brain-lab/tree_manifest.py",
            "--root",
            root,
            "--scope",
            scope,
            "--gzip",
        ],
        sequence,
        timeout=600,
    )
    if execution.stdout.truncated:
        raise RuntimeError("compressed manifest exceeded its retention bound")
    return read_gzip_json(Path(execution.stdout.path))


def _render_expected(value: Any, values: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        return value.format_map(values)
    if isinstance(value, list):
        return [_render_expected(item, values) for item in value]
    if isinstance(value, dict):
        return {key: _render_expected(item, values) for key, item in value.items()}
    return value


def _json_value(payload: Any, path: str) -> Any:
    value = payload
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise KeyError(path)
        value = value[part]
    return value


def _run_health_gates(
    context: OperationContext,
    container: str,
    adapter,
    values: Mapping[str, str],
    mode: str,
) -> list[dict[str, Any]]:
    context.docker.reserve_retry_commands(
        sum(
            gate.retry.maximum_attempts - 1
            for gate in adapter.health
            if gate.retry is not None
            and (mode in gate.required_for or mode != "preserve" or gate.preserve_safe)
        )
    )
    results = []
    for index, gate in enumerate(adapter.health):
        required = mode in gate.required_for
        if mode == "preserve" and not required and not gate.preserve_safe:
            results.append(
                {
                    "id": gate.gate_id,
                    "required": False,
                    "status": "not-run",
                    "reason": "preserve mode does not execute commands that may alter the copied vault",
                }
            )
            continue
        command = adapter.render(gate.command, values)
        maximum_attempts = gate.retry.maximum_attempts if gate.retry else 1
        execution = None
        stdout = ""
        expected = gate.expected_stdout.format_map(values) if gate.expected_stdout else None
        expected_json = _render_expected(gate.expected_json, values) if gate.expected_json else None
        expected_matched = False
        passed = False
        attempts = 0
        for attempt in range(1, maximum_attempts + 1):
            attempts = attempt
            execution = context.docker.exec(
                container,
                command,
                working_directory=VAULT_PATH,
                environment={"BRAIN_VAULT_ROOT": VAULT_PATH},
                evidence_directory=(
                    context.evidence_directory
                    / f"health-{index:02d}-{gate.gate_id}"
                    / f"attempt-{attempt:02d}"
                ),
                timeout_seconds=gate.timeout_seconds,
            )
            stdout = _stdout(execution) if not execution.stdout.truncated else ""
            expected_matched = expected is None or stdout.strip() == expected
            if expected_json is not None:
                try:
                    payload = json.loads(stdout)
                    expected_matched = expected_matched and all(
                        _json_value(payload, path) == value
                        for path, value in expected_json.items()
                    )
                except (json.JSONDecodeError, KeyError):
                    expected_matched = False
            passed = execution.succeeded and expected_matched
            if passed or gate.retry is None or attempt == maximum_attempts:
                break
            try:
                envelope = json.loads(stdout)
            except json.JSONDecodeError:
                break
            error = envelope.get("error") if isinstance(envelope, dict) else None
            if (
                not isinstance(error, dict)
                or error.get("retryable") is not True
                or error.get("code") not in gate.retry.retryable_error_codes
            ):
                break
            details = error.get("details")
            status = details.get("runtime_status") if isinstance(details, dict) else None
            retry_after_ms = status.get("retry_after_ms") if isinstance(status, dict) else None
            requested_delay = (
                float(retry_after_ms) / 1000
                if isinstance(retry_after_ms, (int, float)) and retry_after_ms >= 0
                else gate.retry.maximum_delay_seconds
            )
            time.sleep(min(requested_delay, gate.retry.maximum_delay_seconds))
        assert execution is not None
        results.append(
            {
                "id": gate.gate_id,
                "required": required,
                "status": "passed" if passed else "failed",
                "attempts": attempts,
                "returncode": execution.returncode,
                "expected_stdout": expected,
                "expected_json": expected_json,
                "expected_matched": expected_matched,
                "stdout_sha256": execution.stdout.sha256,
                "stderr_sha256": execution.stderr.sha256,
            }
        )
    failed = [result["id"] for result in results if result["required"] and result["status"] != "passed"]
    if failed:
        raise RuntimeError(f"required baseline health gates failed: {', '.join(failed)}")
    return results


def _attempt_dockerfile() -> str:
    return """ARG BASE_IMAGE
ARG SOURCE_IMAGE
ARG SEED_IMAGE
FROM ${SOURCE_IMAGE} AS source
FROM ${SEED_IMAGE} AS seed
FROM ${BASE_IMAGE}
USER root
COPY --from=source --chown=brain:brain /bundle/source /opt/brain-lab/source
COPY --from=seed --chown=brain:brain /bundle/vault /opt/brain-lab/seed
RUN install -d -o brain -g brain /home/brain/vault
USER brain
WORKDIR /home/brain
"""


def _validate_recipe_inputs(base: dict, source: dict, seed: dict, mode: str) -> None:
    if mode not in {"template", "preserve", "rehydrate"}:
        raise ValueError("baseline mode must be template, preserve, or rehydrate")
    seed_kind = seed["spec"]["kind"]
    if mode == "template" and seed_kind != "template":
        raise ValueError("template preparation requires a template seed")
    if mode in {"preserve", "rehydrate"} and seed_kind != "imported":
        raise ValueError(f"{mode} preparation requires an imported seed")
    if base["image"]["platform"] != source["image"]["platform"] or base["image"]["platform"] != seed["image"]["platform"]:
        raise ValueError("base, source and seed must use the same explicit OCI platform")
    source_spec = source["spec"]
    seed_spec = seed["spec"]
    if seed_kind == "template" and seed_spec["source_id"] != source["resource_id"]:
        raise ValueError("template seed and source bundle do not share the same source identity")
    if seed_kind == "imported":
        if seed_spec["core_version"] != source_spec["core_version"]:
            raise ValueError("imported vault VERSION does not match the selected source")
        if seed_spec["core_sha256"] != source_spec["core_sha256"]:
            raise ValueError("imported vault Brain Core fingerprint does not match the selected source")


def _prepare_container(
    context: OperationContext,
    *,
    container_id: str,
    mode: str,
    adapter,
    values: Mapping[str, str],
    expected_core_sha256: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    _run_required(
        context,
        container_id,
        adapter.render(adapter.install, values),
        "03-install",
        timeout=timeout_seconds,
    )
    for index, command in enumerate(adapter.post_install):
        _run_required(
            context,
            container_id,
            adapter.render(command, values),
            f"03-post-install-{index:02d}",
            timeout=900,
        )
    if mode == "template":
        for index, command in enumerate(adapter.template_prepare):
            _run_required(
                context,
                container_id,
                adapter.render(command, values),
                f"03-template-prepare-{index:02d}",
                timeout=900,
            )

    portable_before = None
    core_before = None
    portable_after = None
    core_after = None
    if mode in {"preserve", "rehydrate"}:
        replace = (
            "find /home/brain/vault -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + "
            "&& cp -a /opt/brain-lab/seed/. /home/brain/vault/"
        )
        _run_required(context, container_id, ["bash", "-c", replace], "04-replace-vault", timeout=600)
        portable_before = _container_manifest(context, container_id, "portable", "05-portable-before")
        core_before = _container_manifest(context, container_id, "core", "06-core-before")
        if core_before["tree_sha256"] != expected_core_sha256:
            raise RuntimeError("copied imported Core no longer matches the selected source fingerprint")
        if mode == "rehydrate":
            for index, command in enumerate(adapter.rehydrate):
                _run_required(
                    context,
                    container_id,
                    adapter.render(command, values),
                    f"07-rehydrate-{index:02d}",
                    timeout=900,
                )
            portable_after = _container_manifest(
                context, container_id, "portable", "08-portable-after"
            )
            core_after = _container_manifest(context, container_id, "core", "09-core-after")
        else:
            portable_after = portable_before
            core_after = core_before
        if portable_after["tree_sha256"] != portable_before["tree_sha256"]:
            raise RuntimeError("rehydration changed portable vault content")
        if core_after["tree_sha256"] != core_before["tree_sha256"]:
            raise RuntimeError("rehydration replaced or modified Brain Core")

    installed_core = core_after or _container_manifest(
        context, container_id, "core", "19-installed-core"
    )
    if installed_core["tree_sha256"] != expected_core_sha256:
        raise RuntimeError("installed Brain Core fingerprint does not match the selected source")
    health = _run_health_gates(context, container_id, adapter, values, mode)
    prepared_manifest = _container_manifest(
        context, container_id, "prepared", "20-prepared-manifest"
    )
    return {
        "health": health,
        "portable_before": portable_before,
        "portable_after": portable_after,
        "core_before": core_before,
        "core_after": core_after,
        "installed_core": installed_core,
        "prepared_manifest": prepared_manifest,
    }


def _rebuild_fingerprint(recipe: BaselineRecipe, prepared: Mapping[str, Any]) -> str:
    successful_health = sorted(
        item["id"] for item in prepared["health"] if item["status"] == "passed"
    )
    return hashlib.sha256(
        canonical_json(
            {
                "recipe": asdict(recipe),
                "prepared_manifest": prepared["prepared_manifest"]["tree_sha256"],
                "successful_health": successful_health,
            }
        ).encode("utf-8")
    ).hexdigest()


def _prepare(
    context: OperationContext,
    request: dict[str, Any],
    *,
    force_rebuild: bool = False,
    expected_rebuild_fingerprint: str | None = None,
) -> HandlerResult:
    require_keys(
        request,
        required=frozenset({"base_id", "source_id", "seed_id", "mode"}),
        optional=frozenset({"adapter_id", "timeout_seconds"}),
    )
    base = context.store.read("base", request["base_id"])
    source = context.store.read("source", request["source_id"])
    seed = context.store.read("seed", request["seed_id"])
    mode = request["mode"]
    _validate_recipe_inputs(base, source, seed, mode)
    adapter = context.compatibility.select(source["spec"]["core_version"])
    if request.get("adapter_id") not in {None, adapter.adapter_id}:
        raise ValueError(
            f"requested adapter {request['adapter_id']!r} does not own Brain {source['spec']['core_version']}"
        )
    preparation = PreparationSpec(mode, adapter.adapter_id, adapter.revision)
    recipe = BaselineRecipe(request["base_id"], request["source_id"], request["seed_id"], preparation)
    baseline_id = recipe.identity()
    if not force_rebuild and context.store.receipt_path("baseline", baseline_id).exists():
        receipt = context.store.read("baseline", baseline_id)
        inspect = context.docker.image_inspect(receipt["image"]["tag"], context.evidence_directory / "reuse-inspect")
        context.docker.verify_resource_labels(inspect, "baseline", baseline_id)
        if receipt.get("status") != "succeeded" or inspect["Id"] != receipt["image"]["id"]:
            raise ValueError("recorded baseline is not a reusable successful immutable image")
        return HandlerResult(
            resource={"kind": "baseline", "id": baseline_id, "docker_id": inspect["Id"]},
            payload={
                "reused": True,
                "rebuild_fingerprint": receipt["rebuild_fingerprint"],
                "health": receipt["health"],
            },
        )

    attempt_id = f"attempt-{uuid.uuid4().hex[:20]}"
    imported = bool(seed["spec"].get("imported"))
    attempt_labels = DockerClient.labels("attempt", attempt_id, imported=imported)
    attempt_tag = DockerClient.deterministic_tag("attempt", attempt_id)
    attempt_receipt = {
        "status": "preparing",
        "imported": imported,
        "baseline_id": baseline_id,
        "recipe": asdict(recipe),
        "image": {"tag": attempt_tag, "labels": attempt_labels},
    }
    context.store.write("attempt", attempt_id, attempt_receipt)
    container_id = None
    baseline_published = False
    attempt_image_removed = False
    try:
        context.docker.build(
            _attempt_dockerfile(),
            tag=attempt_tag,
            platform=base["image"]["platform"],
            labels=attempt_labels,
            build_arguments={
                "BASE_IMAGE": base["image"]["tag"],
                "SOURCE_IMAGE": source["image"]["tag"],
                "SEED_IMAGE": seed["image"]["tag"],
            },
            evidence_directory=context.evidence_directory / "00-attempt-image",
            timeout_seconds=float(request.get("timeout_seconds", 1800)),
        )
        attempt_inspect = context.docker.image_inspect(attempt_tag, context.evidence_directory / "01-attempt-inspect")
        attempt_receipt["image"]["id"] = attempt_inspect["Id"]
        container_name = DockerClient.deterministic_container_name("attempt", attempt_id)
        container_id, _ = context.docker.start_container(
            attempt_inspect["Id"],
            name=container_name,
            platform=base["image"]["platform"],
            network="bridge",
            labels=attempt_labels,
            evidence_directory=context.evidence_directory / "02-attempt-start",
        )
        attempt_receipt["container"] = {"id": container_id, "name": container_name}
        context.store.write("attempt", attempt_id, attempt_receipt)
        values = {
            "source": SOURCE_PATH,
            "vault": VAULT_PATH,
            "version": source["spec"]["core_version"],
            "cli_version": source["spec"]["cli_version"],
        }
        prepared = _prepare_container(
            context,
            container_id=container_id,
            mode=mode,
            adapter=adapter,
            values=values,
            expected_core_sha256=source["spec"]["core_sha256"],
            timeout_seconds=float(request.get("timeout_seconds", 1800)),
        )
        health = prepared["health"]
        rebuild_fingerprint = _rebuild_fingerprint(recipe, prepared)
        if (
            expected_rebuild_fingerprint is not None
            and rebuild_fingerprint != expected_rebuild_fingerprint
        ):
            raise RuntimeError(
                "clean baseline rebuild produced a different normalised fingerprint"
            )
        baseline_labels = DockerClient.labels("baseline", baseline_id, imported=imported)
        baseline_tag = DockerClient.deterministic_tag("baseline", baseline_id)
        image_id, _ = context.docker.commit(
            container_id,
            baseline_tag,
            baseline_labels,
            context.evidence_directory / "21-commit",
        )
        inspect = context.docker.image_inspect(baseline_tag, context.evidence_directory / "22-inspect")
        receipt = {
            "status": "succeeded",
            "imported": imported,
            "recipe": asdict(recipe),
            "attempt_id": attempt_id,
            "adapter": {
                "id": adapter.adapter_id,
                "revision": adapter.revision,
                "version": source["spec"]["core_version"],
            },
            "health": health,
            "portable_before": prepared["portable_before"],
            "portable_after": prepared["portable_after"],
            "core_before": prepared["core_before"],
            "core_after": prepared["core_after"],
            "installed_core": prepared["installed_core"],
            "prepared_manifest": prepared["prepared_manifest"],
            "rebuild_fingerprint": rebuild_fingerprint,
            "image": {
                "id": inspect["Id"],
                "tag": baseline_tag,
                "platform": base["image"]["platform"],
                "size": inspect.get("Size"),
                "labels": baseline_labels,
            },
        }
        context.store.write("baseline", baseline_id, receipt)
        baseline_published = True
        attempt_receipt["status"] = "succeeded"
        attempt_receipt["promoted_baseline_id"] = baseline_id
        context.store.write("attempt", attempt_id, attempt_receipt)
        cleanup = {"complete": True, "survivors": [], "errors": []}
        try:
            context.docker.remove_container(
                container_id, context.evidence_directory / "23-remove-attempt-container"
            )
            container_id = None
        except DockerError as cleanup_error:
            cleanup["complete"] = False
            cleanup["survivors"].append({"kind": "container", "id": container_id})
            cleanup["errors"].append(
                {"type": type(cleanup_error).__name__, "message": str(cleanup_error)}
            )
        try:
            context.docker.remove_image(
                attempt_inspect["Id"], context.evidence_directory / "24-remove-attempt-image"
            )
            attempt_image_removed = True
        except DockerError as cleanup_error:
            cleanup["complete"] = False
            cleanup["survivors"].append({"kind": "image", "id": attempt_inspect["Id"]})
            cleanup["errors"].append(
                {"type": type(cleanup_error).__name__, "message": str(cleanup_error)}
            )
        attempt_receipt["cleanup"] = cleanup
        context.store.write("attempt", attempt_id, attempt_receipt)
        return HandlerResult(
            resource={"kind": "baseline", "id": baseline_id, "docker_id": image_id},
            payload={
                "reused": False,
                "rebuild_fingerprint": rebuild_fingerprint,
                "health": health,
                "attempt_id": attempt_id,
                "cleanup": cleanup,
            },
            effect_certainty=(
                EffectCertainty.COMMITTED if cleanup["complete"] else EffectCertainty.PARTIAL
            ),
            evidence_completeness=(
                EvidenceCompleteness.PARTIAL
                if any(
                    execution.stdout.truncated or execution.stderr.truncated
                    for execution in context.docker.executions[context.execution_start :]
                )
                else EvidenceCompleteness.COMPLETE
            ),
        )
    except (DockerError, OSError, RuntimeError, ValueError) as exc:
        if baseline_published:
            survivors = []
            if container_id:
                survivors.append({"kind": "container", "id": container_id})
            if not attempt_image_removed:
                survivors.append({"kind": "image", "id": attempt_inspect["Id"]})
            cleanup = {
                "complete": False,
                "survivors": survivors,
                "errors": [{"type": type(exc).__name__, "message": str(exc)}],
            }
            attempt_receipt["status"] = "succeeded-with-cleanup-survivors"
            attempt_receipt["cleanup"] = cleanup
            try:
                context.store.write("attempt", attempt_id, attempt_receipt)
            except OSError:
                cleanup["receipt_persisted"] = False
            return HandlerResult(
                resource={"kind": "baseline", "id": baseline_id, "docker_id": image_id},
                payload={
                    "reused": False,
                    "rebuild_fingerprint": rebuild_fingerprint,
                    "health": health,
                    "attempt_id": attempt_id,
                    "cleanup": cleanup,
                },
                effect_certainty=EffectCertainty.PARTIAL,
                evidence_completeness=EvidenceCompleteness.PARTIAL,
            )
        attempt_receipt["status"] = "failed-preparation"
        attempt_receipt["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        if container_id:
            attempt_receipt["retained_container_id"] = container_id
        context.store.write("attempt", attempt_id, attempt_receipt)
        execution = exc.execution if isinstance(exc, DockerError) else None
        outcome = (
            Outcome.TIMEOUT
            if execution is not None and execution.timed_out
            else Outcome.CANCELLED
            if execution is not None and execution.cancelled
            else Outcome.FAILURE
        )
        complete = all(
            item.evidence_complete
            for item in context.docker.executions[context.execution_start :]
        )
        raise OperationFailure(
            str(exc),
            outcome=outcome,
            effect_certainty=EffectCertainty.UNKNOWN,
            evidence_completeness=(
                EvidenceCompleteness.COMPLETE if complete else EvidenceCompleteness.PARTIAL
            ),
            resource={
                "kind": "attempt",
                "id": attempt_id,
                **({"docker_id": container_id} if container_id else {}),
            },
            payload={
                "attempt_id": attempt_id,
                "baseline_id": baseline_id,
                "retained_container_id": container_id,
                "attempt_receipt": attempt_receipt,
            },
            error_type=type(exc).__name__,
        ) from exc


def prepare_baseline(context: OperationContext, request: dict[str, Any]) -> HandlerResult:
    return _prepare(context, request)


def inspect_baseline(context: OperationContext, request: dict[str, Any]) -> HandlerResult:
    require_keys(request, required=frozenset({"id"}))
    receipt = context.store.read("baseline", request["id"])
    inspect = context.docker.image_inspect(receipt["image"]["tag"], context.evidence_directory / "inspect")
    context.docker.verify_resource_labels(inspect, "baseline", request["id"])
    return HandlerResult(
        resource={"kind": "baseline", "id": request["id"], "docker_id": inspect["Id"]},
        payload={"receipt": receipt, "docker": inspect},
    )


def verify_baseline(context: OperationContext, request: dict[str, Any]) -> HandlerResult:
    require_keys(request, required=frozenset({"id"}))
    receipt = context.store.read("baseline", request["id"])
    inspect = context.docker.image_inspect(receipt["image"]["tag"], context.evidence_directory / "00-inspect")
    context.docker.verify_resource_labels(inspect, "baseline", request["id"])
    valid = receipt.get("status") == "succeeded" and inspect["Id"] == receipt["image"]["id"]
    if not valid:
        raise ValueError("baseline receipt and immutable Docker image do not agree")
    return HandlerResult(
        resource={"kind": "baseline", "id": request["id"], "docker_id": inspect["Id"]},
        payload={"verified": True, "rebuild_fingerprint": receipt["rebuild_fingerprint"]},
    )


def rebuild_baseline(context: OperationContext, request: dict[str, Any]) -> HandlerResult:
    require_keys(request, required=frozenset({"id"}), optional=frozenset({"timeout_seconds"}))
    old = context.store.read("baseline", request["id"])
    inspect = context.docker.image_inspect(old["image"]["tag"], context.evidence_directory / "00-old-inspect")
    context.docker.verify_resource_labels(inspect, "baseline", request["id"])
    recipe = old["recipe"]
    rebuilt = _prepare(
        context,
        {
            "base_id": recipe["base_id"],
            "source_id": recipe["source_id"],
            "seed_id": recipe["seed_id"],
            "mode": recipe["preparation"]["mode"],
            "adapter_id": recipe["preparation"]["adapter_id"],
            **({"timeout_seconds": request["timeout_seconds"]} if "timeout_seconds" in request else {}),
        },
        force_rebuild=True,
        expected_rebuild_fingerprint=old["rebuild_fingerprint"],
    )
    new_receipt = context.store.read("baseline", request["id"])
    if inspect["Id"] != new_receipt["image"]["id"]:
        context.docker.remove_image(inspect["Id"], context.evidence_directory / "90-old-remove")
    payload = dict(rebuilt.payload or {})
    payload["rebuild_fingerprint_equal"] = True
    return HandlerResult(
        resource=rebuilt.resource,
        payload=payload,
        effect_certainty=EffectCertainty.COMMITTED,
        evidence_completeness=rebuilt.evidence_completeness,
    )


def destroy_baseline(context: OperationContext, request: dict[str, Any]) -> HandlerResult:
    require_keys(request, required=frozenset({"id"}))
    receipt = context.store.read("baseline", request["id"])
    references = context.store.references_to("baseline", request["id"])
    if references:
        rendered = ", ".join(f"{item['kind']} {item['id']}" for item in references)
        raise ValueError(f"cannot destroy referenced baseline {request['id']}: {rendered}")
    inspect = context.docker.image_inspect(receipt["image"]["tag"], context.evidence_directory / "00-inspect")
    context.docker.verify_resource_labels(inspect, "baseline", request["id"])
    context.docker.remove_image(inspect["Id"], context.evidence_directory / "01-remove")
    context.store.delete("baseline", request["id"])
    return HandlerResult(
        resource={"kind": "baseline", "id": request["id"], "docker_id": inspect["Id"]},
        payload={"destroyed": True},
        effect_certainty=EffectCertainty.COMMITTED,
    )


def _attempt_resources(receipt: Mapping[str, Any]) -> tuple[str | None, str | None]:
    cleanup = receipt.get("cleanup") or {}
    if cleanup.get("complete") and not cleanup.get("survivors"):
        return None, None
    container = receipt.get("container") or {}
    image = receipt.get("image") or {}
    return (
        container.get("id") or receipt.get("retained_container_id"),
        image.get("id") or image.get("tag"),
    )


def _is_imported_attempt(receipt: Mapping[str, Any], *inspections: Mapping[str, Any]) -> bool:
    if receipt.get("imported") is True:
        return True
    recorded_labels = (receipt.get("image") or {}).get("labels") or {}
    if recorded_labels.get(IMPORTED_LABEL) == "true":
        return True
    return any(
        (inspection.get("Config", {}).get("Labels") or {}).get(IMPORTED_LABEL) == "true"
        for inspection in inspections
    )


def inspect_attempt(context: OperationContext, request: dict[str, Any]) -> HandlerResult:
    require_keys(request, required=frozenset({"id"}))
    attempt_id = request["id"]
    receipt = context.store.read("attempt", attempt_id)
    container_ref, image_ref = _attempt_resources(receipt)
    docker: dict[str, Any] = {}
    if container_ref:
        container = context.docker.container_inspect(
            container_ref, context.evidence_directory / "00-container"
        )
        context.docker.verify_resource_labels(container, "attempt", attempt_id)
        docker["container"] = container
    if image_ref:
        image = context.docker.image_inspect(image_ref, context.evidence_directory / "01-image")
        context.docker.verify_resource_labels(image, "attempt", attempt_id)
        docker["image"] = image
    return HandlerResult(
        resource={"kind": "attempt", "id": attempt_id},
        payload={
            "receipt": receipt,
            "docker": docker,
            "imported": _is_imported_attempt(receipt, *docker.values()),
        },
    )


def destroy_attempt(context: OperationContext, request: dict[str, Any]) -> HandlerResult:
    require_keys(
        request,
        required=frozenset({"id"}),
        optional=frozenset({"confirm_imported"}),
    )
    attempt_id = request["id"]
    receipt = context.store.read("attempt", attempt_id)
    references = context.store.references_to("attempt", attempt_id)
    if references:
        rendered = ", ".join(f"{item['kind']} {item['id']}" for item in references)
        raise ValueError(f"cannot destroy referenced attempt {attempt_id}: {rendered}")

    container_ref, image_ref = _attempt_resources(receipt)
    container = None
    image = None
    if container_ref:
        container = context.docker.container_inspect(
            container_ref, context.evidence_directory / "00-container"
        )
        context.docker.verify_resource_labels(container, "attempt", attempt_id)
    if image_ref:
        image = context.docker.image_inspect(image_ref, context.evidence_directory / "01-image")
        context.docker.verify_resource_labels(image, "attempt", attempt_id)

    inspections = [value for value in (container, image) if value is not None]
    if _is_imported_attempt(receipt, *inspections) and request.get("confirm_imported") is not True:
        raise ValueError("imported attempt deletion requires confirm_imported=true")

    removed: list[dict[str, str]] = []
    if container is not None:
        try:
            context.docker.remove_container(
                container["Id"], context.evidence_directory / "02-remove-container"
            )
        except DockerError as exc:
            raise OperationFailure(
                str(exc),
                effect_certainty=EffectCertainty.UNKNOWN,
                resource={"kind": "attempt", "id": attempt_id},
                payload={
                    "destroyed": False,
                    "survivors": [
                        *([{"kind": "container", "id": container["Id"]}] if container else []),
                        *([{"kind": "image", "id": image["Id"]}] if image else []),
                    ],
                },
                error_type=type(exc).__name__,
            ) from exc
        removed.append({"kind": "container", "id": container["Id"]})
        receipt.pop("container", None)
        receipt.pop("retained_container_id", None)
        receipt["cleanup"] = {
            "complete": False,
            "survivors": ([{"kind": "image", "id": image["Id"]}] if image else []),
            "errors": [],
        }
        context.store.write("attempt", attempt_id, receipt)

    if image is not None:
        try:
            context.docker.remove_image(image["Id"], context.evidence_directory / "03-remove-image")
        except DockerError as exc:
            survivor = {"kind": "image", "id": image["Id"]}
            receipt["cleanup"] = {
                "complete": False,
                "survivors": [survivor],
                "errors": [{"type": type(exc).__name__, "message": str(exc)}],
            }
            context.store.write("attempt", attempt_id, receipt)
            raise OperationFailure(
                str(exc),
                effect_certainty=(
                    EffectCertainty.PARTIAL if removed else EffectCertainty.UNKNOWN
                ),
                resource={"kind": "attempt", "id": attempt_id},
                payload={"destroyed": False, "removed": removed, "survivors": [survivor]},
                error_type=type(exc).__name__,
            ) from exc
        removed.append({"kind": "image", "id": image["Id"]})

    context.store.delete("attempt", attempt_id)
    return HandlerResult(
        resource={"kind": "attempt", "id": attempt_id},
        payload={"destroyed": True, "removed": removed},
        effect_certainty=EffectCertainty.COMMITTED,
    )


def register_baseline_handlers(application: Application) -> None:
    application.register("baseline.prepare", prepare_baseline, mutating=True)
    application.register("baseline.inspect", inspect_baseline)
    application.register("baseline.verify", verify_baseline)
    application.register("baseline.rebuild", rebuild_baseline, mutating=True)
    application.register("baseline.destroy", destroy_baseline, mutating=True)
    application.register("attempt.inspect", inspect_attempt)
    application.register("attempt.destroy", destroy_attempt, mutating=True)
