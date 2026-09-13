"""Bounded opaque state with atomic, versioned updates for process owners."""

from __future__ import annotations

from dataclasses import dataclass
import json
from threading import RLock
from typing import Mapping


MAX_STATE_BYTES = 4 * 1024 * 1024
MAX_PAGE_KEYS = 128


class StoreClosedError(RuntimeError):
    """The process owning this state no longer accepts operations."""


class StoreCapacityError(ValueError):
    """The update would exceed the owner's retained-state budget."""


class StoreConflictError(ValueError):
    """A paginated state listing changed between pages."""


@dataclass(frozen=True, slots=True)
class StateSnapshot:
    values: dict[str, object]
    versions: dict[str, int]


@dataclass(frozen=True, slots=True)
class StateKeyPage:
    keys: tuple[str, ...]
    next_after: str | None
    revision: int


def _key(value: object) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 256:
        raise ValueError("state keys must contain 1–256 UTF-8 bytes")
    return value


def _version(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("state versions must be non-negative integers")
    return value


def json_bytes(value: object) -> bytes:
    """Encode strict JSON without silently coercing mapping keys or non-finite numbers."""

    def validate(item):
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ValueError("JSON object keys must be strings")
            for child in item.values():
                validate(child)
        elif isinstance(item, list):
            for child in item:
                validate(child)
        elif item is not None and type(item) not in (str, int, float, bool):
            raise ValueError("state values must be JSON values")

    try:
        validate(value)
        return json.dumps(
            value, ensure_ascii=False, allow_nan=False, separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, RecursionError, UnicodeError) as exc:
        raise ValueError("state value is not encodable JSON") from exc


class MemoryStateStore:
    """Keep JSON records and deletion versions for exactly one owner lifetime."""

    def __init__(self, *, max_bytes: int = MAX_STATE_BYTES) -> None:
        if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_STATE_BYTES:
            raise ValueError("state capacity must be between 1 byte and 4 MiB")
        self._max_bytes = max_bytes
        self._records: dict[str, tuple[int, bytes | None, int]] = {}
        self._bytes = 0
        self._revision = 0
        self._closed = False
        self._lock = RLock()

    def snapshot(self, keys: tuple[str, ...]) -> StateSnapshot:
        """Read only requested records, returning zero for never-written key versions."""

        if not isinstance(keys, tuple):
            raise ValueError("snapshot keys must be a tuple of distinct strings")
        for key in keys:
            _key(key)
        if len(keys) != len(set(keys)):
            raise ValueError("snapshot keys must be distinct")
        with self._lock:
            self._require_open()
            values = {}
            versions = {}
            for key in keys:
                version, encoded, _size = self._records.get(key, (0, None, 0))
                versions[key] = version
                if encoded is not None:
                    values[key] = json.loads(encoded)
            return StateSnapshot(values, versions)

    def compare_exchange(
        self,
        expected_versions: Mapping[str, int],
        writes: Mapping[str, object | None],
    ) -> bool:
        """Atomically apply matching writes; retain deletion versions to prevent ABA."""

        if not isinstance(expected_versions, Mapping) or not isinstance(writes, Mapping):
            raise ValueError("state comparisons and writes must be mappings")
        expected = {_key(key): _version(version) for key, version in expected_versions.items()}
        encoded = {
            _key(key): None if value is None else json_bytes(value)
            for key, value in writes.items()
        }
        if not encoded.keys() <= expected.keys():
            raise ValueError("every written key requires an expected version")
        with self._lock:
            self._require_open()
            if any(self._records.get(key, (0, None, 0))[0] != version for key, version in expected.items()):
                return False
            replacements = {}
            total_bytes = self._bytes
            for key, value in encoded.items():
                old_version, _old_value, old_size = self._records.get(key, (0, None, 0))
                version = old_version + 1
                size = len(json_bytes(key)) + len(str(version)) + (len(value) if value is not None else 4) + 4
                replacements[key] = (version, value, size)
                total_bytes += size - old_size
            if total_bytes > self._max_bytes:
                raise StoreCapacityError("owner state capacity reached; discard retained records or start a new context")
            self._records.update(replacements)
            self._bytes = total_bytes
            if replacements:
                self._revision += 1
            return True

    def list_keys(
        self,
        *,
        prefix: str = "",
        after: str | None = None,
        limit: int = MAX_PAGE_KEYS,
        revision: int | None = None,
    ) -> StateKeyPage:
        """Page live keys against one stable revision, excluding deletion tombstones."""

        if not isinstance(prefix, str) or len(prefix.encode("utf-8")) > 256:
            raise ValueError("state key prefix must contain at most 256 UTF-8 bytes")
        if after is not None:
            _key(after)
            if revision is None:
                raise ValueError("state key continuation requires its listing revision")
        if type(limit) is not int or not 1 <= limit <= MAX_PAGE_KEYS:
            raise ValueError("state key page limit must be between 1 and 128")
        if revision is not None:
            _version(revision)
        with self._lock:
            self._require_open()
            if revision is not None and revision != self._revision:
                raise StoreConflictError("owner state changed; restart the key listing")
            keys = sorted(
                key for key, (_version_value, value, _size) in self._records.items()
                if value is not None and key.startswith(prefix) and (after is None or key > after)
            )
            page = tuple(keys[:limit])
            return StateKeyPage(page, page[-1] if len(keys) > limit else None, self._revision)

    def close(self) -> None:
        """Permanently close admission and discard this owner's ephemeral state."""

        with self._lock:
            self._closed = True
            self._records.clear()
            self._bytes = 0

    def _require_open(self) -> None:
        if self._closed:
            raise StoreClosedError("owner context has ended")
