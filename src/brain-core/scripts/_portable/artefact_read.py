"""Portable artefact read semantics without adapter dependencies."""

from __future__ import annotations

import os

from _common import (
    is_archived_path,
    load_compiled_router,
    normalize_artefact_key,
    read_file_content,
    resolve_and_check_bounds,
    resolve_and_validate_folder,
    resolve_artefact_key_entry,
    resolve_artefact_path,
)


_CONFIG_RESOURCE_MAP = {
    "Memories": "memory",
    "Skills": "skill",
    "Styles": "style",
    "Templates": "template",
    "Plugins": "plugin",
}


def _check_vault_containment(vault_root, rel_path):
    try:
        resolve_and_check_bounds(os.path.join(vault_root, rel_path), vault_root)
    except ValueError:
        return {"error": "Path escapes vault root"}
    return None


def _resolve_config_resource(vault_root, name, file_index=None):
    try:
        resolved = resolve_artefact_path(name, vault_root, file_index=file_index)
    except ValueError:
        return None
    if not resolved.startswith("_Config/"):
        return None
    parts = resolved.split("/")
    if len(parts) >= 2:
        return _CONFIG_RESOURCE_MAP.get(parts[1])
    return None


def read_artefact(router, vault_root, name=None):
    """Read a non-archived artefact by canonical key, path, or basename."""

    if not name:
        return {"error": "artefact resource requires a name parameter (relative path or basename)"}

    key = normalize_artefact_key(name)
    if key:
        entry = resolve_artefact_key_entry(router, key)
        if not entry:
            return {"error": f"No artefact matching '{name}'"}
        return read_file_content(vault_root, entry["path"])

    if "/" in name:
        if is_archived_path(name):
            return {
                "error": f"'{name}' is archived. "
                "Use brain_read(resource=\"archive\", name=\"...\") to read archived files."
            }
        return _check_vault_containment(vault_root, name) or read_file_content(
            vault_root,
            name,
        )

    try:
        resolved, _ = resolve_and_validate_folder(vault_root, router, name)
    except ValueError as exc:
        resource = _resolve_config_resource(vault_root, name)
        if resource:
            return {
                "error": f"'{name}' is in _Config/, not an artefact folder. "
                f"Use brain_read(resource=\"{resource}\") instead."
            }
        return {"error": str(exc)}
    return read_file_content(vault_root, resolved)


def read_from_vault(vault_root, name):
    router = load_compiled_router(vault_root)
    if "error" in router:
        return router
    return read_artefact(router, str(vault_root), name)
