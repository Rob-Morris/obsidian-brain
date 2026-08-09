"""Durable privacy-minimal outcome receipts for local command adapters."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
from pathlib import Path
from threading import RLock

from _application.receipts import (
    CommittedEffect,
    OutcomeReceipt,
    OutcomeReference,
    ReceiptPolicy,
    ReceiptState,
)
from _common import safe_write_json
from _common._file_lock import exclusive_file_lock


RECEIPT_SCHEMA = "brain.command-outcome/1"
RECEIPT_DIRECTORY = Path(".brain/local/command-outcomes")
_RECEIPT_KEYS = frozenset(
    {
        "schema",
        "invocation_id",
        "command_id",
        "command_version",
        "state",
        "recorded_at",
        "committed_effects",
    }
)


class FileReceiptStore:
    """Bounded file-backed receipts shared by direct, CLI and proxy adapters."""

    def __init__(self, vault_root: Path, clock, policy: ReceiptPolicy | None = None) -> None:
        if not vault_root.is_absolute() or vault_root.is_symlink():
            raise ValueError("receipt store requires a regular absolute Brain vault")
        self._root = vault_root.resolve()
        self._clock = clock
        self._policy = policy or ReceiptPolicy()
        self._directory = self._root / RECEIPT_DIRECTORY
        self._lock = RLock()
        _require_regular_vault(self._root)

    def write(self, receipt: OutcomeReceipt) -> None:
        if receipt.state is ReceiptState.NONE:
            return
        with self._locked_directory():
            now = self._clock.now()
            self._cleanup_locked(now)
            path = self._path(receipt.reference)
            existing = self._read_path(path)
            if existing is not None:
                if existing != receipt:
                    raise ValueError("invocation receipt is immutable once recorded")
                return
            safe_write_json(
                path,
                _encode(receipt),
                bounds=self._root,
                follow_symlinks=False,
            )
            self._trim_locked()

    def read(self, reference: OutcomeReference) -> OutcomeReceipt | None:
        with self._locked_directory():
            now = self._clock.now()
            self._cleanup_locked(now)
            return self._read_path(self._path(reference))

    def cleanup(self) -> int:
        with self._locked_directory():
            return self._cleanup_locked(self._clock.now())

    @contextmanager
    def _locked_directory(self):
        with self._lock:
            _ensure_private_directory(self._root, self._directory)
            lock_path = self._directory / ".receipts.lock"
            if lock_path.is_symlink() or (
                lock_path.exists() and not lock_path.is_file()
            ):
                raise ValueError("outcome receipt lock must be a regular file")
            with exclusive_file_lock(lock_path):
                yield

    def _path(self, reference: OutcomeReference) -> Path:
        digest = hashlib.sha256(reference.invocation_id.encode("utf-8")).hexdigest()
        return self._directory / f"{digest}.json"

    def _read_path(self, path: Path) -> OutcomeReceipt | None:
        if path.is_symlink():
            raise ValueError(f"outcome receipt must be a regular file: {path.name}")
        if not path.exists():
            return None
        if not path.is_file():
            raise ValueError(f"outcome receipt must be a regular file: {path.name}")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid outcome receipt: {path.name}") from exc
        return _decode(raw)

    def _records_locked(self) -> list[tuple[Path, OutcomeReceipt]]:
        records = []
        for path in sorted(self._directory.glob("*.json")):
            receipt = self._read_path(path)
            if receipt is not None:
                if path != self._path(receipt.reference):
                    raise ValueError(
                        f"outcome receipt filename does not match its reference: {path.name}"
                    )
                records.append((path, receipt))
        return records

    def _cleanup_locked(self, now: datetime) -> int:
        if now.tzinfo is None:
            raise ValueError("receipt cleanup clock must be timezone-aware")
        cutoff = now - self._policy.retention
        expired = [
            path
            for path, receipt in self._records_locked()
            if receipt.recorded_at < cutoff
        ]
        for path in expired:
            path.unlink()
        return len(expired)

    def _trim_locked(self) -> None:
        records = self._records_locked()
        overflow = len(records) - self._policy.max_records
        if overflow <= 0:
            return
        oldest = sorted(
            records,
            key=lambda item: (
                item[1].recorded_at,
                item[1].reference.invocation_id,
            ),
        )[:overflow]
        for path, _receipt in oldest:
            path.unlink()


def _require_regular_vault(root: Path) -> None:
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise ValueError("receipt store requires a regular absolute Brain vault")
    version = root / ".brain-core" / "VERSION"
    if version.is_symlink() or not version.is_file():
        raise ValueError("receipt store requires an installed Brain Core")


def _ensure_private_directory(root: Path, directory: Path) -> None:
    current = root
    for part in RECEIPT_DIRECTORY.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"refusing symlinked receipt directory: {current}")
        if current.exists():
            if not current.is_dir():
                raise ValueError(f"receipt directory component is not a directory: {current}")
        else:
            current.mkdir()
    if directory.resolve() != directory:
        raise ValueError("receipt directory resolves outside its fixed location")


def _encode(receipt: OutcomeReceipt) -> dict[str, object]:
    return {
        "schema": RECEIPT_SCHEMA,
        "invocation_id": receipt.reference.invocation_id,
        "command_id": receipt.command_id,
        "command_version": receipt.command_version,
        "state": receipt.state.value,
        "recorded_at": receipt.recorded_at.isoformat(),
        "committed_effects": [
            {"kind": effect.kind, "subject": effect.subject}
            for effect in receipt.committed_effects
        ],
    }


def _decode(raw: object) -> OutcomeReceipt:
    if not isinstance(raw, dict) or set(raw) != _RECEIPT_KEYS:
        raise ValueError("outcome receipt has an invalid object shape")
    if raw["schema"] != RECEIPT_SCHEMA:
        raise ValueError("outcome receipt schema is unsupported")
    invocation_id = _required_string(raw, "invocation_id")
    command_id = _required_string(raw, "command_id")
    command_version = raw["command_version"]
    if not isinstance(command_version, int) or isinstance(command_version, bool):
        raise ValueError("outcome receipt command_version must be an integer")
    try:
        state = ReceiptState(_required_string(raw, "state"))
        recorded_at = datetime.fromisoformat(_required_string(raw, "recorded_at"))
    except ValueError as exc:
        raise ValueError("outcome receipt contains an invalid state or timestamp") from exc
    effects = raw["committed_effects"]
    if not isinstance(effects, list):
        raise ValueError("outcome receipt committed_effects must be a list")
    committed = []
    for effect in effects:
        if not isinstance(effect, dict) or set(effect) != {"kind", "subject"}:
            raise ValueError("outcome receipt contains an invalid committed effect")
        committed.append(
            CommittedEffect(
                _required_string(effect, "kind"),
                _required_string(effect, "subject"),
            )
        )
    return OutcomeReceipt(
        OutcomeReference(invocation_id),
        command_id,
        command_version,
        state,
        recorded_at,
        tuple(committed),
    )


def _required_string(raw: dict[str, object], key: str) -> str:
    value = raw[key]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"outcome receipt {key} must be a non-empty string")
    return value
