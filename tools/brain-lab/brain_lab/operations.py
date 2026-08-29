from __future__ import annotations

import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .application import Application, HandlerResult, OperationContext
from .docker import ID_LABEL, IMPORTED_LABEL, KIND_LABEL, MANAGED_LABEL, DockerError
from .model import EffectCertainty, require_keys
from .store import resource_references


def inspect_result(context: OperationContext, request: dict[str, Any]) -> HandlerResult:
    require_keys(request, required=frozenset({"id"}))
    receipt = context.store.read("result", request["id"])
    bundle_path = context.store.evidence / request["id"] / "bundle.json"
    bundle = json.loads(bundle_path.read_text(encoding="utf-8")) if bundle_path.is_file() else None
    return HandlerResult(
        resource={"kind": "result", "id": request["id"]},
        payload={"receipt": receipt, "evidence_bundle": bundle},
    )


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        allowed = ("/home/brain", "/opt/brain-lab", "/usr/local/lib/brain-lab")
        if value.startswith("/") and not value.startswith(allowed):
            return "<redacted-host-path>"
        value = re.sub(
            r"(?<![A-Za-z0-9])(?:/Users|/private|/tmp|/var/folders)/[^\s\"']+",
            "<redacted-host-path>",
            value,
        )
        value = re.sub(r"[A-Za-z]:\\\\Users\\\\[^\s\"']+", "<redacted-host-path>", value)
        return value
    return value


