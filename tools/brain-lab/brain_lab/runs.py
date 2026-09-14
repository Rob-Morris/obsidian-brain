from __future__ import annotations

import copy
import json
import shutil
import tempfile
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

from .application import Application, HandlerResult, OperationContext, OperationFailure
from .container_contract import CONTAINER_PYTHON
from .docker import DockerClient, DockerError
from .model import (
    EffectCertainty,
    EvidenceCompleteness,
    ExecRequest,
    Outcome,
    RunSpec,
    require_keys,
)
from .run_state import (
    RunManifestHelper,
    RunManifestCaptureError,
    capture_run_manifest,
    filesystem_diff,
    load_run_manifest_helper,
)


def _run_source(context: OperationContext, request: dict[str, Any]) -> tuple[str, str, dict[str, Any], bool]:
    has_baseline = "baseline_id" in request
    has_base = "base_id" in request
    if has_baseline == has_base:
        raise ValueError("run start requires exactly one of baseline_id or base_id")
    kind = "baseline" if has_baseline else "base"
    resource_id = request[f"{kind}_id"]
    receipt = context.store.read(kind, resource_id)
    imported = bool(
        kind == "baseline"
        and context.store.read("seed", receipt["recipe"]["seed_id"])["spec"].get("imported")
    )
    return kind, resource_id, receipt, imported


def start_run(context: OperationContext, request: dict[str, Any]) -> HandlerResult:
    require_keys(
        request,
        optional=frozenset({"baseline_id", "base_id", "network"}),
    )
    source_kind, source_id, source, imported = _run_source(context, request)
    spec = RunSpec(
        image=source["image"]["id"],
        platform=source["image"]["platform"],
        network=request.get("network", "none"),
    )
    run_id = f"run-{uuid.uuid4().hex[:20]}"
    labels = DockerClient.labels("run", run_id, imported=imported)
    name = DockerClient.deterministic_container_name("run", run_id)
    container_id, _ = context.docker.start_container(
        spec.image,
        name=name,
        platform=spec.platform,
        network=spec.network,
        labels=labels,
        evidence_directory=context.evidence_directory / "start",
    )
    inspect = context.docker.container_inspect(container_id, context.evidence_directory / "inspect")
    receipt = {
        "source": {"kind": source_kind, "id": source_id, "image_id": spec.image},
        "spec": {"image": spec.image, "platform": spec.platform, "network": spec.network},
        "container": {"id": inspect["Id"], "name": name, "labels": labels},
        "generation": 1,
        "imported": imported,
        "status": "running",
    }
    context.store.write("run", run_id, receipt)
    return HandlerResult(
        resource={"kind": "run", "id": run_id, "docker_id": inspect["Id"]},
        payload={
            "receipt": receipt,
            "direct_docker": {
                "exec": ["docker", "exec", "-it", inspect["Id"], "/bin/bash"],
                "inspect": ["docker", "inspect", inspect["Id"]],
                "copy_out": ["docker", "cp", f"{inspect['Id']}:/path", "/host/path"],
            },
        },
        effect_certainty=EffectCertainty.COMMITTED,
    )


def inspect_run(context: OperationContext, request: dict[str, Any]) -> HandlerResult:
    require_keys(request, required=frozenset({"id"}))
    receipt = context.store.read("run", request["id"])
    inspect = context.docker.container_inspect(receipt["container"]["id"], context.evidence_directory / "inspect")
    context.docker.verify_resource_labels(inspect, "run", request["id"])
    return HandlerResult(
        resource={"kind": "run", "id": request["id"], "docker_id": inspect["Id"]},
        payload={"receipt": receipt, "docker": inspect},
    )


def stop_run(context: OperationContext, request: dict[str, Any]) -> HandlerResult:
    require_keys(request, required=frozenset({"id"}))
    receipt = context.store.read("run", request["id"])
    inspect = context.docker.container_inspect(receipt["container"]["id"], context.evidence_directory / "00-inspect")
    context.docker.verify_resource_labels(inspect, "run", request["id"])
    context.docker.stop(inspect["Id"], context.evidence_directory / "01-stop")
    receipt["status"] = "stopped"
    context.store.write("run", request["id"], receipt)
    return HandlerResult(
        resource={"kind": "run", "id": request["id"], "docker_id": inspect["Id"]},
        payload={"stopped": True},
        effect_certainty=EffectCertainty.COMMITTED,
    )


