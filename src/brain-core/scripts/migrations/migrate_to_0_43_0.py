#!/usr/bin/env python3
"""
migrate_to_0_43_0.py — Unified closure-status vocabulary.

Aligns existing vault artefacts to the closure-status model defined in the
``Artefact Closure Status Model`` design:

- design ``superseded``/``rejected`` → ``deprecated``
- plan ``superseded``/``rejected``  → ``deprecated``
- release ``cancelled``              → ``deprecated``
- task ``blocked``                   → ``parked``

When migrating to ``deprecated``, this migration adds or rewrites a context
callout explaining the reason (preserving any existing supersession link).
Files in retired ``+Superseded/``, ``+Rejected/``, ``+Cancelled/`` folders move
to ``+Deprecated/``. Empty retired folders are removed.

Idempotent — re-running on a migrated vault is a no-op.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from _common import (
    find_vault_root,
    iter_artefact_paths,
    load_compiled_router,
    parse_frontmatter,
    safe_write,
    serialize_frontmatter,
)
from rename import rename_and_update_links


VERSION = "0.43.0"

# (artefact_type, old_status) -> (new_status, reason_keyword | None).
# reason_keyword=None means no callout (blocked → parked stays non-terminal).
_STATUS_MIGRATIONS = {
    ("living/design", "superseded"): ("deprecated", "superseded"),
    ("living/design", "rejected"): ("deprecated", "rejected"),
    ("temporal/plan", "superseded"): ("deprecated", "superseded"),
    ("temporal/plan", "rejected"): ("deprecated", "rejected"),
    ("living/release", "cancelled"): ("deprecated", "cancelled"),
    ("living/task", "blocked"): ("parked", None),
}

# Retired +Status folder names mapped to their replacement.
_FOLDER_RENAMES = {
    "+Superseded": "+Deprecated",
    "+Rejected": "+Deprecated",
    "+Cancelled": "+Deprecated",
}

# Existing supersession callout. Matches the canonical two-line form and the
# single-line variant. The first capture is the optional "by [[link]]" target
# on the heading line; subsequent continuation lines (any "> ..." lines) are
# consumed so the whole block is replaced atomically.
_SUPERSEDED_CALLOUT_RE = re.compile(
    r"^> \[!info\] Superseded(?: by (\[\[[^\]\n]+\]\]))?\s*$\n"
    r"(?:^> .*\n)*",
    re.MULTILINE,
)

_WIKILINK_RE = re.compile(r"\[\[[^\[\]\n]+?\]\]")


def _affected_types():
    return {t for t, _ in _STATUS_MIGRATIONS}


def _artefacts_for_types(router, types):
    by_type = {}
    for art in router.get("artefacts", []):
        ft = art.get("frontmatter_type")
        if ft in types and ft not in by_type:
            by_type[ft] = art
    return by_type


def _find_supersession_callout(body):
    """Return the matched Superseded callout block, or None."""
    match = _SUPERSEDED_CALLOUT_RE.search(body)
    return match.group(0) if match else None


def _link_from_callout(callout_block):
    """Return the first wikilink inside *callout_block*, or None."""
    if not callout_block:
        return None
    links = _WIKILINK_RE.findall(callout_block)
    return links[0] if links else None


def _build_deprecated_callout(reason, link):
    if reason == "superseded" and link:
        return f"> [!info] Deprecated — superseded by {link}\n"
    if reason == "superseded":
        return "> [!info] Deprecated — superseded\n"
    return f"> [!info] Deprecated — {reason}\n"


def _has_deprecated_callout(body):
    return bool(re.search(r"^> \[!info\] Deprecated\b", body, re.MULTILINE))


def _rewrite_body_for_deprecation(body, reason):
    """Replace an existing Superseded callout, or prepend a new Deprecated
    callout near the top of the body. Idempotent — does nothing if a
    Deprecated callout is already present."""
    if _has_deprecated_callout(body):
        return body

    existing_callout = _find_supersession_callout(body)
    new_callout = _build_deprecated_callout(reason, _link_from_callout(existing_callout))

    if existing_callout:
        return body.replace(existing_callout, new_callout, 1)

    # No existing supersession callout — insert near the top, before the first
    # section heading. Leading prose like **Origin:** / **Parent design:**
    # stays above the callout.
    lines = body.splitlines(keepends=True)
    insert_at = len(lines)
    for i, line in enumerate(lines):
        if line.startswith("## "):
            insert_at = i
            break

    return "".join(lines[:insert_at]) + new_callout + "\n" + "".join(lines[insert_at:])


def _path_has_retired_segment(rel_path):
    return any(part in _FOLDER_RENAMES for part in rel_path.split(os.sep))


def _rewrite_path(rel_path):
    """Rewrite +Superseded / +Rejected / +Cancelled segments to +Deprecated."""
    parts = rel_path.split(os.sep)
    new_parts = [_FOLDER_RENAMES.get(p, p) for p in parts]
    return os.sep.join(new_parts)


def _retired_dirs_in_path(rel_path):
    """Return parent directory paths (vault-relative) of any retired +Status
    segments in *rel_path*, deepest first."""
    parts = rel_path.split(os.sep)
    out = []
    for i, p in enumerate(parts):
        if p in _FOLDER_RENAMES:
            out.append(os.sep.join(parts[: i + 1]))
    return out


def backfill_vault(vault_root, *, router=None, dry_run=False):
    """Apply the closure-status migration to *vault_root*. Returns a summary."""
    vault_root = str(vault_root)
    if router is None:
        router = load_compiled_router(vault_root)
        if "error" in router:
            raise ValueError(router["error"])

    types_to_artefact = _artefacts_for_types(router, _affected_types())

    actions = []
    warnings = []
    updated = 0
    retired_dirs = set()

    for art_type, artefact in types_to_artefact.items():
        for rel_path in sorted(iter_artefact_paths(vault_root, artefact, include_status_folders=True)):
            abs_path = os.path.join(vault_root, rel_path)
            try:
                with open(abs_path, "r", encoding="utf-8") as handle:
                    content = handle.read()
            except OSError as e:
                warnings.append(f"{rel_path}: read failed: {e}")
                continue

            fields, body = parse_frontmatter(content)
            if fields.get("type") != art_type:
                continue

            old_status = fields.get("status")
            migration = _STATUS_MIGRATIONS.get((art_type, old_status))
            in_retired_folder = _path_has_retired_segment(rel_path)

            if migration is None and not in_retired_folder:
                continue

            file_actions = []
            new_body = body
            new_rel_path = rel_path

            if migration is not None:
                new_status, reason = migration
                fields["status"] = new_status
                file_actions.append(f"status {old_status} → {new_status}")
                if reason is not None:
                    rewritten = _rewrite_body_for_deprecation(body, reason)
                    if rewritten != body:
                        file_actions.append(f"deprecation callout ({reason})")
                        new_body = rewritten

            if in_retired_folder:
                candidate = _rewrite_path(rel_path)
                if candidate != rel_path:
                    abs_candidate = os.path.join(vault_root, candidate)
                    if os.path.exists(abs_candidate):
                        warnings.append(
                            f"{rel_path}: destination already exists, skipped move to {candidate}"
                        )
                    else:
                        new_rel_path = candidate
                        file_actions.append(f"moved to {candidate}")
                        retired_dirs.update(_retired_dirs_in_path(rel_path))

            if not file_actions:
                continue

            updated += 1
            actions.append({"path": rel_path, "changes": file_actions, "new_path": new_rel_path})

            if dry_run:
                continue

            safe_write(
                abs_path,
                serialize_frontmatter(fields, body=new_body),
                bounds=vault_root,
            )
            if new_rel_path != rel_path:
                rename_and_update_links(vault_root, rel_path, new_rel_path)

    # Clean up empty retired folders (deepest first). rmdir succeeds only on
    # empty directories; non-empty or missing dirs raise OSError, which we
    # surface as a warning rather than failing the migration.
    if not dry_run:
        for dir_rel in sorted(retired_dirs, key=lambda p: p.count(os.sep), reverse=True):
            abs_dir = os.path.join(vault_root, dir_rel)
            try:
                os.rmdir(abs_dir)
                actions.append({"path": dir_rel, "changes": ["removed empty retired folder"]})
            except OSError:
                # Non-empty (other content survived) or already removed — leave it.
                pass

    return {
        "status": "skipped" if updated == 0 and not warnings else "ok",
        "updated": updated,
        "warnings": warnings,
        "actions": actions,
        "dry_run": dry_run,
    }


def migrate(vault_root):
    return backfill_vault(vault_root, dry_run=False)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Migrate closure statuses to the unified deprecated vocabulary."
    )
    parser.add_argument("--vault", help="Path to vault root (default: auto-detect)")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing them")
    args = parser.parse_args(argv)
    vault_root = str(find_vault_root(args.vault))
    result = backfill_vault(vault_root, dry_run=args.dry_run)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
