#!/usr/bin/env python3
"""
migrate_to_0_29_0.py — Backfill timestamps for the frontmatter-backed filename contract.

Walks every artefact in the vault (skipping `_Archive/`, `_Config/`, and
`.brain-core/`) and applies the §5 reconciliation cascade to populate
`created`, `modified`, and any type-specific `date_source` fields that the
new rendering contract requires.

Companion work:

- `migrate_naming.migrate_vault` is run first (RD-8) so legacy filename
  formats are canonicalised before backfill cascades from stable prefixes.
- For ``living/writing`` artefacts without a ``status`` field, the migration
  infers ``published`` when ``publisheddate`` is present and ``draft``
  otherwise — this then drives the selected naming rule.
- Temporal artefacts whose resolved ``created`` falls in a different
  ``yyyy-mm/`` folder than the current location are relocated via the
  shared rename helper, preserving wikilinks.
- Files whose filename no longer matches the rule selected for the
  reconciled state are renamed in place.

Runs automatically via ``upgrade.py`` when crossing the v0.29.0 boundary,
and can be invoked standalone with ``--dry-run`` for a report.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from _common import (
    extract_title,
    find_vault_root,
    is_archived_path,
    load_compiled_router,
    parse_frontmatter,
    reconcile_date_source,
    reconcile_timestamps,
    render_filename,
    resolve_folder,
    safe_write,
    select_rule,
    serialize_frontmatter,
)
from check import find_type_files
from rename import rename_and_update_links
import migrate_naming
import sync_definitions


VERSION = "0.29.0"
TARGET_HANDLERS = {"pre_compile_patch": "patch_pre_compile"}


def _extract_missing_date_source_error(compile_err: str | None) -> tuple[str, str] | None:
    """Parse the v0.29 living taxonomy compile error into (type_key, pattern)."""
    if not compile_err:
        return None
    match = re.search(
        r"Type '([^']+)' rule '([^']+)' has date tokens but no `date_source` declared",
        compile_err,
    )
    if not match:
        return None
    return match.group(1), match.group(2)


def _extract_markdown_section(content: str, heading: str) -> tuple[tuple[int, int], str] | None:
    """Return ``((start, end), body)`` for a level-2 markdown section."""
    match = re.search(
        rf"(?ms)^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)",
        content,
    )
    if not match:
        return None
    return (match.start(1), match.end(1)), match.group(1)


def _extract_taxonomy_date_source(content: str, pattern: str) -> str | None:
    """Read the canonical ``date_source`` for ``pattern`` from taxonomy markdown."""
    section = _extract_markdown_section(content, "Naming")
    if section is None:
        return None
    _, body = section

    simple = re.search(
        rf"(?m)^`{re.escape(pattern)}`[^\n]*date source `([^`]+)`",
        body,
    )
    if simple:
        return simple.group(1)

    lines = body.splitlines()
    for i, line in enumerate(lines):
        if not line.strip().startswith("|"):
            continue
        header_cells = [cell.strip().lower() for cell in line.strip().strip("|").split("|")]
        if "pattern" not in header_cells or "date source" not in header_cells:
            continue
        pattern_idx = header_cells.index("pattern")
        date_idx = header_cells.index("date source")
        start = i + 2 if i + 1 < len(lines) and lines[i + 1].strip().startswith("|") else i + 1
        for row in lines[start:]:
            if not row.strip().startswith("|"):
                break
            cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
            if len(cells) <= max(pattern_idx, date_idx):
                continue
            if cells[pattern_idx].strip("`") != pattern:
                continue
            value = cells[date_idx].strip().strip("`")
            return value or None
        break
    return None


def _extract_frontmatter_fields(content: str) -> set[str]:
    """Extract top-level field names from a taxonomy ``## Frontmatter`` block."""
    match = re.search(
        r"^## Frontmatter\s*\n.*?```ya?ml\s*\n---\s*\n(.*?)---\s*\n```",
        content,
        re.MULTILINE | re.DOTALL,
    )
    if not match:
        return set()
    return set(re.findall(r"^(\w[\w-]*):", match.group(1), re.MULTILINE))


