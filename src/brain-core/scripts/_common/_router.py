"""Compiled router loading and artefact path validation helpers."""

import json
import os
import re

from ._wikilinks import resolve_artefact_path


COMPILED_ROUTER_REL = os.path.join(".brain", "local", "compiled-router.json")


def load_compiled_router(vault_root):
    """Load compiled router JSON. Returns dict or error dict."""
    router_path = os.path.join(str(vault_root), COMPILED_ROUTER_REL)
    if not os.path.isfile(router_path):
        return {"error": f"Compiled router not found at {COMPILED_ROUTER_REL}. Run compile_router.py first."}
    try:
        with open(router_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return {"error": f"Failed to read compiled router: {e}"}


_STRUCTURAL_PLACEHOLDERS = [
    ("yyyymmdd", r"\d{8}"),
    ("yyyy-mm-dd", r"\d{4}-\d{2}-\d{2}"),
    ("yyyy", r"\d{4}"),
    ("ddd", r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)"),
    ("mm", r"\d{2}"),
    ("dd", r"\d{2}"),
    ("{sourcedoctype}", r"[a-z]+(?:-[a-z]+)*"),
]
_TITLE_PLACEHOLDERS = ("{Title}", "{name}", "{slug}")


def _build_pattern_regex(pattern, capture_title=False):
    """Translate a naming pattern into an anchored regex string.

    When capture_title is True, the first title-like placeholder becomes a
    capture group and any subsequent ones are non-capturing.
    """
    result = ""
    capture_emitted = False
    i = 0
    while i < len(pattern):
        matched = False
        for placeholder, regex in _STRUCTURAL_PLACEHOLDERS:
            if pattern.startswith(placeholder, i):
                result += regex
                i += len(placeholder)
                matched = True
                break
        if matched:
            continue
        for placeholder in _TITLE_PLACEHOLDERS:
            if pattern.startswith(placeholder, i):
                if capture_title and not capture_emitted:
                    result += r"(.+)"
                    capture_emitted = True
                else:
                    result += r".+"
                i += len(placeholder)
                matched = True
                break
        if not matched:
            result += re.escape(pattern[i])
            i += 1
    return result


def naming_pattern_to_regex(pattern):
    """Convert a naming pattern string to a compiled regex, or None if pattern is None."""
    if pattern is None:
        return None
    try:
        return re.compile(r"\A" + _build_pattern_regex(pattern) + r"\Z")
    except re.error:
        return None


def extract_title_from_naming_pattern(pattern, filename):
    """Extract the title/slug portion from a filename using a naming pattern.

    Given a pattern like 'yyyymmdd-research~{Title}.md' and a filename stem like
    '20260413-research~Demo Prep', returns 'Demo Prep'. Returns None if the
    pattern is None or the filename does not match the pattern.

    The filename argument may be a stem (no .md) or a full filename — the
    pattern's .md suffix is treated as optional so stems work.
    """
    if pattern is None:
        return None

    body = _build_pattern_regex(pattern, capture_title=True)
    md_suffix = re.escape(".md")
    if body.endswith(md_suffix):
        body = body[:-len(md_suffix)] + r"(?:\.md)?"

    try:
        regex = re.compile(r"\A" + body + r"\Z")
    except re.error:
        return None

    m = regex.match(filename)
    if not m or not m.groups():
        return None
    return m.group(1)


def validate_artefact_folder(vault_root, router, path):
    """Validate path belongs to a known, configured type folder."""
    vault_root = str(vault_root)

    for art in router.get("artefacts", []):
        art_path = art["path"]
        if path.startswith(art_path + os.sep) or path.startswith(art_path + "/"):
            if not art.get("configured"):
                raise ValueError(
                    f"Path '{path}' belongs to unconfigured type '{art['key']}'. "
                    f"Create a taxonomy file first."
                )
            return art

    known_paths = [a["path"] for a in router.get("artefacts", [])]
    raise ValueError(
        f"Path '{path}' does not belong to any known artefact folder. "
        f"Known: {', '.join(known_paths)}"
    )


def resolve_and_validate_folder(vault_root, router, path):
    """Validate path belongs to a known artefact folder, falling back to basename resolution."""
    if not path.endswith(".md"):
        path += ".md"
    try:
        art = validate_artefact_folder(vault_root, router, path)
        return path, art
    except ValueError:
        resolved = resolve_artefact_path(path, vault_root)
        art = validate_artefact_folder(vault_root, router, resolved)
        return resolved, art


def validate_artefact_naming(artefact, path):
    """Validate filename matches the type's naming pattern."""
    naming = artefact.get("naming")
    if naming and naming.get("pattern"):
        regex = naming_pattern_to_regex(naming["pattern"])
        if regex:
            filename = os.path.basename(path)
            if not regex.match(filename):
                raise ValueError(
                    f"Filename '{filename}' does not match expected pattern "
                    f"'{naming['pattern']}' for type '{artefact['key']}'"
                )


def validate_artefact_path(vault_root, router, path):
    """Validate folder membership and naming pattern."""
    art = validate_artefact_folder(vault_root, router, path)
    validate_artefact_naming(art, path)
    return art
