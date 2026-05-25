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

# Legacy closure callouts. Each pattern matches a heading line followed by any
# number of ``> ...`` continuation lines; (verb, preposition) describes how the
# new ``Deprecated — …`` reason should be phrased when a link is present.
# Authors used several callout shapes pre-v0.43.0 — ``[!info] Superseded``,
# ``[!warning] Deprecated``, ``[!info] Merged``, ``[!note] Live planning moved``
# — and many wrote prose after the keyword rather than a wikilink, so the
# heading pattern is intentionally loose. Order matters: more specific verbs
# first so ``Merged into`` does not collapse to a generic ``Superseded`` match.
_LEGACY_CALLOUT_PATTERNS = [
    (
        re.compile(
            r"^> \[![a-z]+\] Merged[^\n]*$\n(?:^> .*\n)*",
            re.MULTILINE | re.IGNORECASE,
        ),
        "merged into",
    ),
    (
        re.compile(
            r"^> \[![a-z]+\] Replaced[^\n]*$\n(?:^> .*\n)*",
            re.MULTILINE | re.IGNORECASE,
        ),
        "replaced by",
    ),
    (
        re.compile(
            r"^> \[![a-z]+\] (?:Superseded|Deprecated)[^\n]*$\n(?:^> .*\n)*",
            re.MULTILINE | re.IGNORECASE,
        ),
        "superseded",
    ),
    (
        re.compile(
            r"^> \[!note\] (?:Live planning moved|Moved)[^\n]*$\n(?:^> .*\n)*",
            re.MULTILINE | re.IGNORECASE,
        ),
        "superseded",
    ),
]

# Body-line conventions that point at a successor outside any callout. These
# take priority over callout-derived links because they are authoritative
# author-level pointers (the callout's first wikilink is often just a parent
# reference, not the supersession target). Order matches the callout patterns.
_BODY_CONVENTION_PATTERNS = [
    (
        re.compile(r"^\*\*Merged into:\*\*\s*(\[\[[^\]\n]+\]\])", re.MULTILINE),
        "merged into",
    ),
    (
        re.compile(r"^\*\*Replaced by:\*\*\s*(\[\[[^\]\n]+\]\])", re.MULTILINE),
        "replaced by",
    ),
    (
        re.compile(r"^\*\*Superseded by:\*\*\s*(\[\[[^\]\n]+\]\])", re.MULTILINE),
        "superseded",
    ),
]

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


def _find_legacy_callout(body):
    """Return ``(matched_block, reason)`` for the first legacy closure callout
    in *body*, or ``(None, None)`` if none is present."""
    for pattern, reason in _LEGACY_CALLOUT_PATTERNS:
        match = pattern.search(body)
        if match:
            return match.group(0), reason
    return None, None


def _find_body_convention(body):
    """Return ``(link, reason)`` for the first ``**Superseded by:** [[X]]``-style
    body convention, or ``(None, None)`` if none is present."""
    for pattern, reason in _BODY_CONVENTION_PATTERNS:
        match = pattern.search(body)
        if match:
            return match.group(1), reason
    return None, None


def _link_from_callout(callout_block):
    """Return the first wikilink inside *callout_block*, or None."""
    if not callout_block:
        return None
    links = _WIKILINK_RE.findall(callout_block)
    return links[0] if links else None


def _build_deprecated_callout(reason, link):
    """Render a ``> [!info] Deprecated — <reason>[ <link>]`` callout.

    ``superseded`` inserts the ``by`` connector when a link is supplied;
    other reasons already carry their preposition (``merged into``,
    ``replaced by``).
    """
    if not link:
        return f"> [!info] Deprecated — {reason}\n"
    if reason == "superseded":
        return f"> [!info] Deprecated — superseded by {link}\n"
    return f"> [!info] Deprecated — {reason} {link}\n"


def _has_deprecated_callout(body):
    return bool(re.search(r"^> \[!info\] Deprecated\b", body, re.MULTILINE))


def _rewrite_body_for_deprecation(body, status_reason):
    """Replace or prepend the ``[!info] Deprecated`` callout. Idempotent.

    Priority for the reason + link that fill the new callout:

    1. Body convention (``**Superseded by:** [[X]]`` and friends) — most
       authoritative, since authors use these as the canonical successor pointer
       and callouts often lead with parent or related links instead.
    2. Legacy callout (``[!info] Superseded``, ``[!warning] Deprecated``,
       ``[!info] Merged``, ``[!note] Live planning moved``, etc.) — the
       first wikilink inside the matched block.
    3. Status fallback — the reason inferred from the artefact's retired status
       (e.g. ``superseded`` → ``superseded``, ``rejected`` → ``rejected``,
       ``cancelled`` → ``cancelled``). No link.

    When a legacy callout exists, the new callout replaces it in place;
    otherwise it is prepended before the first ``##`` heading.
    """
    if _has_deprecated_callout(body):
        return body

    body_link, body_reason = _find_body_convention(body)
    legacy_block, legacy_reason = _find_legacy_callout(body)

    if body_link:
        reason, link = body_reason, body_link
    elif legacy_block:
        reason, link = legacy_reason, _link_from_callout(legacy_block)
    else:
        reason, link = status_reason, None

    new_callout = _build_deprecated_callout(reason, link)

    if legacy_block:
        return body.replace(legacy_block, new_callout, 1)

    # No legacy callout — insert before the first section heading. Leading
    # prose like **Origin:** / **Parent design:** stays above the callout.
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
