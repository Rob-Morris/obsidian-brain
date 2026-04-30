#!/usr/bin/env python3
"""
migrate_to_0_34_0.py — Align legacy release artefacts to the settled release shape.

Normalises three deterministic aspects of pre-Phase-1 release artefacts:

1. Removes the legacy literal `**Project:** [[PROJECT]]` placeholder line.
2. Rebuilds the body structure from `Goal / Gates / Changelog / Sources` to
   `Goal / Acceptance Criteria / Designs In Scope / Release Notes / Sources`.
3. Rehomes and/or renames the file so it matches the status-selected filename
   contract anchored on the canonical `parent:` (or the file's current
   ownership context when the parent does not resolve).

This migration is intentionally conservative: it never infers a new parent.
Releases require a canonical `parent:` (any owning living artefact type), so
the migration halts before making any changes if it finds legacy unparented
releases, listing the offending paths and asking the operator to set
`parent:` explicitly before retrying. If a stored `parent:` does not resolve,
the file stays in its current folder and a warning is emitted.
"""

from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from _common import (
    apply_terminal_status_folder,
    ensure_parent_tag,
    extract_title,
    find_vault_root,
    iter_artefact_paths,
    load_compiled_router,
    normalize_artefact_key,
    parse_frontmatter,
    render_filename_or_default,
    resolve_artefact_key_entry,
    resolve_folder,
    safe_write,
    serialize_frontmatter,
)
from rename import rename_and_update_links


VERSION = "0.34.0"
_RELEASE_FRONTMATTER_TYPE = "living/release"
_KNOWN_SECTIONS = {
    "Goal",
    "Acceptance Criteria",
    "Designs In Scope",
    "Release Notes",
    "Sources",
}
_LEGACY_SECTIONS = {"Gates", "Changelog"}


def _release_artefact(router):
    for art in router.get("artefacts", []):
        if art.get("frontmatter_type") == _RELEASE_FRONTMATTER_TYPE:
            return art
    raise ValueError("Compiled router has no configured release artefact.")


def _split_sections(body):
    matches = list(
        re.finditer(
            r"(?ms)^## ([^\n]+?)\s*\n(.*?)(?=^## [^\n]+?\s*\n|\Z)",
            body,
        )
    )
    if not matches:
        return body.strip("\n"), []
    preamble = body[: matches[0].start()].strip("\n")
    sections = [(match.group(1).strip(), match.group(2).strip("\n")) for match in matches]
    return preamble, sections


def _strip_project_placeholder(preamble):
    lines = [line for line in preamble.splitlines() if line.strip() != "**Project:** [[PROJECT]]"]
    return "\n".join(lines).strip("\n")


def _split_table_row(line):
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _default_acceptance_criteria():
    return "| Criterion | Status |\n|---|---|\n|  | pending |"


_WIKILINK_RE = re.compile(r"\[\[[^\[\]\n]+?\]\]")


def _extract_wikilinks(cell):
    return _WIKILINK_RE.findall(cell or "")


def _convert_gates_section(content):
    stripped = content.strip("\n")
    if not stripped:
        return _default_acceptance_criteria(), {}

    lines = stripped.splitlines()
    first_table = next((i for i, line in enumerate(lines) if line.strip().startswith("|")), None)
    if first_table is None or first_table + 1 >= len(lines):
        return stripped, {}

    table_lines = []
    idx = first_table
    while idx < len(lines) and lines[idx].strip().startswith("|"):
        table_lines.append(lines[idx])
        idx += 1

    headers = _split_table_row(table_lines[0])
    if len(headers) < 2 or headers[0].lower() != "gate":
        return stripped, {}

    criteria_lines = ["| Criterion | Status |", "|---|---|"]
    design_to_criteria = {}
    for row in table_lines[2:]:
        cells = _split_table_row(row)
        criterion = cells[0] if len(cells) > 0 else ""
        status = cells[1] if len(cells) > 1 else ""
        criteria_lines.append(f"| {criterion} | {status} |")
        design_cell = cells[2] if len(cells) > 2 else ""
        for design in _extract_wikilinks(design_cell):
            entry = design_to_criteria.setdefault(design, [])
            if criterion and criterion not in entry:
                entry.append(criterion)

    if len(criteria_lines) == 2:
        criteria_lines.append("|  | pending |")

    trailing = "\n".join(lines[idx:]).strip("\n")
    criteria = "\n".join(criteria_lines)
    if trailing:
        criteria = criteria + "\n\n" + trailing
    return criteria, design_to_criteria


