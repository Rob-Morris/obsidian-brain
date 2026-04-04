#!/usr/bin/env python3
"""
create.py — Create a new vault artefact.

Resolves type from the compiled router, reads the template, generates a
filename from the naming pattern, and writes the file with frontmatter.

Usage:
    python3 create.py --type idea --title "My Idea"
    python3 create.py --type idea --title "My Idea" --body "Content here"
    python3 create.py --type idea --title "My Idea" --vault /path/to/vault --json
"""

import json
import os
import sys
from datetime import datetime, timezone

from _common import (
    find_duplicate_basenames,
    find_vault_root,
    match_artefact,
    parse_frontmatter,
    resolve_body_file,
    safe_write,
    serialize_frontmatter,
    substitute_template_vars,
    title_to_filename,
    title_to_slug,
    unique_filename,
)
from read import read_file_content


# ---------------------------------------------------------------------------
# Naming pattern resolution
# ---------------------------------------------------------------------------

def resolve_naming_pattern(pattern, title, _now=None):
    """Resolve a naming pattern to a filename using the given title and today's date.

    Placeholders:
      {slug}, {name}, {Title}  — title_to_filename(title)
      yyyymmdd        — today as YYYYMMDD
      yyyy-mm-dd      — today as YYYY-MM-DD
      yyyy            — four-digit year
      mm              — two-digit month
      dd              — two-digit day
      ddd             — three-letter weekday (Mon, Tue, ...)
    """
    now = _now if _now is not None else datetime.now(timezone.utc).astimezone()
    safe_title = title_to_filename(title)

    # Order matters: longer placeholders first to avoid partial matches
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


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def create_artefact(vault_root, router, type_key, title, body="", frontmatter_overrides=None, parent=None, template_vars=None):
    """Create a new artefact. Returns {"path": relative_path, "type": ..., "title": ...}.

    Args:
        vault_root: Absolute path to the vault root.
        router: Compiled router dict.
        type_key: Artefact type key (e.g. "idea") or full type (e.g. "living/idea").
        title: Human-readable title, used for filename generation.
        body: Markdown body content (optional, template body used if empty).
        frontmatter_overrides: Optional dict of frontmatter field overrides.
        parent: Optional project subfolder name for living types (e.g. "Brain").
                Places the artefact in {Type}/{parent}/ instead of {Type}/.
        template_vars: Optional dict of placeholder→value substitutions applied
                to the template body (e.g. {"SOURCE_TYPE": "designs"}).
                ``{{date:FORMAT}}`` placeholders are always substituted when the
                template body is used.

    Returns:
        Dict with path, type, and title.

    Raises:
        ValueError: If type is not found, not configured, or file already exists.
    """
    vault_root = str(vault_root)

    # 1. Resolve type
    artefact = resolve_type(router, type_key)

    # 2. Read template (base frontmatter + body)
    template_fields, template_body = _read_template(vault_root, artefact)

    # 3. Capture now once for consistent filename, folder, and timestamps
    now = datetime.now(timezone.utc).astimezone()
    now_iso = now.isoformat()

    # 4. Generate filename
    pattern = artefact.get("naming", {}).get("pattern") if artefact.get("naming") else None
    if pattern:
        filename = resolve_naming_pattern(pattern, title, _now=now)
    else:
        filename = title_to_filename(title) + ".md"

    # 5. Resolve folder
    folder = resolve_folder(artefact, parent=parent, _now=now)

    # 6. Merge frontmatter: template → overrides → force type → timestamps
    fields = dict(template_fields)
    if frontmatter_overrides:
        fields.update(frontmatter_overrides)
    if artefact.get("frontmatter") and artefact["frontmatter"].get("type"):
        fields["type"] = artefact["frontmatter"]["type"]
    if "created" not in fields:
        fields["created"] = now_iso
    if "modified" not in fields:
        fields["modified"] = now_iso

    # 7. Determine body and substitute template variables
    final_body = body if body else template_body
    if not body and final_body:
        final_body = substitute_template_vars(final_body, template_vars, _now=now)

    # 8. Disambiguate basename collisions
    basename_stem = os.path.splitext(filename)[0]

    # Same-folder: append random 3-char suffix to make filename unique
    abs_folder = os.path.join(vault_root, folder)
    os.makedirs(abs_folder, exist_ok=True)
    filename = unique_filename(abs_folder, basename_stem)

    # Cross-folder: append type key if original basename exists elsewhere
    duplicates = find_duplicate_basenames(vault_root, basename_stem, limit=1)
    folder_prefix = os.path.join(folder, "")
    if duplicates and not any(d.startswith(folder_prefix) for d in duplicates):
        current_stem = os.path.splitext(filename)[0]
        filename = f"{current_stem} ({artefact['key']}).md"

    # 9. Write
    rel_path = os.path.join(folder, filename)
    abs_path = os.path.join(vault_root, rel_path)
    content = serialize_frontmatter(fields, body=final_body)
    safe_write(abs_path, content, bounds=vault_root, exclusive=True)

    return {"path": rel_path, "type": artefact["type"], "title": title}


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