def _parse_exec_request(request: dict[str, Any]) -> tuple[str, ExecRequest]:
    require_keys(
        request,
        required=frozenset({"id", "argv"}),
        optional=frozenset({"working_directory", "environment", "stdin", "timeout_seconds"}),
    )
    argv = request["argv"]
    if not isinstance(argv, list):
        raise ValueError("exec argv must be an array")
    environment = request.get("environment", {})
    if not isinstance(environment, dict):
        raise ValueError("exec environment must be an object")
    return request["id"], ExecRequest(
        argv=tuple(argv),
        working_directory=request.get("working_directory"),
        environment=environment,
        stdin=request.get("stdin"),
        timeout_seconds=float(request.get("timeout_seconds", 300)),
    )


def _raise_run_docker_failure(exc: DockerError, run_id: str, docker_id: str, action: str) -> None:
    execution = exc.execution
    raise OperationFailure(
        f"run {action} failed; its effects are unknown and the run was retained",
        outcome=(
            Outcome.TIMEOUT
            if execution is not None and execution.timed_out
            else Outcome.CANCELLED
            if execution is not None and execution.cancelled
            else Outcome.FAILURE
        ),
        effect_certainty=EffectCertainty.UNKNOWN,
        evidence_completeness=(
            EvidenceCompleteness.COMPLETE
            if execution is None or execution.evidence_complete
            else EvidenceCompleteness.PARTIAL
        ),
        resource={"kind": "run", "id": run_id, "docker_id": docker_id},
        payload={
            "execution": execution.to_dict() if execution is not None else None,
            "run_retained": True,
        },
        error_type=type(exc).__name__,
    ) from exc


def _capture_changed_content(
    context: OperationContext,
    container: str,
    diff: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None, bool]:
    paths = [entry["path"] for entry in diff["added"]]
    paths.extend(entry["path"] for entry in diff["changed"])
    if not paths:
        return {"retained": False, "reason": "no-added-or-changed-files"}, None, True
    try:
        execution = context.docker.exec(
            container,
            [
                CONTAINER_PYTHON,
                "/usr/local/lib/brain-lab/changed_content.py",
                "--root",
                "/home/brain",
            ],
            stdin=json.dumps(paths, separators=(",", ":")).encode("utf-8"),
            evidence_directory=context.evidence_directory / "04-changed-content",
            timeout_seconds=600,
        )
    except DockerError as exc:
        return None, f"{type(exc).__name__}: {exc}", False
    if not execution.succeeded or execution.stdout.truncated:
        return None, "changed-content command failed or exceeded its retention bound", False
    return execution.to_dict(), None, execution.evidence_complete


def _manifest_evidence(
    context: OperationContext,
    container: str,
    evidence_name: str,
    helper: RunManifestHelper,
) -> tuple[dict[str, Any] | None, str | None, bool]:
    try:
        capture = capture_run_manifest(context, container, evidence_name, helper)
    except RunManifestCaptureError as exc:
        return None, f"{type(exc).__name__}: {exc}", exc.evidence_complete
    return capture.manifest, None, capture.evidence_complete


