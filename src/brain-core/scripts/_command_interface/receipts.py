"""Durable privacy-minimal outcome receipts for local command adapters."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from threading import RLock

from _application.receipts import (
    AdmissionIntent,
    CommittedEffect,
    ExecutionState,
    InvocationOutcome,
    OwnedReceiptLookup,
    OutcomeReceipt,
    OutcomeReference,
    ReceiptPolicy,
    ReceiptOwnership,
    ReceiptOwnershipError,
    ReceiptIntentConflict,
    ReceiptState,
    PermissionChange,
)
from _common import safe_write_json
from _common._file_lock import exclusive_file_lock


RECEIPT_SCHEMA = "brain.command-outcome/1"
RECEIPT_DIRECTORY = Path(".brain/local/command-outcomes")
RECEIPT_INDEX_SCHEMA = "brain.command-outcome-index/1"
RECEIPT_INDEX_NAME = ".receipt-index"
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
_RECEIPT_FILENAME_RE = re.compile(r"[0-9a-f]{64}\.json")


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
            index = self._load_index_locked()
            index, _expired = self._expire_index_locked(index, now)
            path = self._path(receipt.reference)
            existing = self._read_path(path)
            if existing is not None:
                if existing != receipt:
                    raise ValueError("invocation receipt is immutable once recorded")
                if index.get(path.name) != receipt.recorded_at:
                    index[path.name] = receipt.recorded_at
                    self._write_index_locked(index)
                return
            safe_write_json(
                path,
                _encode(receipt),
                bounds=self._root,
                follow_symlinks=False,
            )
            index[path.name] = receipt.recorded_at
            self._trim_index_locked(index)
            self._write_index_locked(index)

    def read(self, reference: OutcomeReference) -> OutcomeReceipt | None:
        with self._lock:
            if not _validate_existing_directory(self._root, self._directory):
                return None
            now = self._clock.now()
            if now.tzinfo is None:
                raise ValueError("receipt read clock must be timezone-aware")
            receipt = self._read_path(self._path(reference))
            if (
                receipt is not None
                and receipt.recorded_at < now - self._policy.retention
            ):
                return None
            return receipt

    def cleanup(self) -> int:
        with self._locked_directory():
            now = self._clock.now()
            records = self._records_locked()
            retained, expired = self._expire_records_locked(records, now)
            self._write_index_locked(
                {path.name: receipt.recorded_at for path, receipt in retained}
            )
            return expired

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
        raw = _read_json_path(path)
        return None if raw is None else _decode(raw)

    def _remove_record(self, filename: str) -> None:
        (self._directory / filename).unlink(missing_ok=True)

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

    @property
    def _index_path(self) -> Path:
        return self._directory / RECEIPT_INDEX_NAME

    def _load_index_locked(self) -> dict[str, datetime]:
        path = self._index_path
        if path.is_symlink():
            raise ValueError("outcome receipt index must be a regular file")
        if not path.exists():
            return self._rebuild_index_locked()
        if not path.is_file():
            raise ValueError("outcome receipt index must be a regular file")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return _decode_index(raw)
        except FileNotFoundError:
            return self._rebuild_index_locked()
        except (OSError, ValueError, json.JSONDecodeError):
            return self._rebuild_index_locked()

    def _rebuild_index_locked(self) -> dict[str, datetime]:
        index = {
            path.name: receipt.recorded_at
            for path, receipt in self._records_locked()
        }
        self._write_index_locked(index)
        return index

    def _write_index_locked(self, index: dict[str, datetime]) -> None:
        safe_write_json(
            self._index_path,
            _encode_index(index),
            bounds=self._root,
            follow_symlinks=False,
        )

    def _expire_index_locked(
        self,
        index: dict[str, datetime],
        now: datetime,
    ) -> tuple[dict[str, datetime], int]:
        if now.tzinfo is None:
            raise ValueError("receipt cleanup clock must be timezone-aware")
        cutoff = now - self._policy.retention
        retained = {}
        expired = 0
        for filename, recorded_at in index.items():
            if recorded_at < cutoff:
                self._remove_record(filename)
                expired += 1
            else:
                retained[filename] = recorded_at
        return retained, expired

    def _trim_index_locked(self, index: dict[str, datetime]) -> None:
        overflow = len(index) - self._policy.max_records
        if overflow <= 0:
            return
        oldest = sorted(
            index,
            key=lambda filename: (index[filename], filename),
        )[:overflow]
        for filename in oldest:
            self._remove_record(filename)
            del index[filename]

    def _expire_records_locked(
        self,
        records: list[tuple[Path, OutcomeReceipt]],
        now: datetime,
    ) -> tuple[list[tuple[Path, OutcomeReceipt]], int]:
        if now.tzinfo is None:
            raise ValueError("receipt cleanup clock must be timezone-aware")
        cutoff = now - self._policy.retention
        retained = []
        expired = 0
        for path, receipt in records:
            if receipt.recorded_at < cutoff:
                self._remove_record(path.name)
                expired += 1
            else:
                retained.append((path, receipt))
        return retained, expired


def _read_json_path(path: Path):
    if path.is_symlink():
        raise ValueError(f"outcome receipt must be a regular file: {path.name}")
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError(f"outcome receipt must be a regular file: {path.name}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        # Writers publish atomically and maintenance may remove an expired
        # receipt between the existence check and the read.
        return None
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid outcome receipt: {path.name}") from exc
    if raw is None:
        raise ValueError(f"invalid outcome receipt: {path.name}")
    return raw


def _require_regular_vault(root: Path) -> None:
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise ValueError("receipt store requires a regular absolute Brain vault")
    version = root / ".brain-core" / "VERSION"
    if version.is_symlink() or not version.is_file():
        raise ValueError("receipt store requires an installed Brain Core")


def _ensure_private_directory(root: Path, directory: Path) -> None:
    current = root
    for part in directory.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"refusing symlinked receipt directory: {current}")
        if not current.exists():
            try:
                current.mkdir()
            except FileExistsError:
                # Another first invocation may create the directory before
                # either caller reaches the shared receipt lock.
                pass
        if current.is_symlink():
            raise ValueError(f"refusing symlinked receipt directory: {current}")
        if not current.is_dir():
            raise ValueError(f"receipt directory component is not a directory: {current}")
    if directory.resolve() != directory:
        raise ValueError("receipt directory resolves outside its fixed location")


def _validate_existing_directory(root: Path, directory: Path) -> bool:
    current = root
    for part in directory.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"refusing symlinked receipt directory: {current}")
        if not current.exists():
            return False
        if not current.is_dir():
            raise ValueError(
                f"receipt directory component is not a directory: {current}"
            )
    if directory.resolve() != directory:
        raise ValueError("receipt directory resolves outside its fixed location")
    return True


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


def _encode_index(index: dict[str, datetime]) -> dict[str, object]:
    return {
        "schema": RECEIPT_INDEX_SCHEMA,
        "records": {
            filename: recorded_at.isoformat()
            for filename, recorded_at in sorted(index.items())
        },
    }


def _decode_index(raw: object) -> dict[str, datetime]:
    if not isinstance(raw, dict) or set(raw) != {"schema", "records"}:
        raise ValueError("outcome receipt index has an invalid object shape")
    if raw["schema"] != RECEIPT_INDEX_SCHEMA:
        raise ValueError("outcome receipt index schema is unsupported")
    records = raw["records"]
    if not isinstance(records, dict):
        raise ValueError("outcome receipt index records must be an object")
    decoded = {}
    for filename, raw_recorded_at in records.items():
        if (
            not isinstance(filename, str)
            or _RECEIPT_FILENAME_RE.fullmatch(filename) is None
            or not isinstance(raw_recorded_at, str)
        ):
            raise ValueError("outcome receipt index contains an invalid record")
        try:
            recorded_at = datetime.fromisoformat(raw_recorded_at)
        except ValueError as exc:
            raise ValueError("outcome receipt index contains an invalid timestamp") from exc
        if recorded_at.tzinfo is None:
            raise ValueError("outcome receipt index timestamps must be timezone-aware")
        decoded[filename] = recorded_at
    return decoded


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


OWNED_RECEIPT_DIRECTORY = RECEIPT_DIRECTORY / "owned"
_INTENT_SCHEMA = "brain.admission-intent/1"
_OUTCOME_SCHEMA = "brain.owned-invocation-outcome/1"


@dataclass(frozen=True, slots=True)
class _StoredIntent:
    ownership: ReceiptOwnership
    intent: AdmissionIntent

    @property
    def reference(self):
        return self.intent.reference

    @property
    def recorded_at(self):
        return self.intent.recorded_at


class _OwnedLedger(FileReceiptStore):
    """Reuse receipt locking, index and bounded retention for paired records."""

    def __init__(self, vault_root, clock, policy):
        super().__init__(vault_root, clock, policy)
        self._directory = self._root / OWNED_RECEIPT_DIRECTORY

    def _read_path(self, path):
        raw = _read_json_path(path)
        if raw is None:
            return None
        ownership, value = _owned_record(raw, _INTENT_SCHEMA, "intent")
        return _StoredIntent(ownership, _decode_intent(value))

    def _remove_record(self, filename):
        # Evict the pair as one invocation; a racing reader can only become
        # less certain if it sees the intent after the final has been removed.
        (self._directory / filename).with_suffix(".outcome").unlink(missing_ok=True)
        super()._remove_record(filename)


class OwnedReceiptStore:
    """An authenticated facade over one global bounded owned-invocation ledger.

    Final outcomes are separate immutable files; an intent alone never proves
    entry or failure. The old public receipt store remains separate until cutover.
    """

    def __init__(self, vault_root: Path, clock, ownership: ReceiptOwnership,
                 policy: ReceiptPolicy | None = None):
        if not isinstance(ownership, ReceiptOwnership):
            raise ValueError("owned receipts require trusted typed ownership")
        self.ownership = ownership
        self._ledger = _OwnedLedger(vault_root, clock, policy)

    def _require_owner(self, ownership):
        if ownership != self.ownership:
            raise ReceiptOwnershipError("Receipt belongs to another Brain, principal or owner context.")

    def _deny_legacy(self, reference):
        directory = self._ledger._root / RECEIPT_DIRECTORY
        if _validate_existing_directory(self._ledger._root, directory):
            path = directory / self._ledger._path(reference).name
            if _read_json_path(path) is not None:
                raise ReceiptOwnershipError("Legacy receipt has no authenticated ownership and cannot be adopted.")

    def begin(self, intent: AdmissionIntent) -> bool:
        """Claim entry atomically; an identical retry returns False without re-entry."""
        if not isinstance(intent, AdmissionIntent):
            raise ValueError("admission requires a typed intent")
        ledger = self._ledger
        with ledger._locked_directory():
            now = ledger._clock.now()
            index, _ = ledger._expire_index_locked(ledger._load_index_locked(), now)
            if intent.recorded_at < now - ledger._policy.retention:
                raise ValueError("admission intent timestamp is outside receipt retention")
            path = ledger._path(intent.reference)
            existing = ledger._read_path(path)
            if existing is not None:
                self._require_owner(existing.ownership)
                if existing.intent != intent:
                    raise ReceiptIntentConflict("admission intent is immutable once recorded")
            else:
                self._deny_legacy(intent.reference)
                if _read_json_path(path.with_suffix(".outcome")) is not None:
                    raise ValueError("final outcome exists without its admission intent")
            index[path.name] = intent.recorded_at
            ledger._trim_index_locked(index)
            if path.name not in index:
                raise ValueError("admission intent cannot fit the current retention window")
            # Reserve the bounded inventory before publishing intent. A crash
            # can leave an absent record, never an unindexed durable intent.
            ledger._write_index_locked(index)
            if existing is None:
                safe_write_json(path, _encode_owned(self.ownership, _INTENT_SCHEMA,
                                                    "intent", _encode_intent(intent)),
                                bounds=ledger._root, follow_symlinks=False)
            return existing is None

    def finalise(self, outcome: InvocationOutcome) -> None:
        """Publish one immutable completion without changing its admission intent."""
        if not isinstance(outcome, InvocationOutcome):
            raise ValueError("finalisation requires a typed invocation outcome")
        ledger = self._ledger
        with ledger._locked_directory():
            intent = ledger._read_path(ledger._path(outcome.reference))
            if intent is None:
                self._deny_legacy(outcome.reference)
                raise ValueError("finalisation requires an existing admission intent")
            self._require_owner(intent.ownership)
            if intent.recorded_at < ledger._clock.now() - ledger._policy.retention:
                raise ValueError("admission intent expired; the outcome remains unknown")
            OwnedReceiptLookup(outcome.reference, intent.intent, outcome)
            path = ledger._path(outcome.reference).with_suffix(".outcome")
            existing = self._read_outcome(path)
            if existing is not None:
                if existing != outcome:
                    raise ValueError("invocation outcome is immutable once finalised")
                return
            safe_write_json(path, _encode_owned(self.ownership, _OUTCOME_SCHEMA,
                                                "outcome", {"execution": outcome.execution.value,
                                                            "receipt": _encode(outcome.receipt),
                                                            **({"config_revision": outcome.config_revision} if outcome.config_revision is not None else {})}),
                            bounds=ledger._root, follow_symlinks=False)

    def _read_outcome(self, path):
        raw = _read_json_path(path)
        if raw is None:
            return None
        owner, value = _owned_record(raw, _OUTCOME_SCHEMA, "outcome")
        self._require_owner(owner)
        if not isinstance(value, dict) or set(value) not in ({"execution", "receipt"}, {"execution", "receipt", "config_revision"}):
            raise ValueError("owned final outcome has an invalid shape")
        return InvocationOutcome(_decode(value["receipt"]), ExecutionState(value["execution"]), value.get("config_revision"))

    def read(self, reference: OutcomeReference) -> OwnedReceiptLookup:
        """Read only this owner's receipt; absence never authorises a replay."""
        ledger = self._ledger
        with ledger._lock:
            if not _validate_existing_directory(ledger._root, ledger._directory):
                self._deny_legacy(reference)
                return OwnedReceiptLookup(reference)
            path = ledger._path(reference)
            stored = ledger._read_path(path)
            if stored is None:
                self._deny_legacy(reference)
                return OwnedReceiptLookup(reference)
            self._require_owner(stored.ownership)
            now = ledger._clock.now()
            if now.tzinfo is None:
                raise ValueError("receipt read clock must be timezone-aware")
            if stored.recorded_at < now - ledger._policy.retention:
                return OwnedReceiptLookup(reference)
            return OwnedReceiptLookup(reference, stored.intent,
                                      self._read_outcome(path.with_suffix(".outcome")))

    def cleanup(self) -> int:
        """Evict expired invocation pairs together under the existing global budget."""
        return self._ledger.cleanup()


