#!/usr/bin/env python3
"""
shape_presentation.py — Create and preview presentation decks.

Creates a Marp-based presentation artefact from the template, then launches
a live-preview browser window via `marp --preview` and renders a PDF into
`_Assets/Generated/Presentations/`. The agent iteratively edits the markdown
while the user watches the preview update in real time.

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

from _common import (
    coerce_bool,
    find_vault_root,
    resolve_and_check_bounds,
    safe_write,
    slug_to_title,
    substitute_template_vars,
    title_to_filename,
)


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


def _merge_sub_result(result, sub_result):
    """Merge a sub-operation result into the main result dict.

    Copies all keys except ``status`` (which is promoted to ``partial``
    when the sub-result is partial) and ``warning`` (which is appended
    with ``; `` separator rather than overwritten).
    """
    if sub_result.get("status") == "partial":
        result["status"] = "partial"
    for key, value in sub_result.items():
        if key == "status":
            continue
        if key == "warning":
            existing = result.get("warning")
            result["warning"] = value if not existing else f"{existing}; {value}"
            continue
        result[key] = value


def _render_pdf(vault_root, markdown_abs, theme_path):
    """Render a presentation markdown file to PDF via Marp."""
    output_name = os.path.splitext(os.path.basename(markdown_abs))[0] + ".pdf"
    pdf_rel = os.path.join("_Assets", "Generated", "Presentations", output_name)
    pdf_abs = os.path.join(vault_root, pdf_rel)
    resolve_and_check_bounds(pdf_abs, vault_root)
    os.makedirs(os.path.dirname(pdf_abs), exist_ok=True)

    cmd = ["marp", markdown_abs]
    if theme_path:
        cmd.extend(["--theme", theme_path])
    cmd.extend(["-o", pdf_abs])

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        return {
            "status": "partial",
            "rendered": False,
            "marp_missing": True,
            "warning": "marp not installed; created markdown artefact only",
        }

    if proc.returncode != 0:
        try:
            os.unlink(pdf_abs)
        except FileNotFoundError:
            pass
        stderr = (proc.stderr or "").strip()
        return {
            "status": "partial",
            "rendered": False,
            "warning": f"marp render failed: {stderr or 'unknown error'}",
        }

    return {
        "status": "ok",
        "rendered": True,
        "pdf_path": pdf_rel,
    }


def _launch_preview(markdown_abs, theme_path):
    """Launch Marp live preview for a presentation markdown file."""
    cmd = ["marp", "--preview"]
    if theme_path:
        cmd.extend(["--theme", theme_path])
    cmd.append(markdown_abs)

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return {
            "status": "partial",
            "warning": "marp not installed; live preview unavailable",
        }

    return {
        "status": "ok",
        "preview_pid": proc.pid,
    }


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def shape(vault_root, params):
    """Create a presentation artefact, render PDF output, and launch preview.

    Args:
        vault_root: Absolute path to the vault root.
        params: Dict with keys:
            source — relative path to the artefact being presented
            slug   — deck name (lowercase-hyphenated)
            render — optional bool; render PDF output (default true)
            preview — optional bool; launch Marp preview (default true)

    Returns:
        Dict with status, path, and optionally pdf_path / preview_pid.
    """
    vault_root = str(vault_root)

    if not params or "source" not in params or "slug" not in params:
        return {"error": "shape-presentation requires params: {source, slug}"}

    source = params["source"]
    slug = params["slug"]

    source_abs = os.path.join(vault_root, source)
    resolve_and_check_bounds(source_abs, vault_root)
    if not os.path.isfile(source_abs):
        return {"error": f"Source file not found: {source}"}

    now = datetime.now(timezone.utc).astimezone()
    date_prefix = now.strftime("%Y%m%d")
    month_folder = now.strftime("%Y-%m")
    safe_slug = title_to_filename(slug)
    filename = f"{date_prefix}-presentation~{safe_slug}.md"
    rel_path = os.path.join("_Temporal", "Presentations", month_folder, filename)
    abs_path = os.path.join(vault_root, rel_path)

    theme_path = _resolve_theme_path(vault_root)

    created = False
    if not os.path.isfile(abs_path):
        template_content = _read_template(vault_root)
        if template_content is None:
            return {"error": "Presentation template not found"}

        source_stem = os.path.splitext(source)[0]
        source_display = os.path.basename(source)
        content = substitute_template_vars(template_content, {
            "PRESENTATION TITLE": slug_to_title(slug),
            "[[source-artefact|Source document]]": f"[[{source_stem}|{source_display}]]",
        }, _now=now)

        safe_write(abs_path, content, bounds=str(vault_root))
        created = True

    render = coerce_bool(params.get("render"), True)
    preview = coerce_bool(params.get("preview"), True)

    result = {
        "status": "ok",
        "path": rel_path,
        "created": created,
    }
    if theme_path:
        result["theme_path"] = os.path.relpath(theme_path, vault_root)
    if render:
        render_result = _render_pdf(vault_root, abs_path, theme_path)
        _merge_sub_result(result, render_result)
        if render_result.get("marp_missing"):
            preview = False
    else:
        result["rendered"] = False

    if preview:
        _merge_sub_result(result, _launch_preview(abs_path, theme_path))

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    source = None
    slug = None
    vault_arg = None
    render = None
    preview = None

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
        elif arg == "--render":
            render = True
            i += 1
        elif arg == "--no-render":
            render = False
            i += 1
        elif arg == "--preview":
            preview = True
            i += 1
        elif arg == "--no-preview":
            preview = False
            i += 1
        else:
            i += 1

    if not source or not slug:
        print(
            "Usage: shape_presentation.py --source PATH --slug NAME "
            "[--no-render] [--no-preview] [--vault PATH]",
            file=sys.stderr,
        )
        sys.exit(1)

    vault_root = str(find_vault_root(vault_arg))
    params = {"source": source, "slug": slug}
    if render is not None:
        params["render"] = render
    if preview is not None:
        params["preview"] = preview
    result = shape(vault_root, params)

    if "error" in result:
        print(f"Error: {result['error']}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
