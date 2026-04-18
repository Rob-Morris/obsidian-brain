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
import sys
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


VERSION = "0.29.0"


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