def exec_run(context: OperationContext, request: dict[str, Any]) -> HandlerResult:
    run_id, command = _parse_exec_request(request)
    receipt = context.store.read("run", run_id)
    inspect = context.docker.container_inspect(receipt["container"]["id"], context.evidence_directory / "00-inspect")
    context.docker.verify_resource_labels(inspect, "run", run_id)
    try:
        manifest_helper = load_run_manifest_helper(context)
    except RunManifestCaptureError as exc:
        manifest_helper = None
        before, before_error, before_complete = None, f"{type(exc).__name__}: {exc}", False
    else:
        before, before_error, before_complete = _manifest_evidence(
            context, inspect["Id"], "01-before-manifest", manifest_helper
        )
    execution = context.docker.exec(
        inspect["Id"],
        command.argv,
        working_directory=command.working_directory,
        environment=command.environment,
        stdin=None if command.stdin is None else command.stdin.encode("utf-8"),
        timeout_seconds=command.timeout_seconds,
        evidence_directory=context.evidence_directory / "02-exec",
    )
    if manifest_helper is None:
        after, after_error, after_complete = None, before_error, False
    else:
        after, after_error, after_complete = _manifest_evidence(
            context, inspect["Id"], "03-after-manifest", manifest_helper
        )
    evidence_complete = execution.evidence_complete and before_complete and after_complete
    if before is None or after is None:
        filesystem_change = {
            "status": "unavailable",
            "before_error": before_error,
            "after_error": after_error,
        }
    else:
        diff = filesystem_diff(before, after)
        diff["manifest_helper_sha256"] = manifest_helper.sha256
        content, content_error, content_complete = _capture_changed_content(
            context, inspect["Id"], diff
        )
        evidence_complete = evidence_complete and content_complete
        diff["changed_content"] = content
        diff["changed_content_error"] = content_error
        (context.evidence_directory / "filesystem-diff.json").write_text(
            json.dumps(diff, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        filesystem_change = {
            "status": "complete" if evidence_complete else "partial",
            "equal": diff["equal"],
            "summary": diff["summary"],
            "before_tree_sha256": diff["before_tree_sha256"],
            "after_tree_sha256": diff["after_tree_sha256"],
            "evidence": "filesystem-diff.json",
        }
    payload = {
        "execution": execution.to_dict(),
        "filesystem_change": filesystem_change,
        "run_retained": True,
    }
    if execution.timed_out:
        raise OperationFailure(
            "run command timed out; its effects are unknown and the run was retained",
            outcome=Outcome.TIMEOUT,
            effect_certainty=EffectCertainty.UNKNOWN,
            evidence_completeness=(
                EvidenceCompleteness.COMPLETE
                if evidence_complete
                else EvidenceCompleteness.PARTIAL
            ),
            resource={"kind": "run", "id": run_id, "docker_id": inspect["Id"]},
            payload=payload,
            error_type="DockerError",
        )
    if execution.cancelled:
        raise OperationFailure(
            "run command was cancelled; its effects are unknown and the run was retained",
            outcome=Outcome.CANCELLED,
            effect_certainty=EffectCertainty.UNKNOWN,
            evidence_completeness=(
                EvidenceCompleteness.COMPLETE
                if evidence_complete
                else EvidenceCompleteness.PARTIAL
            ),
            resource={"kind": "run", "id": run_id, "docker_id": inspect["Id"]},
            payload=payload,
            error_type="DockerError",
        )
    if not execution.succeeded:
        raise OperationFailure(
            f"run command failed with exit code {execution.returncode}; its effects are unknown and the run was retained",
            effect_certainty=EffectCertainty.UNKNOWN,
            evidence_completeness=(
                EvidenceCompleteness.COMPLETE
                if evidence_complete
                else EvidenceCompleteness.PARTIAL
            ),
            resource={"kind": "run", "id": run_id, "docker_id": inspect["Id"]},
            payload=payload,
        )
    payload.pop("run_retained")
    return HandlerResult(
        resource={"kind": "run", "id": run_id, "docker_id": inspect["Id"]},
        payload=payload,
        effect_certainty=EffectCertainty.COMMITTED,
        evidence_completeness=(
            EvidenceCompleteness.COMPLETE if evidence_complete else EvidenceCompleteness.PARTIAL
        ),
    )


def shell_run(context: OperationContext, request: dict[str, Any]) -> HandlerResult:
    require_keys(request, required=frozenset({"id"}), optional=frozenset({"shell"}))
    receipt = context.store.read("run", request["id"])
    inspect = context.docker.container_inspect(receipt["container"]["id"], context.evidence_directory / "inspect")
    context.docker.verify_resource_labels(inspect, "run", request["id"])
    returncode = context.docker.shell(inspect["Id"], evidence_directory=context.evidence_directory / "shell", shell=request.get("shell", "/bin/bash"))
    if returncode != 0:
        raise OperationFailure(
            f"interactive shell exited with status {returncode}",
            effect_certainty=EffectCertainty.UNKNOWN,
            evidence_completeness=EvidenceCompleteness.PARTIAL,
        )
    return HandlerResult(
        resource={"kind": "run", "id": request["id"], "docker_id": inspect["Id"]},
        payload={"returncode": returncode},
        evidence_completeness=EvidenceCompleteness.PARTIAL,
        effect_certainty=EffectCertainty.UNKNOWN,
    )


def _copy_destination(value: Any) -> PurePosixPath:
    if not isinstance(value, str):
        raise ValueError("copy-in destination must be a string")
    destination = PurePosixPath(value)
    if (
        not destination.is_absolute()
        or destination == PurePosixPath("/home/brain")
        or destination.parts[:3] != ("/", "home", "brain")
        or ".." in destination.parts
    ):
        raise ValueError("copy-in destination must be a child of /home/brain")
    return destination


def _require_available_copy_destination(
    context: OperationContext, container: str, destination: PurePosixPath
) -> None:
    probe = (
        "import os,sys; "
        "root=os.path.realpath('/home/brain'); "
        "parent=os.path.realpath(os.path.dirname(sys.argv[1])); "
        "valid=(os.path.commonpath((root,parent))==root "
        "and os.path.isdir(parent) and not os.path.lexists(sys.argv[1])); "
        "raise SystemExit(0 if valid else 1)"
    )
    execution = context.docker.exec(
        container,
        [CONTAINER_PYTHON, "-c", probe, str(destination)],
        evidence_directory=context.evidence_directory / "01-destination-check",
        timeout_seconds=60,
    )
    if not execution.succeeded:
        raise ValueError(
            "copy-in destination parent must exist and the destination itself must not exist"
        )


def _repair_copy_ownership(
    context: OperationContext, container: str, destination: PurePosixPath, evidence_name: str
) -> None:
    ownership = context.docker.exec(
        container,
        ["chown", "-hR", "10001:10001", str(destination)],
        user="root",
        evidence_directory=context.evidence_directory / evidence_name,
        timeout_seconds=600,
    )
    if not ownership.succeeded:
        raise DockerError("Docker copy-in ownership repair failed", ownership)


def copy_in(context: OperationContext, request: dict[str, Any]) -> HandlerResult:
    require_keys(
        request,
        required=frozenset({"id", "destination"}),
        optional=frozenset({"source", "source_id"}),
    )
    if ("source" in request) == ("source_id" in request):
        raise ValueError("copy-in requires exactly one of source or source_id")
    receipt = context.store.read("run", request["id"])
    inspect = context.docker.container_inspect(receipt["container"]["id"], context.evidence_directory / "00-inspect")
    context.docker.verify_resource_labels(inspect, "run", request["id"])
    destination = _copy_destination(request["destination"])
    _require_available_copy_destination(context, inspect["Id"], destination)
    helper_container = None
    main_failure = None
    cleanup_failure = None
    try:
        if "source" in request:
            source = Path(request["source"]).expanduser().resolve()
            if not source.exists():
                raise ValueError(f"copy-in source does not exist: {source}")
            execution = context.docker.copy_in(
                inspect["Id"],
                source,
                str(destination),
                context.evidence_directory / "02-copy",
            )
            _repair_copy_ownership(context, inspect["Id"], destination, "03-ownership")
        else:
            source_resource = context.store.read("source", request["source_id"])
            if source_resource["image"]["platform"] != receipt["spec"]["platform"]:
                raise ValueError("copy-in source resource and run must use the same OCI platform")
            source_image = context.docker.image_inspect(
                source_resource["image"]["tag"],
                context.evidence_directory / "02-source-inspect",
            )
            context.docker.verify_resource_labels(source_image, "source", request["source_id"])
            helper_id = f"attempt-{uuid.uuid4().hex[:20]}"
            helper_container, _ = context.docker.create_container(
                source_image["Id"],
                name=DockerClient.deterministic_container_name("source-copy", helper_id),
                platform=source_resource["image"]["platform"],
                labels=DockerClient.labels("attempt", helper_id),
                evidence_directory=context.evidence_directory / "03-source-container",
            )
            with tempfile.TemporaryDirectory(prefix="brain-lab-source-copy-") as temporary:
                staged = Path(temporary) / "source"
                context.docker.copy_out(
                    helper_container,
                    "/bundle/source",
                    staged,
                    context.evidence_directory / "04-source-copy-out",
                )
                execution = context.docker.copy_in(
                    inspect["Id"],
                    staged,
                    str(destination),
                    context.evidence_directory / "05-copy",
                )
                _repair_copy_ownership(context, inspect["Id"], destination, "06-ownership")
    except DockerError as exc:
        main_failure = exc
    finally:
        if helper_container is not None:
            try:
                context.docker.remove_container(
                    helper_container,
                    context.evidence_directory / "90-remove-source-container",
                )
            except DockerError as exc:
                cleanup_failure = exc
    if main_failure is not None:
        _raise_run_docker_failure(main_failure, request["id"], inspect["Id"], "copy-in")
    if cleanup_failure is not None:
        raise OperationFailure(
            "copy-in succeeded but its temporary source container could not be removed",
            effect_certainty=EffectCertainty.PARTIAL,
            resource={"kind": "run", "id": request["id"], "docker_id": inspect["Id"]},
            payload={
                "execution": execution.to_dict(),
                "destination": request["destination"],
                "source_id": request.get("source_id"),
                "survivor_container_id": helper_container,
            },
            error_type=type(cleanup_failure).__name__,
        )
    return HandlerResult(
        resource={"kind": "run", "id": request["id"], "docker_id": inspect["Id"]},
        payload={
            "execution": execution.to_dict(),
            "destination": request["destination"],
            "source_id": request.get("source_id"),
        },
        effect_certainty=EffectCertainty.COMMITTED,
    )


def copy_out(context: OperationContext, request: dict[str, Any]) -> HandlerResult:
    require_keys(request, required=frozenset({"id", "source", "destination"}))
    destination = Path(request["destination"]).expanduser().resolve()
    if destination.exists():
        raise ValueError(f"copy-out destination already exists: {destination}")
    if not destination.parent.is_dir():
        raise ValueError(f"copy-out destination parent does not exist: {destination.parent}")
    receipt = context.store.read("run", request["id"])
    inspect = context.docker.container_inspect(receipt["container"]["id"], context.evidence_directory / "00-inspect")
    context.docker.verify_resource_labels(inspect, "run", request["id"])
    try:
        execution = context.docker.copy_out(
            inspect["Id"], request["source"], destination, context.evidence_directory / "01-copy"
        )
    except DockerError as exc:
        _raise_run_docker_failure(exc, request["id"], inspect["Id"], "copy-out")
    return HandlerResult(
        resource={"kind": "run", "id": request["id"], "docker_id": inspect["Id"]},
        payload={"execution": execution.to_dict(), "destination": str(destination)},
        effect_certainty=EffectCertainty.COMMITTED,
    )


def _docker_container_name(inspect: dict[str, Any]) -> str | None:
    name = inspect.get("Name")
    return name.removeprefix("/") if isinstance(name, str) else None


def _rollback_recreation(
    context: OperationContext,
    *,
    run_id: str,
    old_id: str,
    original_name: str,
    old_was_running: bool,
    replacement_start_attempted: bool,
    replacement_id: str | None,
) -> list[str]:
    errors: list[str] = []
    if replacement_start_attempted:
        replacement_reference = replacement_id or original_name
        try:
            replacement = context.docker.container_inspect(
                replacement_reference,
                context.evidence_directory / "90-inspect-replacement",
            )
        except DockerError as exc:
            if replacement_id is not None:
                errors.append(f"replacement reconciliation failed: {exc}")
        else:
            if replacement["Id"] != old_id:
                try:
                    context.docker.verify_resource_labels(replacement, "run", run_id)
                    context.docker.remove_container(
                        replacement["Id"],
                        context.evidence_directory / "91-remove-replacement",
                    )
                except DockerError as exc:
                    errors.append(f"replacement removal failed: {exc}")

    try:
        current_old = context.docker.container_inspect(
            old_id, context.evidence_directory / "92-inspect-previous"
        )
        context.docker.verify_resource_labels(current_old, "run", run_id)
    except DockerError as exc:
        errors.append(f"previous container reconciliation failed: {exc}")
        return errors

    if _docker_container_name(current_old) != original_name:
        try:
            context.docker.rename_container(
                old_id, original_name, context.evidence_directory / "93-restore-name"
            )
        except DockerError as exc:
            errors.append(f"previous container name restoration failed: {exc}")

    running = bool(current_old.get("State", {}).get("Running"))
    if old_was_running and not running:
        try:
            context.docker.start_existing(
                old_id, context.evidence_directory / "94-restart-previous"
            )
        except DockerError as exc:
            errors.append(f"previous container restart failed: {exc}")

    try:
        restored = context.docker.container_inspect(
            old_id, context.evidence_directory / "95-verify-previous"
        )
        context.docker.verify_resource_labels(restored, "run", run_id)
        if _docker_container_name(restored) != original_name:
            errors.append("previous container does not have its recorded name after rollback")
        if bool(restored.get("State", {}).get("Running")) != old_was_running:
            errors.append("previous container does not have its recorded running state after rollback")
    except DockerError as exc:
        errors.append(f"previous container rollback verification failed: {exc}")
    return errors


def recreate_run(context: OperationContext, request: dict[str, Any]) -> HandlerResult:
    require_keys(request, required=frozenset({"id"}))
    run_id = request["id"]
    receipt = context.store.read("run", run_id)
    old = context.docker.container_inspect(receipt["container"]["id"], context.evidence_directory / "00-inspect")
    context.docker.verify_resource_labels(old, "run", run_id)
    labels = receipt["container"]["labels"]
    name = receipt["container"]["name"]
    previous_name = f"{name}-previous-{uuid.uuid4().hex[:8]}"
    replacement_id = None
    replacement_start_attempted = False
    old_was_running = bool(
        old.get("State", {}).get("Running", receipt.get("status") == "running")
    )
    try:
        if old_was_running:
            context.docker.stop(old["Id"], context.evidence_directory / "01-stop-previous")
        context.docker.rename_container(
            old["Id"], previous_name, context.evidence_directory / "02-rename-previous"
        )
        replacement_start_attempted = True
        replacement_id, _ = context.docker.start_container(
            receipt["spec"]["image"],
            name=name,
            platform=receipt["spec"]["platform"],
            network=receipt["spec"]["network"],
            labels=labels,
            evidence_directory=context.evidence_directory / "03-start-replacement",
        )
        inspect = context.docker.container_inspect(
            replacement_id, context.evidence_directory / "04-inspect-replacement"
        )
        context.docker.verify_resource_labels(inspect, "run", run_id)
        replacement_receipt = copy.deepcopy(receipt)
        replacement_receipt["container"]["id"] = inspect["Id"]
        replacement_receipt["generation"] += 1
        replacement_receipt["status"] = "running"
        context.store.write("run", run_id, replacement_receipt)
    except (DockerError, OSError) as primary:
        rollback_errors = _rollback_recreation(
            context,
            run_id=run_id,
            old_id=old["Id"],
            original_name=name,
            old_was_running=old_was_running,
            replacement_start_attempted=replacement_start_attempted,
            replacement_id=replacement_id,
        )
        raise OperationFailure(
            (
                "run recreation failed and the previous container could not be fully restored"
                if rollback_errors
                else "run recreation failed; the previous container was restored"
            ),
            effect_certainty=(
                EffectCertainty.UNKNOWN if rollback_errors else EffectCertainty.NONE
            ),
            evidence_completeness=EvidenceCompleteness.COMPLETE,
            resource={"kind": "run", "id": run_id, "docker_id": old["Id"]},
            payload={
                "previous_docker_id": old["Id"],
                "replacement_docker_id": replacement_id,
                "rollback_errors": rollback_errors,
            },
            error_type=type(primary).__name__,
        ) from primary
    cleanup = {"complete": True, "survivors": [], "errors": []}
    try:
        context.docker.remove_container(
            old["Id"], context.evidence_directory / "05-remove-previous"
        )
    except DockerError as exc:
        cleanup = {
            "complete": False,
            "survivors": [{"kind": "container", "id": old["Id"]}],
            "errors": [{"type": type(exc).__name__, "message": str(exc)}],
        }
    return HandlerResult(
        resource={"kind": "run", "id": run_id, "docker_id": inspect["Id"]},
        payload={
            "recreated": True,
            "generation": replacement_receipt["generation"],
            "previous_docker_id": old["Id"],
            "cleanup": cleanup,
        },
        effect_certainty=(
            EffectCertainty.COMMITTED if cleanup["complete"] else EffectCertainty.PARTIAL
        ),
    )


def discard_run(context: OperationContext, request: dict[str, Any]) -> HandlerResult:
    require_keys(request, required=frozenset({"id"}))
    receipt = context.store.read("run", request["id"])
    inspect = context.docker.container_inspect(receipt["container"]["id"], context.evidence_directory / "00-inspect")
    context.docker.verify_resource_labels(inspect, "run", request["id"])
    context.docker.remove_container(inspect["Id"], context.evidence_directory / "01-remove")
    context.store.delete("run", request["id"])
    return HandlerResult(
        resource={"kind": "run", "id": request["id"], "docker_id": inspect["Id"]},
        payload={"discarded": True},
        effect_certainty=EffectCertainty.COMMITTED,
    )



def register_run_handlers(application: Application) -> None:
    application.register("run.start", start_run, mutating=True)
    application.register("run.inspect", inspect_run)
    application.register("run.stop", stop_run, mutating=True)
    application.register("run.exec", exec_run, mutating=True)
    application.register("run.shell", shell_run, mutating=True)
    application.register("run.copy-in", copy_in, mutating=True)
    application.register("run.copy-out", copy_out, mutating=True)
    application.register("run.recreate", recreate_run, mutating=True)
    application.register("run.discard", discard_run, mutating=True)
