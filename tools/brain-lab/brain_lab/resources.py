from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .application import Application, HandlerResult, OperationContext, OperationFailure
from .capture import RemoteCheckout, capture_worktree, resolve_remote_ref
from .docker import DockerClient, DockerError
from .manifests import (
    TreeManifest,
    manifest_from_json,
    manifest_tree,
    normalised_core_manifest,
    normalised_core_tree,
    select_manifest,
)
from .model import (
    BaseSpec,
    EffectCertainty,
    EvidenceCompleteness,
    SourceSpec,
    VaultSeedSpec,
    require_keys,
    validate_linux_platform,
)


def _image_payload(inspect: dict[str, Any], tag: str, platform: str, labels: dict[str, str]) -> dict[str, Any]:
    return {
        "id": inspect["Id"],
        "tag": tag,
        "platform": platform,
        "architecture": inspect.get("Architecture"),
        "variant": inspect.get("Variant"),
        "os": inspect.get("Os"),
        "size": inspect.get("Size"),
        "labels": labels,
    }


def _rollback_changed_capture(
    context: OperationContext,
    *,
    resource_kind: str,
    resource_id: str,
    tag: str,
    reason: str,
) -> None:
    try:
        context.docker.remove_image(tag, context.evidence_directory / "rollback-image")
    except DockerError as cleanup_error:
        raise OperationFailure(
            f"{reason}; rollback failed: {cleanup_error}",
            effect_certainty=EffectCertainty.PARTIAL,
            resource={"kind": resource_kind, "id": resource_id},
            payload={
                "rollback_complete": False,
                "surviving_image_tag": tag,
                "cleanup_error": str(cleanup_error),
            },
            error_type="CaptureChanged",
        ) from cleanup_error
    raise OperationFailure(
        reason,
        effect_certainty=EffectCertainty.NONE,
        resource={"kind": resource_kind, "id": resource_id},
        payload={"rollback_complete": True, "removed_image_tag": tag},
        error_type="CaptureChanged",
    )


def _read_version(path: Path) -> str:
    try:
        version = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise ValueError(f"Brain VERSION is missing: {path}") from exc
    if not version:
        raise ValueError(f"Brain VERSION is empty: {path}")
    return version


def _read_cli_version(root: Path) -> str:
    launcher = root / "cli" / "brain"
    try:
        content = launcher.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ValueError(f"Brain CLI launcher is missing: {launcher}") from exc
    matched = re.search(r'^BRAIN_CLI_VERSION="([0-9]+\.[0-9]+\.[0-9]+)"$', content, re.MULTILINE)
    if matched is None:
        raise ValueError(f"Brain CLI version is not declared canonically: {launcher}")
    return matched.group(1)


def _ensure_capture_space(root: Path, total_bytes: int) -> dict[str, int]:
    free = shutil.disk_usage(root).free
    fixed_headroom = 512 * 1024 * 1024
    required = max(total_bytes * 2 + fixed_headroom, 1024 * 1024 * 1024)
    if free < required:
        raise ValueError(f"capture requires {required} free bytes but only {free} are available")
    return {
        "source_bytes": total_bytes,
        "free_bytes": free,
        "required_free_bytes": required,
        "fixed_headroom_bytes": fixed_headroom,
        "copy_multiplier": 2,
    }