def _format_legacy_origin(criteria):
    quoted = [f'"{c}"' for c in criteria if c]
    if not quoted:
        return ""
    if len(quoted) == 1:
        return f" (legacy criterion: {quoted[0]})"
    return f" (legacy criteria: {'; '.join(quoted)})"


def _merge_designs(existing, design_to_criteria):
    if existing and existing.strip():
        return existing.strip("\n")
    if design_to_criteria:
        lines = []
        for design, criteria in design_to_criteria.items():
            origin = _format_legacy_origin(criteria)
            lines.append(f"- {design} — _todo: release role_{origin}")
        return "\n".join(lines)
    return "- "


def _rebuild_release_body(body):
    preamble, raw_sections = _split_sections(body)
    preamble = _strip_project_placeholder(preamble)

    sections = {}
    extras = []
    for heading, content in raw_sections:
        if heading in _KNOWN_SECTIONS | _LEGACY_SECTIONS:
            sections.setdefault(heading, content)
        else:
            extras.append((heading, content))

    goal = sections.get("Goal", "").strip("\n")

    if "Acceptance Criteria" in sections:
        acceptance = sections["Acceptance Criteria"].strip("\n")
        design_to_criteria = {}
    elif "Gates" in sections:
        acceptance, design_to_criteria = _convert_gates_section(sections["Gates"])
    else:
        acceptance = _default_acceptance_criteria()
        design_to_criteria = {}

    designs = _merge_designs(sections.get("Designs In Scope"), design_to_criteria)
    release_notes = (sections.get("Release Notes") or sections.get("Changelog", "")).strip("\n")
    sources = sections.get("Sources", "").strip("\n") or "- "

    parts = []
    if preamble:
        parts.append(preamble)

    ordered = [
        ("Goal", goal),
        ("Acceptance Criteria", acceptance),
        ("Designs In Scope", designs),
        ("Release Notes", release_notes),
        ("Sources", sources),
    ]
    for heading, content in ordered:
        parts.append(f"## {heading}")
        if content:
            parts.append(content)

    for heading, content in extras:
        parts.append(f"## {heading}")
        if content.strip():
            parts.append(content.strip("\n"))

    return "\n\n".join(parts).rstrip() + "\n"


def _derive_title(artefact, fields, rel_path):
    filename = os.path.basename(rel_path)
    stem, _ext = os.path.splitext(filename)
    version = fields.get("version")
    if version and stem.startswith(f"{version} - "):
        return stem[len(version) + 3:]
    return extract_title(artefact.get("naming"), fields, filename) or stem


def _fallback_folder_for_unresolved_parent(rel_path, artefact):
    """Folder to keep the file in when its `parent:` does not resolve.

    Strips a leading ``+Status/`` from the file's current folder so the
    fallback lands in the ownership context, not inside a terminal-status
    folder. Falls back to the artefact base path if the file is at the
    artefact root.
    """
    current_folder = os.path.dirname(rel_path)
    if os.path.basename(current_folder).startswith("+"):
        current_folder = os.path.dirname(current_folder)
    return current_folder or artefact["path"]


def _canonical_release_path(router, artefact, rel_path, fields, title):
    parent_key = normalize_artefact_key(fields.get("parent"))
    if parent_key and resolve_artefact_key_entry(router, parent_key):
        folder = resolve_folder(
            artefact,
            parent=parent_key,
            fields=fields,
            router=router,
        )
    else:
        folder = _fallback_folder_for_unresolved_parent(rel_path, artefact)
    folder = apply_terminal_status_folder(folder, artefact, fields)
    filename = render_filename_or_default(artefact.get("naming"), title, fields)
    return os.path.join(folder, filename)


