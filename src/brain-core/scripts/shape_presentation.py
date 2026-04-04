#!/usr/bin/env python3
"""
shape_presentation.py — Create and preview presentation decks.

Creates a Marp-based presentation artefact from the template, then launches
a live-preview browser window via `marp --preview`. The agent iteratively
edits the markdown while the user watches the preview update in real time.

Usage (via MCP):
    brain_action("shape-presentation", {source: "path/to/source.md", slug: "my-deck"})

Usage (CLI):
    python3 shape_presentation.py --source "path/to/source.md" --slug "my-deck" --vault /path/to/vault
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone

from _common import find_vault_root, resolve_and_check_bounds, safe_write, slug_to_title, substitute_template_vars, title_to_filename


# ---------------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------------

def _read_template(vault_root):
    """Read the presentation template from the artefact library."""
    # Try vault-installed template first, then library source
    paths = [
        os.path.join(vault_root, "_Config", "Templates", "Temporal", "Presentations.md"),
        os.path.join(vault_root, ".brain-core", "artefact-library", "temporal",
                     "presentations", "template.md"),
    ]
    for path in paths:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
    return None


def _resolve_theme_path(vault_root):
    """Find the theme CSS file."""
    paths = [
        os.path.join(vault_root, "_Config", "Skills", "presentations", "theme.css"),
        os.path.join(vault_root, ".brain-core", "artefact-library", "temporal",
                     "presentations", "theme.css"),
    ]
    for path in paths:
        if os.path.isfile(path):
            return path
    return None


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def shape(vault_root, params):
    """Create a presentation artefact and launch live preview.

    Args:
        vault_root: Absolute path to the vault root.
        params: Dict with keys:
            source — relative path to the artefact being presented
            slug   — deck name (lowercase-hyphenated)

    Returns:
        Dict with status, path, and optionally preview_pid.
    """
    vault_root = str(vault_root)

    # Validate params
    if not params or "source" not in params or "slug" not in params:
        return {"error": "shape-presentation requires params: {source, slug}"}

    source = params["source"]
    slug = params["slug"]

    # Validate source is within vault and exists
    source_abs = os.path.join(vault_root, source)
    resolve_and_check_bounds(source_abs, vault_root)
    if not os.path.isfile(source_abs):
        return {"error": f"Source file not found: {source}"}

    # Resolve paths
    now = datetime.now(timezone.utc).astimezone()
    date_prefix = now.strftime("%Y%m%d")
    month_folder = now.strftime("%Y-%m")
    safe_slug = title_to_filename(slug)
    filename = f"{date_prefix}-presentation~{safe_slug}.md"
    rel_path = os.path.join("_Temporal", "Presentations", month_folder, filename)
    abs_path = os.path.join(vault_root, rel_path)

    theme_path = _resolve_theme_path(vault_root)

    # Create from template if it doesn't exist
    created = False
    if not os.path.isfile(abs_path):
        template_content = _read_template(vault_root)
        if template_content is None:
            return {"error": "Presentation template not found"}

        # Fill template placeholders
        source_stem = os.path.splitext(source)[0]
        source_display = os.path.basename(source)
        content = substitute_template_vars(template_content, {
            "PRESENTATION TITLE": slug_to_title(slug),
            "[[source-artefact|Source document]]": f"[[{source_stem}|{source_display}]]",
        }, _now=now)

        safe_write(abs_path, content, bounds=str(vault_root))
        created = True

    # Launch live preview
    preview_pid = None
    if theme_path:
        try:
            proc = subprocess.Popen(
                ["marp", "--preview", "--theme", theme_path, abs_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            preview_pid = proc.pid
        except FileNotFoundError:
            # marp not installed — continue without preview
            pass

    result = {
        "status": "ok",
        "path": rel_path,
        "created": created,
    }
    if preview_pid is not None:
        result["preview_pid"] = preview_pid
    if theme_path:
        result["theme_path"] = os.path.relpath(theme_path, vault_root)

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    source = None
    slug = None
    vault_arg = None

    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--source" and i + 1 < len(sys.argv):
            source = sys.argv[i + 1]
            i += 2
        elif arg == "--slug" and i + 1 < len(sys.argv):
            slug = sys.argv[i + 1]
            i += 2
        elif arg == "--vault" and i + 1 < len(sys.argv):
            vault_arg = sys.argv[i + 1]
            i += 2
        else:
            i += 1

    if not source or not slug:
        print(
            'Usage: shape_presentation.py --source PATH --slug NAME [--vault PATH]',
            file=sys.stderr,
        )
        sys.exit(1)

    vault_root = str(find_vault_root(vault_arg))
    result = shape(vault_root, {"source": source, "slug": slug})

    if "error" in result:
        print(f"Error: {result['error']}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
