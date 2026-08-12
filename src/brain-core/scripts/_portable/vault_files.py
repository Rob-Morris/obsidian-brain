"""Portable exact vault-file and archived-artefact reads."""

from __future__ import annotations

import os
from pathlib import Path
from datetime import datetime, timezone

from _common import (
    MissingFileResult,
    is_archived_path,
    load_compiled_router,
    parse_frontmatter,
    resolve_and_check_bounds,
)


def _read_exact_file(vault_root, path):
    try:
        resolved = resolve_and_check_bounds(
            os.path.join(str(vault_root), path),
            str(vault_root),
        )
    except ValueError:
        return {"error": "Path escapes vault root"}
    if not os.path.isfile(resolved):
        return MissingFileResult(path)
    with open(resolved, "r", encoding="utf-8") as handle:
        return handle.read()


def read_vault_file(vault_root, path):
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
    return _read_exact_file(vault_root, path)


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
        public_files = {
            "colours.md",
            "guide.md",
            "index.md",
            "md-bootstrap.md",
            "session-core.md",
        }
        public_trees = {
            "artefact-library",
            "client-adapters",
            "skills",
            "standards",
        }
        permitted = (
            len(parts) == 2 and parts[1] in public_files
        ) or (
            len(parts) > 2 and parts[1] in public_trees
        )
        if permitted and not any(part.startswith(".") for part in parts[1:]):
            return None
        return "Path is outside the public Brain Core documentation namespace"
    if any(part.startswith(".") for part in parts):
        return "Path is outside the public vault-file namespace"
    return None


def read_archived_artefact(vault_root, path, *, infer_markdown=False):
    if not is_archived_path(path):
        return {"error": f"'{path}' is not in _Archive/"}
    result = _read_exact_file(vault_root, path)
    if not infer_markdown or path.endswith(".md") or not isinstance(
        result, MissingFileResult
    ):
        return result

    markdown_result = _read_exact_file(vault_root, f"{path}.md")
    if not isinstance(markdown_result, MissingFileResult):
        return markdown_result
    return markdown_result


def list_archived_artefacts(router, vault_root):
    vault_root = str(vault_root)
    results = []
    seen = set()

    def scan(base_dir):
        if not os.path.isdir(base_dir):
            return
        for dirpath, dirnames, filenames in os.walk(base_dir):
            dirnames[:] = [name for name in dirnames if not name.startswith(".")]
            for filename in filenames:
                if not filename.endswith(".md"):
                    continue
                absolute = os.path.join(dirpath, filename)
                relative = os.path.relpath(absolute, vault_root)
                if relative in seen:
                    continue
                seen.add(relative)
                try:
                    with open(absolute, "r", encoding="utf-8") as handle:
                        fields, _ = parse_frontmatter(handle.read())
                except (OSError, UnicodeError):
                    fields = {}
                results.append(
                    {
                        "path": relative,
                        "title": os.path.splitext(filename)[0],
                        "type": fields.get("type", ""),
                        "created": fields.get("created", ""),
                        "modified": datetime.fromtimestamp(
                            os.path.getmtime(absolute), timezone.utc
                        ).isoformat(),
                        "status": fields.get("status", ""),
                        "archiveddate": fields.get("archiveddate", ""),
                        "tags": fields.get("tags", []),
                        "key": fields.get("key", ""),
                        "parent": fields.get("parent", ""),
                        "location": "archived",
                    }
                )

    scan(os.path.join(vault_root, "_Archive"))
    for artefact in router.get("artefacts", ()):
        artefact_dir = os.path.join(vault_root, artefact["path"])
        if not os.path.isdir(artefact_dir):
            continue
        for entry in os.listdir(artefact_dir):
            if entry == "_Archive":
                scan(os.path.join(artefact_dir, "_Archive"))
            child = os.path.join(artefact_dir, entry)
            if os.path.isdir(child) and not entry.startswith((".", "_", "+")):
                archive = os.path.join(child, "_Archive")
                if os.path.isdir(archive):
                    scan(archive)

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
