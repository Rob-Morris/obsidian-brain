"""Signature-refreshed derived-state snapshots for long-lived adapters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Callable

from _common import COMPILED_ROUTER_REL, load_compiled_router
from _search.lexical_query import load_index
from _search.paths import OUTPUT_PATH


@dataclass(frozen=True, slots=True)
class _CachedSnapshot:
    signature: tuple[int, int, int, int] | None
    value: dict


class FileDerivedSnapshotStore:
    """Reuse parsed router/index state until the authoritative file changes."""

    def __init__(self, vault_root: Path):
        self._root = vault_root.resolve()
        self._lock = threading.RLock()
        self._cache: dict[str, _CachedSnapshot] = {}

    def load_router(self) -> dict:
        return self._load(
            "router",
            self._root / COMPILED_ROUTER_REL,
            lambda: load_compiled_router(self._root),
        )

    def load_lexical_index(self) -> dict:
        return self._load(
            "lexical",
            self._root / OUTPUT_PATH,
            lambda: load_index(self._root),
        )

    def invalidate(self, *names: str) -> None:
        """Discard named snapshots, or every snapshot when no names are given."""

        with self._lock:
            if not names:
                self._cache.clear()
                return
            for name in names:
                self._cache.pop(name, None)

    def _load(self, name: str, path: Path, loader: Callable[[], dict]) -> dict:
        with self._lock:
            signature = _signature(path)
            cached = self._cache.get(name)
            if cached is not None and cached.signature == signature:
                return cached.value
            value = loader()
            loaded_signature = _signature(path)
            if loaded_signature != signature:
                value = loader()
                loaded_signature = _signature(path)
            self._cache[name] = _CachedSnapshot(loaded_signature, value)
            return value


def _signature(path: Path) -> tuple[int, int, int, int] | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