def _patch_rules_table(section_body: str, pattern: str, date_source: str) -> str | None:
    """Fill or add a Date source column for ``pattern`` in a ### Rules table."""
    lines = section_body.splitlines()
    for i, line in enumerate(lines):
        if not line.strip().startswith("|"):
            continue
        header_cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        lowered = [cell.lower() for cell in header_cells]
        if "pattern" not in lowered:
            continue

        pattern_idx = lowered.index("pattern")
        date_idx = lowered.index("date source") if "date source" in lowered else None
        updated = list(lines)
        if date_idx is None:
            header_cells.append("Date source")
            updated[i] = "| " + " | ".join(header_cells) + " |"
            date_idx = len(header_cells) - 1
            if i + 1 < len(lines) and lines[i + 1].strip().startswith("|"):
                sep_cells = [cell.strip() for cell in lines[i + 1].strip().strip("|").split("|")]
                while len(sep_cells) < len(header_cells):
                    sep_cells.append("---")
                updated[i + 1] = "| " + " | ".join(sep_cells[: len(header_cells)]) + " |"
            data_start = i + 2
        else:
            data_start = i + 2 if i + 1 < len(lines) and lines[i + 1].strip().startswith("|") else i + 1

        changed = False
        for j in range(data_start, len(lines)):
            row = lines[j]
            if not row.strip().startswith("|"):
                break
            cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
            if len(cells) <= pattern_idx:
                continue
            if cells[pattern_idx].strip("`") != pattern:
                continue
            while len(cells) <= date_idx:
                cells.append("")
            if cells[date_idx].strip("`") == date_source:
                return section_body
            cells[date_idx] = f"`{date_source}`"
            updated[j] = "| " + " | ".join(cells) + " |"
            changed = True
            break

        if changed:
            return "\n".join(updated)
        return None
    return None


def _patch_blocking_taxonomy(
    local_content: str,
    *,
    pattern: str,
    date_source: str,
    required_field: str | None,
) -> str | None:
    """Inject the missing date_source contract without overwriting custom prose."""
    naming = _extract_markdown_section(local_content, "Naming")
    if naming is None:
        return None
    (start, end), naming_body = naming

    patched_naming = None
    simple = re.search(rf"(?m)^`{re.escape(pattern)}`([^\n]*)$", naming_body)
    if simple:
        line = simple.group(0)
        if "date source" in line.lower():
            patched_naming = naming_body
        else:
            stripped = line.rstrip()
            if stripped.endswith("."):
                replacement = stripped[:-1] + f", date source `{date_source}`."
            else:
                replacement = stripped + f", date source `{date_source}`"
            patched_naming = naming_body[: simple.start()] + replacement + naming_body[simple.end():]
    else:
        patched_naming = _patch_rules_table(naming_body, pattern, date_source)

    if patched_naming is None:
        return None

    patched = local_content[:start] + patched_naming + local_content[end:]
    if not required_field or required_field in {"created", "modified"}:
        return patched

    frontmatter = re.search(
        r"(^## Frontmatter\s*\n.*?```ya?ml\s*\n---\s*\n)(.*?)(---\s*\n```)",
        patched,
        re.MULTILINE | re.DOTALL,
    )
    if not frontmatter:
        return None

    existing = set(re.findall(r"^(\w[\w-]*):", frontmatter.group(2), re.MULTILINE))
    if required_field in existing:
        return patched

    body = frontmatter.group(2)
    if body and not body.endswith("\n"):
        body += "\n"
    body += f"{required_field}:\n"
    return patched[: frontmatter.start(2)] + body + patched[frontmatter.end(2):]


def _snapshot_file(context: dict[str, Any] | None, path: str) -> None:
    """Ask the upgrade runner to snapshot a file before mutating it."""
    if not context:
        return
    snapshot = context.get("snapshot_file")
    if callable(snapshot):
        snapshot(path)