def _encode_owned(owner, schema, field, value):
    return {"schema": schema, "ownership": asdict(owner), field: value}


def _owned_record(raw, schema, field):
    if not isinstance(raw, dict) or set(raw) != {"schema", "ownership", field} or raw["schema"] != schema:
        raise ValueError("owned receipt has an invalid schema or shape")
    owner = raw["ownership"]
    if not isinstance(owner, dict) or set(owner) != {"brain_id", "principal", "kind", "context_id"}:
        raise ValueError("owned receipt ownership has an invalid shape")
    return ReceiptOwnership(**owner), raw[field]


def _encode_intent(intent):
    value = asdict(intent)
    value["recorded_at"] = intent.recorded_at.isoformat()
    if intent.permission_change is None:
        value.pop("permission_change")
    return value


def _decode_intent(raw):
    fields = {"reference", "command_id", "command_version", "recorded_at", "basis", "generation",
              "source", "grant_id", "operation_id", "operation_digest", "request_id"}
    if not isinstance(raw, dict) or set(raw) not in (fields, fields | {"permission_change"}):
        raise ValueError("admission intent has an invalid shape")
    reference = raw["reference"]
    if not isinstance(reference, dict) or set(reference) != {"invocation_id"}:
        raise ValueError("admission reference has an invalid shape")
    value = dict(raw, reference=OutcomeReference(_required_string(reference, "invocation_id")),
                 recorded_at=datetime.fromisoformat(_required_string(raw, "recorded_at")))
    change = value.get("permission_change")
    if change is not None:
        expected = {"operator_id", "before_profile", "after_profile", "added_permissions", "removed_permissions", "before_revision"}
        if not isinstance(change, dict) or set(change) != expected:
            raise ValueError("permission change audit has an invalid shape")
        if any(not isinstance(change[key], list) for key in ("added_permissions", "removed_permissions")):
            raise ValueError("permission change audit command sets are invalid")
        value["permission_change"] = PermissionChange(**{**change,
            "added_permissions": tuple(change["added_permissions"]), "removed_permissions": tuple(change["removed_permissions"])})
    return AdmissionIntent(**value)
