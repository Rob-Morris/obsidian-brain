#!/usr/bin/env python3
"""
start_shaping.py — Bootstrap a shaping session for an existing artefact.

Resolves the target artefact, sets its status to ``shaping`` (if the type
has a status field), creates a shaping transcript from the template, and
links the transcript back to the source artefact.

Usage (via MCP):
    brain_action("start-shaping", {target: "Designs/My Design.md"})
    brain_action("start-shaping", {target: "My Design", title: "Custom Title"})

Usage (CLI):
    python3 start_shaping.py --target "Designs/My Design.md" --vault /path/to/vault
"""

import json
import os
import sys
from datetime import datetime, timezone

from _common import (
    find_vault_root,
    match_artefact,
    parse_frontmatter,
    resolve_artefact_path,
    safe_write,
    serialize_frontmatter,
    title_to_filename,
)
from read import read_file_content


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def start_shaping(vault_root, router, params):
    """Bootstrap a shaping session for an existing artefact.

    Args:
        vault_root: Absolute path to the vault root.
        router: Compiled router dict.
        params: Dict with keys:
            target — path or basename of artefact to shape (required)
            title  — override transcript title (optional; defaults to artefact title)

    Returns:
        Dict with status, target_path, transcript_path, set_status.
    """
    vault_root = str(vault_root)

    if not params or "target" not in params:
        return {"error": "start-shaping requires params: {target}"}

    target = params["target"]
    if not target or not target.strip():
        return {"error": "target must be a non-empty string"}

    # 1. Resolve target to an existing file
    try:
        rel_path = resolve_artefact_path(target, vault_root)
    except ValueError as e:
        return {"error": str(e)}

    abs_path = os.path.join(vault_root, rel_path)

    # 2. Read target content and frontmatter
    content = read_file_content(vault_root, rel_path)
    if content.startswith("Error:"):
        return {"error": f"Cannot read target: {content}"}

    fields, body = parse_frontmatter(content)

    # 3. Derive title from the artefact (filename stem, or override)
    artefact_title = os.path.splitext(os.path.basename(rel_path))[0]
    # Strip dated prefix+type prefix if present (e.g. "20260307-shaping-transcript~Title" → "Title")
    if "~" in artefact_title:
        artefact_title = artefact_title.split("~", 1)[1]
    transcript_title = params.get("title") or artefact_title

    # 4. Check if the artefact's type has a status field → set to shaping
    set_status = False
    artefact_type = fields.get("type", "")
    artefacts = router.get("artefacts", [])
    matched = match_artefact(artefacts, artefact_type) if artefact_type else None

    if matched and matched.get("frontmatter"):
        status_enum = matched["frontmatter"].get("status_enum")
        if status_enum and "shaping" in status_enum:
            current_status = fields.get("status", "")
            if current_status != "shaping":
                fields["status"] = "shaping"
                fields["modified"] = datetime.now(timezone.utc).astimezone().isoformat()
                updated_content = serialize_frontmatter(fields, body=body)
                safe_write(abs_path, updated_content, bounds=vault_root)
                set_status = True

    # 5. Generate transcript filename and path
    now = datetime.now(timezone.utc).astimezone()
    date_prefix = now.strftime("%Y%m%d")
    month_folder = now.strftime("%Y-%m")
    safe_title = title_to_filename(transcript_title)
    transcript_filename = f"{date_prefix}-shaping-transcript~{safe_title}.md"
    transcript_rel = os.path.join(
        "_Temporal", "Shaping Transcripts", month_folder, transcript_filename
    )
    transcript_abs = os.path.join(vault_root, transcript_rel)

    # 6. Create transcript from template
    template_content = _read_transcript_template(vault_root)
    if template_content is None:
        return {"error": "Shaping transcript template not found"}

    # Strip .md and build wikilink to source
    source_stem = os.path.splitext(rel_path)[0]
    source_display = os.path.splitext(os.path.basename(rel_path))[0]

    transcript_content = template_content
    transcript_content = transcript_content.replace(
        "SOURCE_DOC_PATH|SOURCE_DOC_TITLE",
        f"{source_stem}|{source_display}",
    )
    transcript_content = transcript_content.replace("SOURCE_DOC_PATH", source_stem)
    transcript_content = transcript_content.replace("SOURCE_DOC_TITLE", source_display)
    # Replace SOURCE_TYPE tag with the artefact type key
    source_type_tag = matched["key"] if matched else "artefact"
    transcript_content = transcript_content.replace("SOURCE_TYPE", source_type_tag)
    # Fill date placeholder
    transcript_content = transcript_content.replace(
        "{{date:YYYY-MM-DD}}", now.strftime("%Y-%m-%d")
    )

    os.makedirs(os.path.dirname(transcript_abs), exist_ok=True)
    safe_write(transcript_abs, transcript_content, bounds=vault_root)

    # 7. Add transcript link to source artefact
    transcript_stem = os.path.splitext(transcript_rel)[0]
    transcript_display = os.path.splitext(transcript_filename)[0]
    _add_transcript_link(abs_path, vault_root, transcript_stem, transcript_display)

    return {
        "status": "ok",
        "target_path": rel_path,
        "transcript_path": transcript_rel,
        "set_status": set_status,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_transcript_template(vault_root):
    """Read the shaping transcript template."""
    paths = [
        os.path.join(vault_root, "_Config", "Templates", "Temporal",
                     "Shaping Transcripts.md"),
        os.path.join(vault_root, ".brain-core", "artefact-library", "temporal",
                     "shaping-transcripts", "template.md"),
    ]
    for path in paths:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
    return None


def _add_transcript_link(abs_path, vault_root, transcript_stem, transcript_display):
    """Add or update the **Transcripts:** line on the source artefact."""
    with open(abs_path, "r", encoding="utf-8") as f:
        content = f.read()

    link = f"[[{transcript_stem}|{transcript_display}]]"

    # Look for existing **Transcripts:** line
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("**Transcripts:**"):
            # Avoid duplicate links
            if link not in line:
                lines[i] = line.rstrip() + f" {link}"
            safe_write(abs_path, "\n".join(lines), bounds=vault_root)
            return

    # No existing line — add before the first heading in the body
    fields, body = parse_frontmatter(content)
    if not body.strip():
        # Empty body — just append
        new_content = content.rstrip() + f"\n\n**Transcripts:** {link}\n"
    else:
        # Insert after frontmatter, before first content line
        body_lines = body.split("\n")
        insert_idx = 0
        # Skip leading blank lines
        while insert_idx < len(body_lines) and not body_lines[insert_idx].strip():
            insert_idx += 1
        body_lines.insert(insert_idx, f"**Transcripts:** {link}\n")
        new_content = serialize_frontmatter(fields, body="\n".join(body_lines))

    safe_write(abs_path, new_content, bounds=vault_root)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    target = None
    title = None
    vault_arg = None

    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--target" and i + 1 < len(sys.argv):
            target = sys.argv[i + 1]
            i += 2
        elif arg == "--title" and i + 1 < len(sys.argv):
            title = sys.argv[i + 1]
            i += 2
        elif arg == "--vault" and i + 1 < len(sys.argv):
            vault_arg = sys.argv[i + 1]
            i += 2
        else:
            i += 1

    if not target:
        print(
            "Usage: start_shaping.py --target PATH [--title TITLE] [--vault PATH]",
            file=sys.stderr,
        )
        sys.exit(1)

    vault_root = str(find_vault_root(vault_arg))

    # Load router
    import compile_router
    router = compile_router.load(vault_root)
    if router is None:
        router = compile_router.compile(vault_root)

    params = {"target": target}
    if title:
        params["title"] = title

    result = start_shaping(vault_root, router, params)

    if "error" in result:
        print(f"Error: {result['error']}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