def _remediate_blocking_taxonomy(
    vault_root: str,
    *,
    type_key: str,
    pattern: str,
    tracking: dict,
    context: dict[str, Any] | None,
    result: dict[str, Any],
) -> bool:
    """Apply the smallest safe change that unblocks the v0.29 compile gate."""
    full_type_key = f"living/{type_key}"
    target_rel = os.path.join("_Config", "Taxonomy", "Living", f"{type_key}.md")
    target_path = os.path.join(vault_root, target_rel)
    upstream_path = os.path.join(
        vault_root, ".brain-core", "artefact-library", "living", type_key, "taxonomy.md",
    )
    if not os.path.isfile(upstream_path) or not os.path.isfile(target_path):
        result["warnings"].append({
            "type": full_type_key,
            "target": target_rel,
            "reason": "no canonical library taxonomy available for remediation",
        })
        return False

    type_tracking = tracking["installed"].get(full_type_key, {})
    installed_entry = type_tracking.get("files", {}).get("taxonomy")
    status = sync_definitions.compute_file_status(upstream_path, installed_entry, target_path)
    action = status["action"]

    if action in {"update", "baseline"}:
        _snapshot_file(context, target_path)
        if action == "update":
            shutil.copy2(upstream_path, target_path)

        tracking_entry = tracking["installed"].setdefault(
            full_type_key,
            {
                "brain_core_version": sync_definitions.read_version(vault_root) or "unknown",
                "installed_at": datetime.now(timezone.utc).isoformat(),
                "files": {},
            },
        )
        tracking_entry.setdefault("files", {})
        tracking_entry["files"]["taxonomy"] = {
            "source_hash": status["upstream_hash"],
            "target": target_rel,
        }
        tracking_path = os.path.join(vault_root, ".brain", "tracking.json")
        _snapshot_file(context, tracking_path)
        sync_definitions.save_tracking(vault_root, tracking)
        result["updated"].append({
            "type": full_type_key,
            "target": target_rel,
            "action": action,
        })
        return True

    with open(upstream_path, "r", encoding="utf-8") as f:
        upstream_content = f.read()
    with open(target_path, "r", encoding="utf-8") as f:
        local_content = f.read()

    date_source = _extract_taxonomy_date_source(upstream_content, pattern)
    if not date_source:
        result["warnings"].append({
            "type": full_type_key,
            "target": target_rel,
            "reason": f"canonical taxonomy has no date source for pattern {pattern!r}",
        })
        return False

    required_fields = _extract_frontmatter_fields(upstream_content)
    patched = _patch_blocking_taxonomy(
        local_content,
        pattern=pattern,
        date_source=date_source,
        required_field=date_source if date_source in required_fields else None,
    )
    if patched is None or patched == local_content:
        result["warnings"].append({
            "type": full_type_key,
            "target": target_rel,
            "reason": "taxonomy needs manual date_source remediation",
        })
        return False

    _snapshot_file(context, target_path)
    safe_write(target_path, patched, bounds=vault_root)
    result["patched"].append({
        "type": full_type_key,
        "target": target_rel,
        "action": action,
    })
    return True


