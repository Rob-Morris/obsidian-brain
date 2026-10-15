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
    resolve_and_check_bounds,
)

from _common._artefacts import iter_archived_artefact_paths


PUBLIC_BRAIN_CORE_FILES = frozenset(
    {"colours.md", "guide.md", "index.md", "md-bootstrap.md", "session-core.md",
     "plugins.md", "triggers.md"}
)
PUBLIC_BRAIN_CORE_TREES = frozenset(
    {"artefact-library", "client-adapters", "skills", "standards"}
)


def _read_exact_file(vault_root, path, *, convert_lossless=False):
    try:
        resolved = resolve_and_check_bounds(
            os.path.join(str(vault_root), path),
            str(vault_root),
        )
    except ValueError:
        return {"error": "Path escapes vault root"}
    if not os.path.isfile(resolved):
        return MissingFileResult(path)
    return read_exact_file_content(resolved, convert_lossless=convert_lossless)


def read_vault_file(vault_root, path, *, convert_lossless=False):
    if is_archived_path(path):
        return {
            "error": (
                f"'{path}' is archived. Use artefact.read with "
                'location="archived" instead.'
            )
        }
    public_error = _public_file_error(vault_root, path)
    if public_error is not None:
        return {"error": public_error}
    return _read_exact_file(vault_root, path, convert_lossless=convert_lossless)


def _public_file_error(vault_root, path):
    """Keep Reader exact-file access inside the documented public namespace."""

    try:
        resolved = Path(
            resolve_and_check_bounds(
                os.path.join(str(vault_root), path),
                str(vault_root),
            )
        )
        relative = resolved.relative_to(Path(vault_root).resolve())
    except (ValueError, OSError):
        return "Path escapes vault root"
    parts = relative.parts
    if not parts:
        return "Path does not identify a public vault file"
    if parts[0] == ".brain-core":
        permitted = (
            len(parts) == 2 and parts[1] in PUBLIC_BRAIN_CORE_FILES
        ) or (
            len(parts) > 2 and parts[1] in PUBLIC_BRAIN_CORE_TREES
        )
        if permitted and not any(part.startswith(".") for part in parts[1:]):
            return None
        return "Path is outside the public Brain Core documentation namespace"
    if any(part.startswith(".") for part in parts):
        return "Path is outside the public vault-file namespace"
    return None


def read_archived_artefact(vault_root, path, *, infer_markdown=False, convert_lossless=False):
    if not is_archived_path(path):
        return {"error": f"'{path}' is not in _Archive/"}
    result = _read_exact_file(vault_root, path, convert_lossless=convert_lossless)
    if not infer_markdown or path.endswith(".md") or not isinstance(
        result, MissingFileResult
    ):
        return result

    markdown_result = _read_exact_file(vault_root, f"{path}.md", convert_lossless=convert_lossless)
    if not isinstance(markdown_result, MissingFileResult):
        return markdown_result
    return markdown_result


def list_archived_artefacts(router, vault_root):
    vault_root = str(vault_root)
    results = []
    for relative in iter_archived_artefact_paths(vault_root, router):
        absolute = os.path.join(vault_root, relative)
        filename = os.path.basename(relative)
        try:
            resolved = resolve_and_check_bounds(absolute, vault_root)
            modified = datetime.fromtimestamp(
                os.path.getmtime(resolved), timezone.utc
            ).isoformat()
        except (ValueError, OSError):
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
