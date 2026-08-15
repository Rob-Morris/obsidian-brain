"""Dependency-light inspection for generated Brain cache state.

Every field read from a generated cache file is untrusted input. If a
present field has the wrong type, the cache is structurally unusable for
downstream consumers: return a named stale reason with ``payload=None`` so
callers short-circuit. Only return ``payload=data`` for stale states whose
parsed structure is still safe to read, because ``check.py`` runs structural
checks against that payload.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import stat
from typing import Any, Mapping


_router_resource_signatures: dict[
    Path,
    tuple[str, tuple[tuple[str, int | None, int | None], ...]],
] = {}


@dataclass(frozen=True)
class CacheState:
    """Read-only status for one generated cache file."""

    stale: bool
    reason: str
    path: str
    payload: Mapping[str, Any] | None = None


def inspect_router_cache(
    vault_root: str | Path,
    *,
    verify_content: bool = False,
) -> CacheState:
    """Inspect the compiled router cache without mutating it.

    ``verify_content`` is required before mutation admission. The default
    status path may reuse unchanged filesystem metadata, but metadata alone is
    not authoritative evidence that source content still matches the router.
    """
    import compile_router

    vault_root = Path(vault_root)
    router_path = vault_root / compile_router.OUTPUT_PATH
    rel_path = compile_router.OUTPUT_PATH
    if not router_path.is_file():
        return CacheState(True, "missing", rel_path)

    try:
        data = json.loads(router_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return CacheState(True, "invalid-json", rel_path)
    if not isinstance(data, dict):
        return CacheState(True, "invalid-payload", rel_path)

    meta = data.get("meta", {})
    if not isinstance(meta, dict):
        return CacheState(True, "invalid-metadata", rel_path)
    compiled_at = meta.get("compiled_at")
    sources = meta.get("sources", {})
    if not compiled_at or not isinstance(sources, dict) or not sources:
        return CacheState(True, "missing-metadata", rel_path, data)

    try:
        compiled_ts = datetime.fromisoformat(compiled_at).timestamp()
    except (ValueError, TypeError):
        return CacheState(True, "invalid-timestamp", rel_path, data)

    artefacts = data.get("artefacts", [])
    artefact_index = data.get("artefact_index", {})
    artefact_index_sources = meta.get("artefact_index_sources")
    if artefact_index and artefact_index_sources is None:
        return CacheState(True, "missing-artefact-index-sources", rel_path, data)
    if artefact_index_sources is not None and not isinstance(artefact_index_sources, list):
        return CacheState(True, "invalid-artefact-index-sources", rel_path, data)
    artefact_index_source_paths = set(artefact_index_sources or [])

    expected_index_source_count = meta.get("artefact_index_source_count")
    resource_signature = _resource_mtime_signature(vault_root, compile_router)
    source_hash = str(meta.get("source_hash") or "")
    cached_resource_state = _router_resource_signatures.get(vault_root)
    resources_changed = cached_resource_state != (source_hash, resource_signature)
    inventory_required = resources_changed or verify_content
    current_index_sources = None
    if inventory_required and expected_index_source_count is not None:
        current_index_source_count, current_index_sources = (
            compile_router.living_artefact_source_state(
                str(vault_root), artefacts
            )
        )
        if current_index_source_count != expected_index_source_count:
            return CacheState(True, "artefact-index-count-drift", rel_path, data)

    if inventory_required:
        for key, fs_count in compile_router.resource_counts(str(vault_root)).items():
            if fs_count != len(data.get(key, [])):
                return CacheState(True, f"{key}-count-drift", rel_path, data)

    for source_rel_path, expected_hash in sources.items():
        abs_path = vault_root / source_rel_path
        if source_rel_path in artefact_index_source_paths:
            if not inventory_required:
                continue
            if current_index_sources is None:
                _count, current_index_sources = compile_router.living_artefact_source_state(
                    str(vault_root), artefacts
                )
            current_hash = current_index_sources.get(source_rel_path)
            if current_hash is None:
                return CacheState(True, "artefact-index-source-unreadable", rel_path, data)
            if current_hash != expected_hash:
                return CacheState(True, "artefact-index-source-drift", rel_path, data)
            continue

        if verify_content:
            try:
                current_hash = compile_router.hash_file(abs_path)
            except OSError:
                return CacheState(True, "missing-source", rel_path, data)
            if current_hash != expected_hash:
                return CacheState(True, "source-content-drift", rel_path, data)
            continue

        try:
            if os.path.getmtime(abs_path) > compiled_ts:
                return CacheState(True, "source-newer-than-router", rel_path, data)
        except OSError:
            return CacheState(True, "missing-source", rel_path, data)

    if resources_changed:
        _router_resource_signatures[vault_root] = (source_hash, resource_signature)
    return CacheState(False, "fresh", rel_path, data)


def _resource_mtime_signature(vault_root: Path, compile_router):
    """Return cheap file-presence/mtime evidence for router-count inputs."""
    signature = []
    root_text = str(vault_root)
    for relative, descend in compile_router.resource_source_dirs(root_text):
        absolute = vault_root / relative if relative else vault_root
        if descend:
            _append_filtered_tree(absolute, vault_root, signature)
            continue
        try:
            stat = absolute.stat()
            signature.append((relative, stat.st_mtime_ns, stat.st_size))
        except OSError:
            signature.append((relative, None, None))
    return tuple(signature)


def _append_filtered_tree(path: Path, vault_root: Path, signature: list) -> None:
    try:
        entries = sorted(path.iterdir(), key=lambda item: item.name)
    except OSError:
        signature.append((path.relative_to(vault_root).as_posix(), None, None))
        return
    for entry in entries:
        if entry.name.startswith((".", "_")):
            continue
        try:
            file_stat = entry.stat(follow_symlinks=False)
            relative = entry.relative_to(vault_root).as_posix()
            signature.append((relative, file_stat.st_mtime_ns, file_stat.st_size))
            if stat.S_ISDIR(file_stat.st_mode):
                _append_filtered_tree(entry, vault_root, signature)
        except OSError:
            continue


def load_fresh_compiled_router(vault_root: str | Path) -> dict[str, Any]:
    """Load the compiled router only when the cache is fresh enough to mutate."""
    state = inspect_router_cache(vault_root, verify_content=True)
    if state.stale:
        return {
            "error": (
                "Compiled router cache is stale or unreadable "
                f"({state.reason}). Run compile_router.py or repair.py before mutating."
            )
        }
    return dict(state.payload or {})


def inspect_lexical_cache(vault_root: str | Path) -> CacheState:
    """Inspect the lexical retrieval index cache without mutating it."""
    from _common import iter_artefact_paths
    import compile_router
    import _search.paths as search_paths

    vault_root = Path(vault_root)
    index_path = vault_root / search_paths.OUTPUT_PATH
    rel_path = search_paths.OUTPUT_PATH
    if not index_path.is_file():
        return CacheState(True, "missing", rel_path)

    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return CacheState(True, "invalid-json", rel_path)
    if not isinstance(data, dict):
        return CacheState(True, "invalid-payload", rel_path)

    meta = data.get("meta", {})
    if not isinstance(meta, dict):
        return CacheState(True, "invalid-metadata", rel_path, data)
    if meta.get("index_version") != search_paths.INDEX_VERSION:
        return CacheState(True, "version-drift", rel_path, data)

    built_at = meta.get("built_at")
    if not built_at:
        return CacheState(True, "missing-built-at", rel_path, data)
    try:
        threshold = datetime.fromisoformat(built_at).timestamp()
    except (ValueError, TypeError):
        return CacheState(True, "invalid-built-at", rel_path, data)

    expected_count = meta.get("document_count", 0)
    if not isinstance(expected_count, int):
        return CacheState(True, "invalid-document-count", rel_path)
    all_types = (
        compile_router.scan_living_types(str(vault_root))
        + compile_router.scan_temporal_types(str(vault_root))
    )
    count = 0
    for type_info in all_types:
        for rel_path_doc in iter_artefact_paths(str(vault_root), type_info):
            count += 1
            if count > expected_count:
                return CacheState(True, "document-count-drift", rel_path, data)
            try:
                if os.path.getmtime(vault_root / rel_path_doc) > threshold:
                    return CacheState(True, "document-newer-than-index", rel_path, data)
            except OSError:
                continue
    if count != expected_count:
        return CacheState(True, "document-count-drift", rel_path, data)

    return CacheState(False, "fresh", rel_path, data)