def _read_template(vault_root, artefact):
    """Read and parse the template file for an artefact type."""
    template_ref = artefact.get("template_file")
    if not template_ref:
        return {}, ""

    content = read_file_content(vault_root, template_ref)
    if content.startswith("Error:"):
        return {}, ""

    return parse_frontmatter(content)


def resolve_folder(artefact, parent=None, _now=None):
    """Resolve the target folder for a new artefact.

    Living types: use artefact["path"], optionally with a parent subfolder.
    Temporal types: append yyyy-mm/ subfolder (parent ignored).
    """
    base_path = artefact["path"]
    if artefact.get("classification") == "temporal":
        now = _now if _now is not None else datetime.now(timezone.utc).astimezone()
        month_folder = now.strftime("%Y-%m")
        return os.path.join(base_path, month_folder)
    if parent:
        return os.path.join(base_path, parent)
    return base_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    type_key = None
    title = None
    body = ""
    body_file_path = ""
    vault_arg = None
    parent = None
    json_mode = False

    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--type" and i + 1 < len(sys.argv):
            type_key = sys.argv[i + 1]
            i += 2
        elif arg == "--title" and i + 1 < len(sys.argv):
            title = sys.argv[i + 1]
            i += 2
        elif arg == "--body" and i + 1 < len(sys.argv):
            body = sys.argv[i + 1]
            i += 2
        elif arg == "--body-file" and i + 1 < len(sys.argv):
            body_file_path = sys.argv[i + 1]
            i += 2
        elif arg == "--vault" and i + 1 < len(sys.argv):
            vault_arg = sys.argv[i + 1]
            i += 2
        elif arg == "--parent" and i + 1 < len(sys.argv):
            parent = sys.argv[i + 1]
            i += 2
        elif arg == "--json":
            json_mode = True
            i += 1
        else:
            i += 1

    if not type_key or not title:
        print(
            'Usage: create.py --type TYPE --title TITLE [--body BODY] [--body-file PATH] [--parent NAME] [--vault PATH] [--json]',
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        body, _ = resolve_body_file(body, body_file_path)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    vault_root = str(find_vault_root(vault_arg))

    # Load router
    from check import load_router
    router = load_router(vault_root)
    if "error" in router:
        if json_mode:
            print(json.dumps(router))
        else:
            print(f"Error: {router['error']}", file=sys.stderr)
        sys.exit(1)

    try:
        result = create_artefact(vault_root, router, type_key, title, body=body, parent=parent)
    except ValueError as e:
        if json_mode:
            print(json.dumps({"error": str(e)}))
        else:
            print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if json_mode:
        print(json.dumps(result, indent=2))
    else:
        print(f"Created {result['path']}", file=sys.stderr)


if __name__ == "__main__":
    main()