def export_result(context: OperationContext, request: dict[str, Any]) -> HandlerResult:
    require_keys(
        request,
        required=frozenset({"id", "destination"}),
        optional=frozenset({"redacted"}),
    )
    result_id = request["id"]
    destination = Path(request["destination"]).expanduser().resolve()
    if destination.exists():
        raise ValueError(f"result export destination already exists: {destination}")
    destination.mkdir(parents=True)
    receipt = context.store.read("result", result_id)
    evidence = context.store.evidence / result_id
    redacted = request.get("redacted", True)
    if redacted:
        (destination / "receipt.json").write_text(
            json.dumps(_redact(receipt), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        bundle_path = evidence / "bundle.json"
        if bundle_path.is_file():
            bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
            bundle["raw_files_exported"] = False
            (destination / "bundle.json").write_text(
                json.dumps(_redact(bundle), indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
    else:
        shutil.copytree(evidence, destination / "evidence")
        (destination / "receipt.json").write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return HandlerResult(
        resource={"kind": "result", "id": result_id},
        payload={"destination": str(destination), "redacted": redacted},
        effect_certainty=EffectCertainty.COMMITTED,
    )


def destroy_result(context: OperationContext, request: dict[str, Any]) -> HandlerResult:
    require_keys(request, required=frozenset({"id"}))
    context.store.read("result", request["id"])
    references = context.store.references_to("result", request["id"])
    if references:
        rendered = ", ".join(f"{item['kind']} {item['id']}" for item in references)
        raise ValueError(f"cannot destroy referenced result {request['id']}: {rendered}")
    evidence = context.store.evidence / request["id"]
    if evidence.is_dir():
        shutil.rmtree(evidence)
    context.store.delete("result", request["id"])
    return HandlerResult(
        resource={"kind": "result", "id": request["id"]},
        payload={"destroyed": True},
        effect_certainty=EffectCertainty.COMMITTED,
    )


def _receipt_is_imported(receipt: dict[str, Any]) -> bool:
    labels = (receipt.get("image") or {}).get("labels") or {}
    return bool(
        receipt.get("spec", {}).get("imported")
        or receipt.get("imported")
        or labels.get(IMPORTED_LABEL) == "true"
    )


def _summary_labels(row: dict[str, Any]) -> dict[str, str]:
    raw = row.get("Labels")
    if not isinstance(raw, str):
        return {}
    labels = {}
    for item in raw.split(","):
        key, separator, value = item.partition("=")
        if separator and key:
            labels[key] = value
    return labels


def _receipt_docker_id(receipt: dict[str, Any], docker_kind: str) -> str | None:
    owner = receipt.get("container") if docker_kind == "container" else receipt.get("image")
    docker_id = owner.get("id") if isinstance(owner, dict) else None
    return docker_id if isinstance(docker_id, str) and docker_id else None


def _docker_orphans(context: OperationContext, docker: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    orphans = []
    for docker_kind, rows in (("container", docker["containers"]), ("image", docker["images"])):
        for row in rows:
            labels = _summary_labels(row)
            kind = labels.get(KIND_LABEL)
            resource_id = labels.get(ID_LABEL)
            if labels.get(MANAGED_LABEL) != "true" or not kind or not resource_id:
                continue
            try:
                receipt_path = context.store.receipt_path(kind, resource_id)
            except ValueError:
                receipt_path = None
            docker_id = row.get("ID")
            if not isinstance(docker_id, str) or not docker_id:
                continue
            if receipt_path is not None and receipt_path.exists():
                try:
                    receipt = context.store.read(kind, resource_id)
                except ValueError:
                    continue
                if _receipt_docker_id(receipt, docker_kind) == docker_id:
                    continue
            orphans.append(
                {
                    "docker_kind": docker_kind,
                    "docker_id": docker_id,
                    "resource_kind": kind,
                    "resource_id": resource_id,
                    "imported": labels.get(IMPORTED_LABEL) == "true",
                    "size": row.get("Size"),
                    "destroy_request": {
                        "docker_kind": docker_kind,
                        "docker_id": docker_id,
                        **(
                            {"confirm_imported": True}
                            if labels.get(IMPORTED_LABEL) == "true"
                            else {}
                        ),
                    },
                }
            )
    return sorted(orphans, key=lambda item: (item["docker_kind"], item["docker_id"]))


def inventory(context: OperationContext, request: dict[str, Any]) -> HandlerResult:
    require_keys(request)
    receipts = context.store.list()
    references = resource_references(receipts)
    docker = context.docker.list_managed(context.evidence_directory / "docker")
    values = []
    for receipt in receipts:
        kind = receipt.get("resource_kind")
        resource_id = receipt.get("resource_id")
        image = receipt.get("image", {})
        values.append(
            {
                "kind": kind,
                "id": resource_id,
                "created_at": receipt.get("created_at"),
                "size": image.get("size"),
                "imported": _receipt_is_imported(receipt),
                "references": references.get((kind, resource_id), []),
                "evidence": str(context.store.evidence / resource_id)
                if kind == "result"
                else None,
                "corrupt": receipt.get("corrupt", False),
            }
        )
    return HandlerResult(payload={"resources": values, "docker": docker})


def cleanup_preview(context: OperationContext, request: dict[str, Any]) -> HandlerResult:
    require_keys(request, optional=frozenset({"older_than_days"}))
    days = int(request.get("older_than_days", 7))
    if days < 0:
        raise ValueError("older_than_days cannot be negative")
    cutoff = datetime.now(UTC).timestamp() - days * 86400
    receipts = context.store.list()
    docker = context.docker.list_managed(context.evidence_directory / "docker")
    references = resource_references(receipts)
    candidates = []
    retained = []
    for receipt in receipts:
        created = receipt.get("created_at")
        try:
            old_enough = created is not None and datetime.fromisoformat(created).timestamp() <= cutoff
        except (TypeError, ValueError):
            old_enough = False
        key = (receipt.get("resource_kind"), receipt.get("resource_id"))
        imported = _receipt_is_imported(receipt)
        if old_enough and not references.get(key) and not imported:
            candidates.append({"kind": key[0], "id": key[1], "exact_only": True})
        else:
            reasons = []
            if not old_enough:
                reasons.append("younger-than-threshold-or-unknown-age")
            if references.get(key):
                reasons.append("referenced")
            if imported:
                reasons.append("imported-vault-requires-explicit-deletion")
            retained.append({"kind": key[0], "id": key[1], "reasons": reasons})
    return HandlerResult(
        payload={
            "dry_run": True,
            "older_than_days": days,
            "candidates": candidates,
            "retained": retained,
            "docker_orphans": _docker_orphans(context, docker),
        }
    )


def destroy_orphan(context: OperationContext, request: dict[str, Any]) -> HandlerResult:
    require_keys(
        request,
        required=frozenset({"docker_kind", "docker_id"}),
        optional=frozenset({"confirm_imported"}),
    )
    docker_kind = request["docker_kind"]
    docker_id = request["docker_id"]
    if docker_kind not in {"container", "image"}:
        raise ValueError("orphan Docker kind must be container or image")
    inspect = (
        context.docker.container_inspect(docker_id, context.evidence_directory / "00-inspect")
        if docker_kind == "container"
        else context.docker.image_inspect(docker_id, context.evidence_directory / "00-inspect")
    )
    labels = (inspect.get("Config", {}).get("Labels") or {})
    kind = labels.get(KIND_LABEL)
    resource_id = labels.get(ID_LABEL)
    if labels.get(MANAGED_LABEL) != "true" or not kind or not resource_id:
        raise DockerError("Docker resource is not a fully labelled Brain Lab resource; refusing mutation")
    context.docker.verify_resource_labels(inspect, kind, resource_id)
    try:
        receipt_path = context.store.receipt_path(kind, resource_id)
    except ValueError as exc:
        raise DockerError("Docker resource labels do not name a valid Brain Lab resource") from exc
    recorded = False
    if receipt_path.exists():
        try:
            receipt = context.store.read(kind, resource_id)
        except ValueError as exc:
            raise DockerError(
                "Docker resource has an unreadable matching Brain Lab receipt; refusing mutation"
            ) from exc
        recorded = _receipt_docker_id(receipt, docker_kind) == inspect["Id"]
    if recorded:
        raise ValueError(
            f"Docker resource belongs to recorded {kind} {resource_id}; use its exact resource operation"
        )
    imported = labels.get(IMPORTED_LABEL) == "true"
    if imported and request.get("confirm_imported") is not True:
        raise ValueError("imported orphan deletion requires confirm_imported=true")
    if docker_kind == "container":
        context.docker.remove_container(inspect["Id"], context.evidence_directory / "01-remove")
    else:
        context.docker.remove_image(inspect["Id"], context.evidence_directory / "01-remove")
    return HandlerResult(
        resource={"kind": "orphan", "id": resource_id, "docker_id": inspect["Id"]},
        payload={"destroyed": True, "docker_kind": docker_kind, "imported": imported},
        effect_certainty=EffectCertainty.COMMITTED,
    )


def register_operational_handlers(application: Application) -> None:
    application.register("results.inspect", inspect_result)
    application.register("results.export", export_result, mutating=True)
    application.register("results.destroy", destroy_result, mutating=True)
    application.register("lab.inventory", inventory)
    application.register("cleanup.preview", cleanup_preview)
    application.register("orphan.destroy", destroy_orphan, mutating=True)