def patch_pre_compile(vault_root: str, *, context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Patch 0.29 compile blockers before the compile gate runs."""
    if context is None:
        context = {}
    compile_error = context.get("compile_error")
    result: dict[str, Any] = {
        "status": "skipped",
        "updated": [],
        "patched": [],
        "warnings": [],
    }
    if not compile_error:
        return result

    tracking = sync_definitions.load_tracking(vault_root)
    attempted: set[tuple[str, str]] = set()
    validate_compile = context.get("validate_compile")

    while True:
        parsed = _extract_missing_date_source_error(compile_error)
        if parsed is None:
            break

        type_key, pattern = parsed
        if (type_key, pattern) in attempted:
            break
        attempted.add((type_key, pattern))

        changed = _remediate_blocking_taxonomy(
            vault_root,
            type_key=type_key,
            pattern=pattern,
            tracking=tracking,
            context=context,
            result=result,
        )
        if not changed:
            break
        if not callable(validate_compile):
            break
        compile_error = validate_compile()

    context["compile_error"] = compile_error

    if result["warnings"]:
        result["status"] = "warnings"
    elif result["updated"] or result["patched"]:
        result["status"] = "ok"
    return result


def _infer_writing_status(fields: dict) -> str | None:
    """Infer writing status from ``publisheddate`` presence. None if no change."""
    if fields.get("status"):
        return None
    return "published" if fields.get("publisheddate") else "draft"


def _title_from_stem(naming: dict | None, fields: dict, filename: str) -> str:
    """Reverse-engineer a title for re-render, falling back to the stem."""
    stem = os.path.splitext(filename)[0]
    if not naming:
        return stem
    title = extract_title(naming, fields, filename)
    return title or stem


def _rendered_filename(naming: dict | None, fields: dict, filename: str) -> str | None:
    """Render the expected filename for ``fields``; None if no change needed."""
    if not naming:
        return None
    title = _title_from_stem(naming, fields, filename)
    try:
        new_name = render_filename(naming, title, fields)
    except ValueError:
        return None
    return new_name if new_name != filename else None


def _target_folder(art: dict, fields: dict, current_folder: str) -> str | None:
    """For temporal artefacts, compute the ``yyyy-mm/`` folder. None if unchanged."""
    if art.get("classification") != "temporal":
        return None
    try:
        target = resolve_folder(art, fields=fields)
    except ValueError:
        return None
    return target if target != current_folder else None


def _process_artefact(
    vault_root: str,
    art: dict,
    rel_path: str,
    *,
    dry_run: bool,
    counts: dict[str, int],
    actions: list[str],
) -> None:
    """Backfill one artefact. Mutates ``counts`` and ``actions``."""
    abs_path = os.path.join(vault_root, rel_path)
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError):
        return

    fields, body = parse_frontmatter(text)
    original = dict(fields)

    if art.get("type") == "living/writing":
        inferred = _infer_writing_status(fields)
        if inferred is not None:
            fields["status"] = inferred
            counts["writing_status_inferred"] += 1

    reconcile_timestamps(fields, abs_path, filename=os.path.basename(rel_path))

    naming = art.get("naming")
    rule = select_rule(naming, fields) if naming else None
    if rule is not None:
        try:
            before = fields.get(rule.get("date_source")) if rule.get("date_source") else None
            reconcile_date_source(fields, abs_path, os.path.basename(rel_path), naming, rule)
            after = fields.get(rule.get("date_source")) if rule.get("date_source") else None
            if rule.get("date_source") and before != after:
                counts["date_source_backfilled"] += 1
        except ValueError:
            counts["date_source_unresolvable"] += 1

    frontmatter_changed = fields != original
    if frontmatter_changed:
        counts["frontmatter_backfilled"] += 1

    current_folder = os.path.dirname(rel_path)
    new_folder = _target_folder(art, fields, current_folder)
    new_filename = _rendered_filename(naming, fields, os.path.basename(rel_path))

    final_folder = new_folder if new_folder is not None else current_folder
    final_basename = new_filename or os.path.basename(rel_path)
    final_rel = os.path.join(final_folder, final_basename) if final_folder else final_basename

    if dry_run:
        if frontmatter_changed:
            actions.append(f"backfill {rel_path}")
        if new_folder is not None:
            counts["relocated"] += 1
            actions.append(f"relocate {rel_path} → {final_rel}")
        if new_filename is not None:
            counts["renamed"] += 1
            actions.append(f"rename {rel_path} → {final_rel}")
        if not frontmatter_changed and new_folder is None and new_filename is None:
            counts["already_clean"] += 1
        return

    if frontmatter_changed:
        new_content = serialize_frontmatter(fields, body=body)
        safe_write(abs_path, new_content, bounds=vault_root)
        actions.append(f"backfill {rel_path}")

    if new_folder is not None:
        abs_target_dir = os.path.join(vault_root, new_folder)
        os.makedirs(abs_target_dir, exist_ok=True)
        relocated_rel = os.path.join(new_folder, os.path.basename(rel_path))
        rename_and_update_links(vault_root, rel_path, relocated_rel)
        rel_path = relocated_rel
        counts["relocated"] += 1
        actions.append(f"relocate → {rel_path}")

    if new_filename is not None and new_filename != os.path.basename(rel_path):
        renamed_rel = os.path.join(os.path.dirname(rel_path), new_filename)
        rename_and_update_links(vault_root, rel_path, renamed_rel)
        counts["renamed"] += 1
        actions.append(f"rename → {renamed_rel}")

    if not frontmatter_changed and new_folder is None and new_filename is None:
        counts["already_clean"] += 1


def backfill_vault(vault_root: str, *, router: dict | None = None, dry_run: bool = False) -> dict[str, Any]:
    """Backfill timestamps and type-specific date fields across the vault.

    Idempotent — a second run after a successful first run is a no-op.
    Tests may pre-compile and pass a router in to avoid the on-disk lookup.
    """
    vault_root = str(vault_root)
    if router is None:
        router = load_compiled_router(vault_root)
    if "error" in router:
        return {"status": "error", "message": router["error"], "actions": []}

    counts = {
        "frontmatter_backfilled": 0,
        "date_source_backfilled": 0,
        "date_source_unresolvable": 0,
        "renamed": 0,
        "relocated": 0,
        "already_clean": 0,
        "writing_status_inferred": 0,
    }
    actions: list[str] = []

    for art in router.get("artefacts", []):
        if not art.get("configured"):
            continue
        for rel_path in find_type_files(vault_root, art["path"], skip_archive=True):
            if is_archived_path(rel_path):
                continue
            _process_artefact(
                vault_root,
                art,
                rel_path,
                dry_run=dry_run,
                counts=counts,
                actions=actions,
            )

    return {
        "status": "ok",
        "dry_run": dry_run,
        "counts": counts,
        "actions": actions,
    }


def migrate(vault_root: str) -> dict[str, Any]:
    """Entry point invoked by ``upgrade.py``.

    Runs ``migrate_naming`` first (RD-8) so legacy filename formats are
    canonicalised, then backfills timestamps and related fields.
    """
    vault_root = os.path.realpath(str(vault_root))
    summary_actions: list[str] = []

    # 1. Legacy filename canonicalisation (RD-8).
    naming_result = migrate_naming.migrate_vault(vault_root, dry_run=False)
    if naming_result.get("renamed"):
        summary_actions.append(
            f"migrate_naming: renamed {naming_result['renamed']} files"
        )
    for err in naming_result.get("errors", []):
        summary_actions.append(f"migrate_naming error on {err.get('file')}: {err.get('error')}")

    # 2. Backfill timestamps + type-specific date fields + writing status.
    backfill = backfill_vault(vault_root, dry_run=False)
    if backfill.get("status") != "ok":
        return {"status": "error", "message": backfill.get("message", "backfill failed")}

    counts = backfill["counts"]
    if counts["frontmatter_backfilled"]:
        summary_actions.append(f"backfilled frontmatter on {counts['frontmatter_backfilled']} files")
    if counts["writing_status_inferred"]:
        summary_actions.append(f"inferred status on {counts['writing_status_inferred']} writings")
    if counts["date_source_backfilled"]:
        summary_actions.append(
            f"backfilled type-specific date_source on {counts['date_source_backfilled']} files"
        )
    if counts["renamed"]:
        summary_actions.append(f"renamed {counts['renamed']} files")
    if counts["relocated"]:
        summary_actions.append(f"relocated {counts['relocated']} temporal files")
    if counts["date_source_unresolvable"]:
        summary_actions.append(
            f"{counts['date_source_unresolvable']} files had unresolvable date_source (skipped)"
        )

    if not summary_actions:
        return {"status": "skipped", "actions": []}
    return {"status": "ok", "actions": summary_actions}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill timestamps and rename files for the v0.29.0 contract.",
    )
    parser.add_argument("--vault", help="Vault root (default: auto-detect)")
    parser.add_argument("--dry-run", action="store_true", help="Report without writing")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args()

    vault_root = str(find_vault_root(args.vault))
    result = backfill_vault(vault_root, dry_run=args.dry_run)

    if args.json_output:
        print(json.dumps(result, indent=2))
        return

    prefix = "[DRY RUN] " if args.dry_run else ""
    for line in result.get("actions", []):
        print(f"  {prefix}{line}")

    counts = result.get("counts", {})
    print(
        f"\n{prefix}backfilled={counts.get('frontmatter_backfilled', 0)}, "
        f"renamed={counts.get('renamed', 0)}, "
        f"relocated={counts.get('relocated', 0)}, "
        f"already_clean={counts.get('already_clean', 0)}, "
        f"writing_status_inferred={counts.get('writing_status_inferred', 0)}"
    )


if __name__ == "__main__":
    main()
