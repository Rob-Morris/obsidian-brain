"""Shared artefact and config-resource helpers."""

import os
from datetime import datetime, timezone

from ._slugs import title_to_filename, title_to_slug
from ._vault import match_artefact


def read_file_content(vault_root, rel_path):
    """Read a vault file's content given a relative path from vault root."""
    original = rel_path
    if not rel_path.endswith(".md"):
        rel_path += ".md"
    abs_path = os.path.join(vault_root, rel_path)
    if not os.path.isfile(abs_path) and original != rel_path:
        abs_path = os.path.join(vault_root, original)
        rel_path = original
    if not os.path.isfile(abs_path):
        return f"Error: file not found: {rel_path}"
    with open(abs_path, "r", encoding="utf-8") as f:
        return f.read()


def resolve_naming_pattern(pattern, title, _now=None):
    """Resolve a naming pattern to a filename using the given title and today's date."""
    now = _now if _now is not None else datetime.now(timezone.utc).astimezone()
    safe_title = title_to_filename(title)

    replacements = [
        ("yyyymmdd", now.strftime("%Y%m%d")),
        ("yyyy-mm-dd", now.strftime("%Y-%m-%d")),
        ("yyyy", now.strftime("%Y")),
        ("ddd", now.strftime("%a")),
        ("mm", now.strftime("%m")),
        ("dd", now.strftime("%d")),
        ("{slug}", safe_title),
        ("{name}", safe_title),
        ("{Title}", safe_title),
    ]

    result = pattern
    for placeholder, value in replacements:
        result = result.replace(placeholder, value)

    return result


def resolve_type(router, type_key):
    """Match type_key against router artefacts by key, full type, or singular form."""
    artefacts = router.get("artefacts", [])
    match = match_artefact(artefacts, type_key)
    if match is None:
        raise ValueError(
            f"Unknown artefact type '{type_key}'. "
            f"Valid types: {', '.join(a['key'] for a in artefacts)}"
        )
    if not match.get("configured"):
        raise ValueError(
            f"Type '{type_key}' exists but is not configured "
            f"(no taxonomy file). Create a taxonomy file first."
        )
    return match


def resolve_folder(artefact, parent=None, _now=None):
    """Resolve the target folder for a new artefact."""
    base_path = artefact["path"]
    if artefact.get("classification") == "temporal":
        now = _now if _now is not None else datetime.now(timezone.utc).astimezone()
        month_folder = now.strftime("%Y-%m")
        return os.path.join(base_path, month_folder)
    if parent:
        return os.path.join(base_path, parent)
    return base_path


def config_resource_rel_path(router, resource, name):
    """Return the relative path for a _Config/ resource."""
    slug = title_to_slug(name)
    if resource == "skill":
        return os.path.join("_Config", "Skills", slug, "SKILL.md")
    if resource == "memory":
        return os.path.join("_Config", "Memories", slug + ".md")
    if resource == "style":
        return os.path.join("_Config", "Styles", slug + ".md")
    if resource == "template":
        artefact = resolve_type(router, name)
        classification = artefact.get("classification", "living")
        subdir = "Living" if classification == "living" else "Temporal"
        return os.path.join("_Config", "Templates", subdir, artefact["folder"] + ".md")
    raise ValueError(f"Unknown config resource: {resource}")
