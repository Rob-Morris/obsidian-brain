from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable


RESOURCE_KINDS = {"base", "source", "seed", "attempt", "baseline", "run", "result"}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def resource_references(
    receipts: Iterable[dict[str, Any]],
) -> dict[tuple[str, str], list[dict[str, str]]]:
    """Return the canonical dependency graph expressed by resource receipts."""
    references: dict[tuple[str, str], list[dict[str, str]]] = {}

    def add(kind: Any, resource_id: Any, owner_kind: Any, owner_id: Any) -> None:
        if not all(isinstance(value, str) for value in (kind, resource_id, owner_kind, owner_id)):
            return
        references.setdefault((kind, resource_id), []).append(
            {"kind": owner_kind, "id": owner_id}
        )

    for receipt in receipts:
        owner_kind = receipt.get("resource_kind")
        owner_id = receipt.get("resource_id")
        if owner_kind == "seed":
            add("source", receipt.get("spec", {}).get("source_id"), owner_kind, owner_id)
        elif owner_kind == "baseline":
            recipe = receipt.get("recipe", {})
            for kind in ("base", "source", "seed"):
                add(kind, recipe.get(f"{kind}_id"), owner_kind, owner_id)
            add("attempt", receipt.get("attempt_id"), owner_kind, owner_id)
        elif owner_kind == "run":
            source = receipt.get("source", {})
            add(source.get("kind"), source.get("id"), owner_kind, owner_id)
        elif owner_kind == "result":
            for step in receipt.get("payload", {}).get("steps", []):
                if isinstance(step, dict):
                    add("result", step.get("operation_id"), owner_kind, owner_id)

    for owners in references.values():
        owners.sort(key=lambda item: (item["kind"], item["id"]))
    return references


class StateStore:
    def __init__(self, root: Path | None = None):
        if root is None:
            data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
            root = data_home / "brain-lab"
        self.root = root.expanduser().resolve()
        self.receipts = self.root / "receipts"
        self.evidence = self.root / "evidence"
        self.receipts.mkdir(parents=True, exist_ok=True)
        self.evidence.mkdir(parents=True, exist_ok=True)

    def _resource_directory(self, kind: str) -> Path:
        if kind not in RESOURCE_KINDS:
            raise ValueError(f"unknown resource kind: {kind}")
        directory = self.receipts / kind
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def receipt_path(self, kind: str, resource_id: str) -> Path:
        if "/" in resource_id or resource_id in {"", ".", ".."}:
            raise ValueError("resource ID is not safe")
        return self._resource_directory(kind) / f"{resource_id}.json"

    def write(self, kind: str, resource_id: str, value: dict[str, Any]) -> Path:
        path = self.receipt_path(kind, resource_id)
        payload = dict(value)
        payload.setdefault("resource_kind", kind)
        payload.setdefault("resource_id", resource_id)
        payload.setdefault("created_at", utc_now())
        fd, temporary = tempfile.mkstemp(prefix=f".{resource_id}.", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return path

    def read(self, kind: str, resource_id: str) -> dict[str, Any]:
        path = self.receipt_path(kind, resource_id)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError(f"unknown {kind} resource: {resource_id}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"corrupt receipt: {path}")
        return value

    def list(self, kind: str | None = None) -> list[dict[str, Any]]:
        kinds: Iterable[str] = [kind] if kind else sorted(RESOURCE_KINDS)
        values = []
        for candidate in kinds:
            for path in sorted(self._resource_directory(candidate).glob("*.json")):
                try:
                    value = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    value = {"resource_kind": candidate, "resource_id": path.stem, "corrupt": True}
                values.append(value)
        return values

    def delete(self, kind: str, resource_id: str) -> bool:
        path = self.receipt_path(kind, resource_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def references_to(self, kind: str, resource_id: str) -> list[dict[str, str]]:
        return resource_references(self.list()).get((kind, resource_id), [])

    def evidence_directory(self, operation_id: str) -> Path:
        directory = self.evidence / operation_id
        directory.mkdir(parents=True, exist_ok=False)
        return directory
