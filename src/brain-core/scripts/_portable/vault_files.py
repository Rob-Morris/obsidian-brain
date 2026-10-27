"""Portable exact vault-file and archived-artefact reads."""

from __future__ import annotations

import os
from pathlib import Path
from datetime import datetime, timezone

from _common import (
    MissingFileResult,
    NonStandardVaultTextError,
    is_archived_path,
    load_compiled_router,
    parse_frontmatter,
    read_exact_file_content,
)

from _common._artefacts import iter_archived_artefact_paths


from _common._source_access import (
    PUBLIC_BRAIN_CORE_FILES, PUBLIC_BRAIN_CORE_TREES,
    resolve_public_source, resolve_archive_source, SourceAccessError,
)


def _read_exact_file(vault_root, path, *, convert_lossless=False):
    try:
        resolved = resolve_archive_source(vault_root, path)
    except FileNotFoundError:
        return MissingFileResult(path)
    except SourceAccessError as exc:
        return {"error": str(exc)}
    return read_exact_file_content(resolved, convert_lossless=convert_lossless)


def read_vault_file(vault_root, path, *, convert_lossless=False):
    try:
        resolved = resolve_public_source(vault_root, path)
    except FileNotFoundError:
        return MissingFileResult(path)
    except SourceAccessError as exc:
        return {"error": str(exc)}
    return read_exact_file_content(resolved, convert_lossless=convert_lossless)


def resolve_public_vault_file(vault_root, path):
    """Compatibility entry point for canonical public regular-file admission."""
    return resolve_public_source(vault_root, path)


def read_archived_artefact(vault_root, path, *, infer_markdown=False, convert_lossless=False):
    if not is_archived_path(path):
        return {"error": f"'{path}' is not in _Archive/"}
    candidate = path
    if infer_markdown and not path.endswith(".md"):
        try:
            if not (Path(vault_root) / path).exists():
                candidate = f"{path}.md"
        except (OSError, RuntimeError):
            return {"error": "Cannot select the requested archive source"}
    return _read_exact_file(vault_root, candidate, convert_lossless=convert_lossless)


def list_archived_artefacts(router, vault_root, *, excluded_sources=None):
    """Collect admitted archive sources; callers must acknowledge exclusions."""
    vault_root = str(vault_root)
    results = []
    excluded = [] if excluded_sources is None else excluded_sources
    for relative in iter_archived_artefact_paths(vault_root, router, on_excluded=excluded.append):
        absolute = os.path.join(vault_root, relative)
        filename = os.path.basename(relative)
        try:
            resolved = resolve_archive_source(vault_root, relative)
            modified = datetime.fromtimestamp(
                os.path.getmtime(resolved), timezone.utc
            ).isoformat()
        except SourceAccessError:
            excluded.append(relative)
            continue
        except OSError:
            continue
        try:
            content = read_archived_artefact(vault_root, relative, convert_lossless=True)
            if not isinstance(content, str):
                continue
            fields, _ = parse_frontmatter(content)
        except (OSError, UnicodeError, NonStandardVaultTextError):
            fields = {}
        results.append(
            {
                "path": relative,
                "title": os.path.splitext(filename)[0],
                "type": fields.get("type", ""),
                "created": fields.get("created", ""),
                "modified": modified,
                "status": fields.get("status", ""),
                "archiveddate": fields.get("archiveddate", ""),
                "tags": fields.get("tags", []),
                "workspace": fields.get("workspace"),
                "key": fields.get("key", ""),
                "parent": fields.get("parent", ""),
                "location": "archived",
            }
        )

    if excluded_sources is None and excluded:
        raise SourceAccessError(
            f"{len(excluded)} archive source(s) excluded; coverage is incomplete"
        )
    results.sort(
        key=lambda item: (item.get("archiveddate", ""), item["path"].casefold()),
        reverse=True,
    )
    return results


def list_archived_artefacts_from_vault(vault_root):
    router = load_compiled_router(vault_root)
    if "error" in router:
        raise FileNotFoundError(router["error"])
    return list_archived_artefacts(router, vault_root)