def build_base(context: OperationContext, request: dict[str, Any]) -> HandlerResult:
    require_keys(
        request,
        optional=frozenset({"image", "platform", "timeout_seconds"}),
    )
    image = request.get("image", "ubuntu:24.04")
    platform = validate_linux_platform(request.get("platform", "linux/arm64"))
    timeout = float(request.get("timeout_seconds", 1800))
    if not isinstance(image, str):
        raise ValueError("base image must be a string")
    context.docker.verify_available(context.evidence_directory / "00-docker")
    context.docker.pull(image, platform, context.evidence_directory / "01-pull", timeout_seconds=timeout)
    source_inspect = context.docker.image_inspect(image, context.evidence_directory / "02-source-inspect")
    repo_digests = source_inspect.get("RepoDigests") or []
    resolved_digest = repo_digests[0] if repo_digests else source_inspect["Id"]
    dockerfile = (context.tool_root / "Dockerfile").read_text(encoding="utf-8")
    dockerfile_sha256 = hashlib.sha256(dockerfile.encode("utf-8")).hexdigest()
    copied_inputs = {}
    for path in sorted((context.tool_root / "container").iterdir()):
        if path.is_file():
            copied_inputs[f"container/{path.name}"] = hashlib.sha256(path.read_bytes()).hexdigest()
    build_inputs_sha256 = hashlib.sha256(
        json.dumps(
            {"Dockerfile": dockerfile_sha256, **copied_inputs},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    spec = BaseSpec(
        image=image,
        platform=platform,
        resolved_image_id=source_inspect["Id"],
        resolved_digest=resolved_digest,
        dockerfile_sha256=dockerfile_sha256,
        build_inputs_sha256=build_inputs_sha256,
    )
    resource_id = spec.identity()
    existing_path = context.store.receipt_path("base", resource_id)
    if existing_path.exists():
        existing = context.store.read("base", resource_id)
        inspect = context.docker.image_inspect(existing["image"]["tag"], context.evidence_directory / "03-reuse-inspect")
        context.docker.verify_resource_labels(inspect, "base", resource_id)
        if inspect["Id"] != existing["image"]["id"]:
            raise ValueError("base tag no longer identifies the recorded immutable image")
        return HandlerResult(
            resource={"kind": "base", "id": resource_id},
            payload={"reused": True, "image_id": existing["image"]["id"]},
        )
    labels = DockerClient.labels("base", resource_id)
    tag = DockerClient.deterministic_tag("base", resource_id)
    context.docker.build(
        dockerfile,
        tag=tag,
        platform=platform,
        labels=labels,
        build_arguments={"BASE_IMAGE": resolved_digest},
        evidence_directory=context.evidence_directory / "03-build",
        timeout_seconds=timeout,
        context_directory=context.tool_root,
    )
    inspect = context.docker.image_inspect(tag, context.evidence_directory / "04-inspect")
    receipt = {
        "spec": asdict(spec),
        "image": _image_payload(inspect, tag, platform, labels),
        "resolved_reference": resolved_digest,
        "package_inputs": {
            "path": "/usr/local/share/brain-lab/package-inputs.json",
            "content_addressed_by": inspect["Id"],
            "records": ["apt-repository-configuration", "installed-package-versions"],
        },
    }
    context.store.write("base", resource_id, receipt)
    return HandlerResult(
        resource={"kind": "base", "id": resource_id, "docker_id": inspect["Id"]},
        payload={"reused": False, "platform": platform, "resolved_reference": resolved_digest},
        effect_certainty=EffectCertainty.COMMITTED,
    )


def inspect_resource(kind: str):
    def inspect(context: OperationContext, request: dict[str, Any]) -> HandlerResult:
        require_keys(request, required=frozenset({"id"}))
        receipt = context.store.read(kind, request["id"])
        image = receipt.get("image")
        docker = None
        if image:
            docker = context.docker.image_inspect(image["tag"], context.evidence_directory / "inspect")
            context.docker.verify_resource_labels(docker, kind, request["id"])
        container = receipt.get("container")
        if container:
            docker = context.docker.container_inspect(container["id"], context.evidence_directory / "inspect")
            context.docker.verify_resource_labels(docker, kind, request["id"])
        return HandlerResult(
            resource={"kind": kind, "id": request["id"]},
            payload={"receipt": receipt, "docker": docker},
        )

    return inspect


def destroy_image_resource(kind: str):
    def destroy(context: OperationContext, request: dict[str, Any]) -> HandlerResult:
        optional = frozenset({"confirm_imported"}) if kind == "seed" else frozenset()
        require_keys(request, required=frozenset({"id"}), optional=optional)
        resource_id = request["id"]
        receipt = context.store.read(kind, resource_id)
        references = context.store.references_to(kind, resource_id)
        if references:
            rendered = ", ".join(f"{item['kind']} {item['id']}" for item in references)
            raise ValueError(f"cannot destroy referenced {kind} {resource_id}: {rendered}")
        if receipt.get("spec", {}).get("imported") and request.get("confirm_imported") is not True:
            raise ValueError("imported seed deletion requires confirm_imported=true")
        image = receipt["image"]
        inspect = context.docker.image_inspect(image["tag"], context.evidence_directory / "00-inspect")
        context.docker.verify_resource_labels(inspect, kind, resource_id)
        context.docker.remove_image(inspect["Id"], context.evidence_directory / "01-remove")
        context.store.delete(kind, resource_id)
        return HandlerResult(
            resource={"kind": kind, "id": resource_id, "docker_id": inspect["Id"]},
            payload={"destroyed": True},
            effect_certainty=EffectCertainty.COMMITTED,
        )

    return destroy


def resolve_source(context: OperationContext, request: dict[str, Any]) -> HandlerResult:
    require_keys(request, required=frozenset({"repository", "ref"}))
    commit = resolve_remote_ref(
        context.runner,
        request["repository"],
        request["ref"],
        evidence_directory=context.evidence_directory / "resolve",
    )
    return HandlerResult(payload={"repository": request["repository"], "ref": request["ref"], "commit": commit})


def _capture_source_root(
    context: OperationContext,
    root: Path,
    *,
    selector: dict[str, Any],
    commit: str | None,
    include_untracked: list[str],
    platform: str,
) -> HandlerResult:
    capture = capture_worktree(
        context.runner,
        root,
        include_untracked=include_untracked,
        evidence_directory=context.evidence_directory / "capture",
    )
    if commit is not None and capture.commit != commit:
        raise RuntimeError("remote checkout does not match the resolved commit")
    space_preflight = _ensure_capture_space(capture.root, capture.manifest.total_bytes)
    core_root = capture.root / "src" / "brain-core"
    core_version = _read_version(core_root / "VERSION")
    cli_version = _read_cli_version(capture.root)
    core_manifest = normalised_core_tree(core_root)
    spec = SourceSpec(
        tree_sha256=capture.manifest.tree_sha256,
        core_version=core_version,
        core_sha256=core_manifest.tree_sha256,
        cli_version=cli_version,
        commit=capture.commit,
        platform=platform,
        selector=selector,
    )
    resource_id = spec.identity()
    existing_path = context.store.receipt_path("source", resource_id)
    if existing_path.exists():
        receipt = context.store.read("source", resource_id)
        inspect = context.docker.image_inspect(receipt["image"]["tag"], context.evidence_directory / "reuse-inspect")
        context.docker.verify_resource_labels(inspect, "source", resource_id)
        if inspect["Id"] != receipt["image"]["id"]:
            raise ValueError("source tag no longer identifies the recorded immutable image")
        return HandlerResult(
            resource={"kind": "source", "id": resource_id},
            payload={
                "reused": True,
                "tree_sha256": receipt["spec"]["tree_sha256"],
                "core_version": receipt["spec"]["core_version"],
            },
        )
    labels = DockerClient.labels("source", resource_id)
    tag = DockerClient.deterministic_tag("source", resource_id)
    context.docker.import_bundle(
        capture.root,
        capture.manifest,
        destination_prefix="bundle/source",
        tag=tag,
        platform=platform,
        labels=labels,
        evidence_directory=context.evidence_directory / "import",
    )
    after = manifest_tree(capture.root, [entry.path for entry in capture.manifest.entries])
    if after.tree_sha256 != capture.manifest.tree_sha256:
        _rollback_changed_capture(
            context,
            resource_kind="source",
            resource_id=resource_id,
            tag=tag,
            reason="source changed during streamed import",
        )
    inspect = context.docker.image_inspect(tag, context.evidence_directory / "inspect")
    receipt = {
        "spec": asdict(spec),
        "manifest": capture.manifest.to_dict(),
        "core_manifest": core_manifest.to_dict(),
        "capture": {
            "tracked_paths": list(capture.tracked_paths),
            "deleted_tracked_paths": list(capture.deleted_tracked_paths),
            "included_untracked_paths": list(capture.included_untracked_paths),
            "excluded_untracked_paths": list(capture.excluded_untracked_paths),
            "space_preflight": space_preflight,
        },
        "image": _image_payload(inspect, tag, platform, labels),
    }
    context.store.write("source", resource_id, receipt)
    return HandlerResult(
        resource={"kind": "source", "id": resource_id, "docker_id": inspect["Id"]},
        payload={
            "reused": False,
            "tree_sha256": spec.tree_sha256,
            "core_version": spec.core_version,
            "cli_version": spec.cli_version,
            "tracked_path_count": len(capture.tracked_paths),
            "deleted_tracked_paths": list(capture.deleted_tracked_paths),
            "included_untracked_paths": list(capture.included_untracked_paths),
            "excluded_untracked_paths": list(capture.excluded_untracked_paths),
            "space_preflight": space_preflight,
        },
        effect_certainty=EffectCertainty.COMMITTED,
    )


def capture_source(context: OperationContext, request: dict[str, Any]) -> HandlerResult:
    require_keys(
        request,
        required=frozenset({"kind"}),
        optional=frozenset({"path", "repository", "ref", "include_untracked", "platform"}),
    )
    kind = request["kind"]
    platform = validate_linux_platform(request.get("platform", "linux/arm64"))
    included = request.get("include_untracked", [])
    if not isinstance(included, list) or not all(isinstance(item, str) for item in included):
        raise ValueError("include_untracked must be an array of paths")
    if kind == "worktree":
        if "path" not in request or "repository" in request or "ref" in request:
            raise ValueError("worktree source requires path and prohibits repository/ref")
        return _capture_source_root(
            context,
            Path(request["path"]),
            selector={"kind": "worktree"},
            commit=None,
            include_untracked=included,
            platform=platform,
        )
    if kind == "git":
        if "repository" not in request or "ref" not in request or "path" in request or included:
            raise ValueError("git source requires repository/ref and prohibits path/include_untracked")
        with RemoteCheckout(
            context.runner,
            request["repository"],
            request["ref"],
            context.evidence_directory / "remote",
        ) as (root, commit):
            return _capture_source_root(
                context,
                root,
                selector={"kind": "git", "repository": request["repository"], "ref": request["ref"]},
                commit=commit,
                include_untracked=[],
                platform=platform,
            )
    raise ValueError("source kind must be 'worktree' or 'git'")


def template_seed(context: OperationContext, request: dict[str, Any]) -> HandlerResult:
    require_keys(request, required=frozenset({"source_id"}))
    source = context.store.read("source", request["source_id"])
    source_manifest = manifest_from_json(source["manifest"])
    template_manifest = select_manifest(source_manifest, "template-vault")
    if not template_manifest.entries:
        raise ValueError("source bundle has no template-vault content")
    source_spec = source["spec"]
    spec = VaultSeedSpec(
        kind="template",
        tree_sha256=template_manifest.tree_sha256,
        core_version=source_spec["core_version"],
        core_sha256=source_spec["core_sha256"],
        platform=source["image"]["platform"],
        source_id=request["source_id"],
    )
    resource_id = spec.identity()
    if context.store.receipt_path("seed", resource_id).exists():
        receipt = context.store.read("seed", resource_id)
        inspect = context.docker.image_inspect(receipt["image"]["tag"], context.evidence_directory / "reuse-inspect")
        context.docker.verify_resource_labels(inspect, "seed", resource_id)
        return HandlerResult(
            resource={"kind": "seed", "id": resource_id},
            payload={"reused": True, "tree_sha256": receipt["spec"]["tree_sha256"]},
        )
    labels = DockerClient.labels("seed", resource_id)
    tag = DockerClient.deterministic_tag("seed", resource_id)
    dockerfile = """ARG SOURCE_IMAGE
FROM ${SOURCE_IMAGE} AS source
FROM scratch
COPY --from=source /bundle/source/template-vault/ /bundle/vault/
"""
    context.docker.build(
        dockerfile,
        tag=tag,
        platform=source["image"]["platform"],
        labels=labels,
        build_arguments={"SOURCE_IMAGE": source["image"]["tag"]},
        evidence_directory=context.evidence_directory / "build",
    )
    inspect = context.docker.image_inspect(tag, context.evidence_directory / "inspect")
    receipt = {
        "spec": asdict(spec),
        "manifest": template_manifest.to_dict(),
        "image": _image_payload(inspect, tag, source["image"]["platform"], labels),
    }
    context.store.write("seed", resource_id, receipt)
    return HandlerResult(
        resource={"kind": "seed", "id": resource_id, "docker_id": inspect["Id"]},
        payload={"reused": False, "tree_sha256": spec.tree_sha256, "source_id": spec.source_id},
        effect_certainty=EffectCertainty.COMMITTED,
    )


def import_seed(context: OperationContext, request: dict[str, Any]) -> HandlerResult:
    require_keys(request, required=frozenset({"path"}), optional=frozenset({"platform"}))
    root = Path(request["path"]).expanduser().resolve()
    before = manifest_tree(root)
    space_preflight = _ensure_capture_space(root, before.total_bytes)
    version = _read_version(root / ".brain-core" / "VERSION")
    core = normalised_core_manifest(root)
    platform = validate_linux_platform(request.get("platform", "linux/arm64"))
    spec = VaultSeedSpec(
        kind="imported",
        tree_sha256=before.tree_sha256,
        core_version=version,
        core_sha256=core.tree_sha256,
        platform=platform,
        imported=True,
    )
    resource_id = spec.identity()
    if context.store.receipt_path("seed", resource_id).exists():
        receipt = context.store.read("seed", resource_id)
        inspect = context.docker.image_inspect(receipt["image"]["tag"], context.evidence_directory / "reuse-inspect")
        context.docker.verify_resource_labels(inspect, "seed", resource_id)
        return HandlerResult(
            resource={"kind": "seed", "id": resource_id},
            payload={
                "reused": True,
                "tree_sha256": receipt["spec"]["tree_sha256"],
                "warning": "This is a local Docker copy of an imported vault.",
            },
        )
    labels = DockerClient.labels("seed", resource_id, imported=True)
    tag = DockerClient.deterministic_tag("seed", resource_id)
    context.docker.import_bundle(
        root,
        before,
        destination_prefix="bundle/vault",
        tag=tag,
        platform=platform,
        labels=labels,
        evidence_directory=context.evidence_directory / "import",
    )
    after = manifest_tree(root)
    if after.tree_sha256 != before.tree_sha256:
        _rollback_changed_capture(
            context,
            resource_kind="seed",
            resource_id=resource_id,
            tag=tag,
            reason="imported vault changed during capture",
        )
    inspect = context.docker.image_inspect(tag, context.evidence_directory / "inspect")
    receipt = {
        "spec": asdict(spec),
        "manifest": before.to_dict(),
        "core_manifest": core.to_dict(),
        "source_manifest_before": before.tree_sha256,
        "source_manifest_after": after.tree_sha256,
        "space_preflight": space_preflight,
        "image": _image_payload(inspect, tag, platform, labels),
        "warning": "Imported vault content is stored as a second local copy in Docker storage.",
    }
    context.store.write("seed", resource_id, receipt)
    return HandlerResult(
        resource={"kind": "seed", "id": resource_id, "docker_id": inspect["Id"]},
        payload={
            "reused": False,
            "tree_sha256": spec.tree_sha256,
            "warning": receipt["warning"],
            "space_preflight": space_preflight,
        },
        effect_certainty=EffectCertainty.COMMITTED,
    )


def register_resource_handlers(application: Application) -> None:
    application.register("base.build", build_base, mutating=True)
    application.register("base.inspect", inspect_resource("base"))
    application.register("base.destroy", destroy_image_resource("base"), mutating=True)
    application.register("source.resolve", resolve_source)
    application.register("source.capture", capture_source, mutating=True)
    application.register("source.inspect", inspect_resource("source"))
    application.register("source.destroy", destroy_image_resource("source"), mutating=True)
    application.register("seed.template", template_seed, mutating=True)
    application.register("seed.import", import_seed, mutating=True)
    application.register("seed.inspect", inspect_resource("seed"))
    application.register("seed.destroy", destroy_image_resource("seed"), mutating=True)