def _load_release_records(vault_root, artefact):
    """Read every release artefact once; return a list of (rel_path, fields, body)."""
    records = []
    for rel_path in sorted(iter_artefact_paths(vault_root, artefact, include_status_folders=True)):
        abs_path = os.path.join(vault_root, rel_path)
        try:
            with open(abs_path, "r", encoding="utf-8") as handle:
                content = handle.read()
        except OSError:
            continue
        fields, body = parse_frontmatter(content)
        if fields.get("type") != _RELEASE_FRONTMATTER_TYPE:
            continue
        records.append((rel_path, fields, body))
    return records


def backfill_vault(vault_root, router=None, *, dry_run=False):
    """Normalise legacy release artefacts to the settled Phase 1 structure."""
    vault_root = str(vault_root)
    router = router or load_compiled_router(vault_root)
    if "error" in router:
        raise ValueError(router["error"])

    artefact = _release_artefact(router)
    records = _load_release_records(vault_root, artefact)

    unparented = [rel for rel, fields, _ in records if not normalize_artefact_key(fields.get("parent"))]
    if unparented:
        listing = "\n".join(f"  - {path}" for path in unparented)
        raise ValueError(
            "Cannot migrate: the following release artefacts have no `parent:` set.\n"
            "Releases require a canonical parent (any owning living artefact type).\n"
            "Add `parent: <type>/<key>` to each file's frontmatter, then re-run the migration:\n"
            f"{listing}"
        )

    actions = []
    warnings = []
    updated = 0

    for rel_path, fields, body in records:
        abs_path = os.path.join(vault_root, rel_path)
        title = _derive_title(artefact, fields, rel_path)
        file_actions = []

        parent_key = normalize_artefact_key(fields.get("parent"))
        if parent_key and resolve_artefact_key_entry(router, parent_key):
            if ensure_parent_tag(fields):
                file_actions.append(f"added parent tag {parent_key}")
        elif parent_key:
            warnings.append(
                f"{rel_path}: parent {parent_key!r} does not resolve; kept current ownership context"
            )

        new_body = _rebuild_release_body(body)
        if new_body != body:
            file_actions.append("normalised release sections")

        new_rel_path = rel_path
        candidate_path = _canonical_release_path(router, artefact, rel_path, fields, title)
        if candidate_path != rel_path:
            abs_candidate = os.path.join(vault_root, candidate_path)
            if os.path.exists(abs_candidate):
                warnings.append(
                    f"{rel_path}: destination already exists, skipped move to {candidate_path}"
                )
            else:
                new_rel_path = candidate_path
                file_actions.append(f"moved to {candidate_path}")

        if not file_actions:
            continue

        updated += 1
        actions.append({"path": rel_path, "changes": file_actions})
        if dry_run:
            continue

        safe_write(
            abs_path,
            serialize_frontmatter(fields, body=new_body),
            bounds=vault_root,
        )
        if new_rel_path != rel_path:
            rename_and_update_links(vault_root, rel_path, new_rel_path)

    return {
        "status": "skipped" if updated == 0 and not warnings else "ok",
        "updated": updated,
        "warnings": warnings,
        "actions": actions,
        "dry_run": dry_run,
    }


def migrate(vault_root):
    router = load_compiled_router(str(vault_root))
    if "error" in router:
        raise ValueError(router["error"])
    return backfill_vault(vault_root, router=router, dry_run=False)


def main():
    parser = argparse.ArgumentParser(description="Align legacy release artefacts to the v0.34.0 shape.")
    parser.add_argument("--vault", help="Path to vault root (default: auto-detect)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without modifying files")
    args = parser.parse_args()
    vault_root = str(find_vault_root(args.vault))
    router = load_compiled_router(vault_root)
    result = backfill_vault(vault_root, router=router, dry_run=args.dry_run)
    print(result)


if __name__ == "__main__":
    main()
