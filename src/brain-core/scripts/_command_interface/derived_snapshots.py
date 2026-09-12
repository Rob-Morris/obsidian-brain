"""Signature-refreshed derived-state snapshots for long-lived adapters."""

from __future__ import annotations

import copy
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


class DerivedSnapshotChangedError(RuntimeError):
    """The authoritative derived file did not stabilise during a bounded load."""


class FileDerivedSnapshotStore:
    """Reuse parsed state while returning caller-owned defensive values."""

    _MAX_STABILITY_ATTEMPTS = 3

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
                return copy.deepcopy(cached.value)
            for _attempt in range(self._MAX_STABILITY_ATTEMPTS):
                before = _signature(path)
                value = loader()
                after = _signature(path)
                if before == after:
                    stored = copy.deepcopy(value)
                    self._cache[name] = _CachedSnapshot(after, stored)
                    return copy.deepcopy(stored)
            raise DerivedSnapshotChangedError(
                f"{name} derived snapshot changed during "
                f"{self._MAX_STABILITY_ATTEMPTS} consecutive loads"
            )


def _signature(path: Path) -> tuple[int, int, int, int] | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
