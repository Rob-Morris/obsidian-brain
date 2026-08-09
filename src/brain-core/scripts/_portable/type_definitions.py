"""Portable exact artefact-type and template views."""

from __future__ import annotations

from _common import (
    load_compiled_router,
    markdown_rel_path,
    match_artefact,
    read_file_content,
)


def list_types(router, query=None):
    resources = [dict(item) for item in router.get("artefacts", ())]
    if query is None:
        return resources
    folded = query.casefold()
    return [
        item
        for item in resources
        if folded in item.get("key", "").casefold()
        or folded in item.get("frontmatter_type", "").casefold()
    ]


def _find_type_exact(router, type_key):
    matches = [
        item for item in router.get("artefacts", ()) if item.get("key") == type_key
    ]
    if not matches:
        return {"error": f"No artefact type with key '{type_key}'"}
    if len(matches) != 1:
        raise ValueError(
            f"Artefact type key must match exactly once; found {len(matches)}: "
            f"{type_key}"
        )
    metadata = dict(matches[0])
    if metadata.get("template_file"):
        metadata["template_file"] = markdown_rel_path(metadata["template_file"])
    return metadata


def read_type_exact(router, vault_root, type_key):
    metadata = _find_type_exact(router, type_key)
    if isinstance(metadata, dict) and "error" in metadata:
        return metadata
    taxonomy_path = metadata.get("taxonomy_file")
    definition = (
        read_file_content(vault_root, taxonomy_path) if taxonomy_path else None
    )
    return metadata, definition


def list_templates(router, query=None, *, resolve_paths=False):
    resources = []
    for artefact_type in router.get("artefacts", ()):
        template_path = artefact_type.get("template_file")
        if not template_path:
            continue
        type_key = artefact_type.get("key", "")
        if query is not None and query.casefold() not in type_key.casefold():
            continue
        resources.append(
            {
                "name": type_key,
                "type": artefact_type.get(
                    "frontmatter_type",
                    artefact_type.get("type", ""),
                ),
                "template_file": (
                    markdown_rel_path(template_path)
                    if resolve_paths
                    else template_path
                ),
            }
        )
    return resources


def read_template_exact(router, vault_root, type_key):
    metadata = _find_type_exact(router, type_key)
    if isinstance(metadata, dict) and "error" in metadata:
        return metadata
    template_path = metadata.get("template_file")
    if not template_path:
        return {"error": f"Artefact type '{type_key}' has no template file"}
    return metadata, read_file_content(vault_root, template_path)


def read_type_compatible(router, type_reference):
    match = match_artefact(router.get("artefacts", ()), type_reference)
    if not match:
        return {"error": f"No artefact matching '{type_reference}'"}
    return [match]


def read_template_compatible(router, vault_root, type_reference):
    match = match_artefact(router.get("artefacts", ()), type_reference)
    if not match:
        return {"error": f"No artefact matching '{type_reference}'"}
    if not match.get("template_file"):
        return {"error": f"Artefact '{type_reference}' has no template file"}
    return read_file_content(vault_root, match["template_file"])


def _load(vault_root):
    router = load_compiled_router(vault_root)
    if "error" in router:
        raise FileNotFoundError(router["error"])
    return router


def list_types_from_vault(vault_root, query=None):
    return list_types(_load(vault_root), query)


def read_type_exact_from_vault(vault_root, type_key):
    return read_type_exact(_load(vault_root), vault_root, type_key)


def list_templates_from_vault(vault_root, query=None):
    return list_templates(_load(vault_root), query, resolve_paths=True)


def read_template_exact_from_vault(vault_root, type_key):
    return read_template_exact(_load(vault_root), vault_root, type_key)
