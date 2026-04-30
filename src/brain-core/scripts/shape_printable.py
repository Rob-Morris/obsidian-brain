#!/usr/bin/env python3
"""
shape_printable.py — Create printable documents and render PDF output.

Creates a printable markdown artefact from the template, then renders a PDF
into `_Assets/Generated/Printables/` using pandoc with a LaTeX header stack.
The optional keep-heading-with-next header reduces orphaned headings at page
breaks by reserving vertical space before new sections.

Usage (via MCP):
    brain_action("shape-printable", params={"source": "path/to/source.md", "slug": "my-brief"})

Usage (CLI):
    python3 shape_printable.py --source "path/to/source.md" --slug "my-brief" --vault /path/to/vault
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

from _common import (
    coerce_bool,
    find_vault_root,
    parse_frontmatter,
    resolve_and_check_bounds,
    safe_write,
    slug_to_title,
    substitute_template_vars,
    title_to_filename,
)


_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
_TOOL_ENV_VARS = {
    "pandoc": "BRAIN_PANDOC_PATH",
    "xelatex": "BRAIN_XELATEX_PATH",
    "lualatex": "BRAIN_LUALATEX_PATH",
    "pdflatex": "BRAIN_PDFLATEX_PATH",
}


def _read_template(vault_root):
    """Read the printable template from the artefact library."""
    paths = [
        os.path.join(vault_root, "_Config", "Templates", "Temporal", "Printables.md"),
        os.path.join(vault_root, ".brain-core", "artefact-library", "temporal",
                     "printables", "template.md"),
    ]
    for path in paths:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
    return None


def _resolve_support_path(vault_root, filename):
    """Find a printable support file in the vault or library."""
    paths = [
        os.path.join(vault_root, "_Config", "Skills", "printables", filename),
        os.path.join(vault_root, ".brain-core", "artefact-library", "temporal",
                     "printables", filename),
    ]
    for path in paths:
        if os.path.isfile(path):
            return path
    return None


def _load_tool_paths(vault_root):
    """Load configured local tool paths from the defaults config zone."""
    try:
        import config
    except ImportError:
        return {}

    try:
        cfg = config.load_config(vault_root)
    except Exception:
        return {}

    defaults = cfg.get("defaults", {})
    tool_paths = defaults.get("tool_paths", {})
    return tool_paths if isinstance(tool_paths, dict) else {}


def _wikilinks_to_text(text):
    """Replace Obsidian wikilinks with plain text labels for PDF rendering."""
    def _replace(match):
        target = match.group(1)
        alias = match.group(2)
        if alias:
            return alias
        stem = target.split("#", 1)[0].rsplit("/", 1)[-1]
        if stem.lower().endswith(".md"):
            stem = stem[:-3]
        return stem

    return _WIKILINK_RE.sub(_replace, text)


def _configured_tool_candidates(tool_name, tool_paths):
    """Yield configured explicit path candidates for a tool in priority order."""
    env_var = _TOOL_ENV_VARS.get(tool_name)
    env_value = os.environ.get(env_var, "").strip() if env_var else ""
    if env_value:
        yield (env_value, f"{env_var}")

    config_value = tool_paths.get(tool_name, "")
    if isinstance(config_value, str):
        config_value = config_value.strip()
    else:
        config_value = ""
    if config_value:
        yield (
            config_value,
            f".brain/local/config.yaml defaults.tool_paths.{tool_name}",
        )


def _resolve_tool_path(tool_name, tool_paths):
    """Resolve a tool using explicit local config first, then host PATH."""
    warnings = []

    for candidate, source in _configured_tool_candidates(tool_name, tool_paths):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved, warnings
        warnings.append(f"{source} not found: {candidate}")

    return shutil.which(tool_name), warnings


def _select_pdf_engine(requested, tool_paths):
    """Select a LaTeX PDF engine, respecting an explicit request when present."""
    warnings = []
    if requested and requested not in {"", "auto"}:
        resolved, engine_warnings = _resolve_tool_path(requested, tool_paths)
        warnings.extend(engine_warnings)
        return requested, resolved, warnings
    for candidate in ("xelatex", "lualatex", "pdflatex"):
        resolved, engine_warnings = _resolve_tool_path(candidate, tool_paths)
        warnings.extend(engine_warnings)
        if resolved:
            return candidate, resolved, warnings
    return None, None, warnings


def _render_pdf(vault_root, markdown_abs, params):
    """Render a printable markdown file to PDF."""
    warnings = []
    tool_paths = _load_tool_paths(vault_root)
    pandoc_path, pandoc_warnings = _resolve_tool_path("pandoc", tool_paths)
    warnings.extend(pandoc_warnings)
    if not pandoc_path:
        return {
            "status": "partial",
            "rendered": False,
            "warning": "; ".join(
                warnings + ["pandoc not installed; created markdown artefact only"]
            ),
        }

    with open(markdown_abs, "r", encoding="utf-8") as f:
        content = f.read()

    frontmatter, _body = parse_frontmatter(content)
    requested_engine = params.get("pdf_engine") or frontmatter.get("pdf_engine")
    keep_heading_with_next = coerce_bool(
        params.get("keep_heading_with_next"),
        coerce_bool(frontmatter.get("keep_heading_with_next"), True),
    )

    pdf_engine, pdf_engine_path, engine_warnings = _select_pdf_engine(
        requested_engine,
        tool_paths,
    )
    warnings.extend(engine_warnings)
    if pdf_engine_path is None:
        if requested_engine and requested_engine not in {"", "auto"}:
            warning = f"requested PDF engine not installed: {requested_engine}"
        else:
            warning = "no supported PDF engine found (tried xelatex, lualatex, pdflatex)"
        return {
            "status": "partial",
            "rendered": False,
            "keep_heading_with_next": keep_heading_with_next,
            "warning": "; ".join(warnings + [warning]) if warnings else warning,
        }

    output_name = os.path.splitext(os.path.basename(markdown_abs))[0] + ".pdf"
    pdf_rel = os.path.join("_Assets", "Generated", "Printables", output_name)
    pdf_abs = os.path.join(vault_root, pdf_rel)
    resolve_and_check_bounds(pdf_abs, vault_root)
    os.makedirs(os.path.dirname(pdf_abs), exist_ok=True)

    base_header = _resolve_support_path(vault_root, "base.tex")
    if base_header is None:
        warnings.append("printable base header not found; using pandoc defaults")

    keep_headings_header = None
    if keep_heading_with_next:
        keep_headings_header = _resolve_support_path(vault_root, "keep-headings.tex")
        if keep_headings_header is None:
            warnings.append(
                "keep-headings header not found; headings may break across pages"
            )

    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=".md")
        os.close(fd)
        safe_write(tmp_path, _wikilinks_to_text(content))

        cmd = [
            pandoc_path,
            tmp_path,
            "--standalone",
            "--output",
            pdf_abs,
            f"--pdf-engine={pdf_engine_path}",
        ]
        if base_header:
            cmd.extend(["--include-in-header", base_header])
        if keep_headings_header:
            cmd.extend(["--include-in-header", keep_headings_header])

        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            try:
                os.unlink(pdf_abs)
            except FileNotFoundError:
                pass
            stderr = (proc.stderr or "").strip()
            warning = f"pandoc render failed: {stderr or 'unknown error'}"
            return {
                "status": "partial",
                "rendered": False,
                "keep_heading_with_next": keep_heading_with_next,
                "pdf_engine": pdf_engine,
                "warning": warning,
            }
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    result = {
        "status": "ok",
        "rendered": True,
        "pdf_path": pdf_rel,
        "pdf_engine": pdf_engine,
        "keep_heading_with_next": keep_heading_with_next,
    }
    if warnings:
        result["warning"] = "; ".join(warnings)
    return result


def shape(vault_root, params):
    """Create a printable artefact and optionally render it to PDF."""
    vault_root = str(vault_root)

    if not params or "source" not in params or "slug" not in params:
        return {"error": "shape-printable requires params: {source, slug}"}

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
    filename = f"{date_prefix}-printable~{safe_slug}.md"
    rel_path = os.path.join("_Temporal", "Printables", month_folder, filename)
    abs_path = os.path.join(vault_root, rel_path)

    created = False
    if not os.path.isfile(abs_path):
        template_content = _read_template(vault_root)
        if template_content is None:
            return {"error": "Printable template not found"}

        source_stem = os.path.splitext(source)[0]
        source_display = os.path.basename(source)
        content = substitute_template_vars(template_content, {
            "PRINTABLE TITLE": slug_to_title(slug),
            "[[source-artefact|Source document]]": f"[[{source_stem}|{source_display}]]",
        }, _now=now)

        safe_write(abs_path, content, bounds=str(vault_root))
        created = True

    result = {
        "status": "ok",
        "path": rel_path,
        "created": created,
    }

    render = coerce_bool(params.get("render"), True)
    if not render:
        result["rendered"] = False
        return result

    render_result = _render_pdf(vault_root, abs_path, params)
    if render_result.get("status") == "partial":
        result["status"] = "partial"
    for key, value in render_result.items():
        if key != "status":
            result[key] = value
    return result


def main():
    source = None
    slug = None
    vault_arg = None
    render = None
    keep_heading_with_next = None
    pdf_engine = None

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
        elif arg == "--pdf-engine" and i + 1 < len(sys.argv):
            pdf_engine = sys.argv[i + 1]
            i += 2
        elif arg == "--render":
            render = True
            i += 1
        elif arg == "--no-render":
            render = False
            i += 1
        elif arg == "--keep-heading-with-next":
            keep_heading_with_next = True
            i += 1
        elif arg == "--no-keep-heading-with-next":
            keep_heading_with_next = False
            i += 1
        else:
            i += 1

    if not source or not slug:
        print(
            "Usage: shape_printable.py --source PATH --slug NAME "
            "[--no-render] [--pdf-engine ENGINE] "
            "[--keep-heading-with-next|--no-keep-heading-with-next] "
            "[--vault PATH]",
            file=sys.stderr,
        )
        sys.exit(1)

    params = {"source": source, "slug": slug}
    if render is not None:
        params["render"] = render
    if keep_heading_with_next is not None:
        params["keep_heading_with_next"] = keep_heading_with_next
    if pdf_engine is not None:
        params["pdf_engine"] = pdf_engine

    vault_root = str(find_vault_root(vault_arg))
    result = shape(vault_root, params)

    if "error" in result:
        print(f"Error: {result['error']}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
