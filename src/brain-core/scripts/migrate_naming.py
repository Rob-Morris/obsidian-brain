#!/usr/bin/env python3
"""
migrate_naming.py — Migrate vault filenames to generous naming conventions.

Renames existing artefact files from aggressive slugs to human-readable titles:
  - Living: my-project.md → My Project.md
  - Temporal: 20260324-plan--api-refactor.md → 20260324-plan~API Refactor.md
  - Prefixless temporal: 20260307-discord-animation-research.md → 20260307-research~Discord Animation Research.md

Updates all wikilinks vault-wide for each rename.

Usage:
    python3 migrate_naming.py --vault /path/to/vault --dry-run
    python3 migrate_naming.py --vault /path/to/vault --json
    python3 migrate_naming.py --vault /path/to/vault
"""

import json
import os
import re
import sys
from datetime import datetime, timezone

from _common import (
    find_vault_root,
    iter_artefact_paths,
    load_compiled_router,
    read_frontmatter,
    reconcile_fields_for_render,
    render_filename,
    slug_to_title,
    validate_filename,
)
from rename import rename_and_update_links


# ---------------------------------------------------------------------------
# Pattern matching for old conventions
# ---------------------------------------------------------------------------

# Old temporal pattern: yyyymmdd-{prefix}--{slug}.md
_OLD_TEMPORAL_RE = re.compile(
    r"^(\d{8}-[a-z]+(?:-[a-z]+)*)--([a-z0-9]+(?:-[a-z0-9]+)*)\.md$"
)

# Old prefixless temporal pattern: yyyymmdd-{slug}.md (used by research, plans, transcripts
# before they adopted type prefixes)
_OLD_PREFIXLESS_RE = re.compile(
    r"^(\d{8})-([a-z0-9]+(?:-[a-z0-9]+)*)\.md$"
)

# Old living pattern: aggressive-slug.md (all lowercase, hyphens, no spaces)
_OLD_LIVING_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*\.md$")


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def compute_new_filename(filename, artefact, fields=None, abs_path=None):
    """Compute the new filename for an old-convention or off-rule file.

    Two detection paths run in order:

    1. Legacy-regex pass — recognises pre-contract slug forms (aggressive
       living slugs, prefixless or double-dash temporals) and extracts a
       title from the old shape.
    2. State-aware pass — if the filename does not match the naming rule
       selected for the current frontmatter state, re-render it from the
       current state (using the stem as the title).

    Rendering always goes through the shared naming engine so the result is
    whatever the current naming contract says the filename should be.

    Returns the new filename string, or None if no migration is needed.
    """
    classification = artefact.get("classification", "living")
    naming = artefact.get("naming")
    fields = fields or {}

    title = None
    historical_date = None

    if classification == "temporal":
        m = _OLD_TEMPORAL_RE.match(filename)
        if m:
            title = slug_to_title(m.group(2))
            historical_date = m.group(1)[:8]
        else:
            m = _OLD_PREFIXLESS_RE.match(filename)
            if m:
                title = slug_to_title(m.group(2))
                historical_date = m.group(1)
    elif _OLD_LIVING_RE.match(filename):
        title = slug_to_title(filename[:-3])

    if title is None and naming and not validate_filename(naming, fields, filename):
        title = os.path.splitext(filename)[0]

    if title is None or not naming:
        return None

    render_fields = dict(fields)
    if historical_date and "created" not in render_fields:
        try:
            dt = datetime.strptime(historical_date, "%Y%m%d").replace(tzinfo=timezone.utc)
            render_fields["created"] = dt.astimezone().isoformat()
        except ValueError:
            pass
    reconcile_fields_for_render(render_fields, artefact, abs_path, filename)

    try:
        new_filename = render_filename(naming, title, render_fields)
    except ValueError:
        return None

    return new_filename if new_filename != filename else None


def migrate_vault(vault_root, router=None, dry_run=False):
    """Migrate all vault files to new naming conventions.

    Returns a summary dict with counts and details of what was (or would be) done.
    """
    vault_root = str(vault_root)

    if router is None:
        router = load_compiled_router(vault_root)
    if "error" in router:
        return {"error": router["error"], "renamed": 0, "skipped": 0, "errors": []}

    renamed = []
    skipped = 0
    errors = []
    planned = []

    for art in router.get("artefacts", []):
        if not art.get("configured"):
            continue

        for rel_path in iter_artefact_paths(vault_root, art):
            filename = os.path.basename(rel_path)
            abs_path = os.path.join(vault_root, rel_path)
            try:
                fields = read_frontmatter(abs_path)
            except (OSError, UnicodeDecodeError):
                fields = {}

            new_filename = compute_new_filename(filename, art, fields=fields, abs_path=abs_path)

            if new_filename is None or new_filename == filename:
                skipped += 1
                continue

            dir_part = os.path.dirname(rel_path)
            new_rel_path = os.path.join(dir_part, new_filename) if dir_part else new_filename
            planned.append((rel_path, new_rel_path))

    seen_targets = {}
    for rel_path, new_rel_path in planned:
        previous = seen_targets.get(new_rel_path)
        if previous and previous != rel_path:
            errors.append({
                "file": rel_path,
                "target": new_rel_path,
                "error": f"Planned target also claimed by {previous}",
            })
            continue
        seen_targets[new_rel_path] = rel_path

        dest_abs = os.path.join(vault_root, new_rel_path)
        source_abs = os.path.join(vault_root, rel_path)
        if not os.path.isfile(dest_abs):
            continue
        try:
            same = os.path.samefile(source_abs, dest_abs)
        except OSError:
            same = False
        if not same:
            errors.append({
                "file": rel_path,
                "target": new_rel_path,
                "error": "Target file already exists",
            })

    if errors:
        return {
            "dry_run": dry_run,
            "renamed": 0,
            "skipped": skipped,
            "error_count": len(errors),
            "details": [],
            "errors": errors,
        }

    for rel_path, new_rel_path in planned:
        if dry_run:
            renamed.append({
                "source": rel_path,
                "dest": new_rel_path,
                "links_updated": 0,
            })
            continue
        try:
            links = rename_and_update_links(vault_root, rel_path, new_rel_path)
            renamed.append({
                "source": rel_path,
                "dest": new_rel_path,
                "links_updated": links,
            })
        except (FileNotFoundError, FileExistsError, OSError) as e:
            errors.append({
                "file": rel_path,
                "target": new_rel_path,
                "error": str(e),
            })
            break

    return {
        "dry_run": dry_run,
        "renamed": len(renamed),
        "skipped": skipped,
        "error_count": len(errors),
        "details": renamed,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    vault_arg = None
    dry_run = False
    json_mode = False

    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--vault" and i + 1 < len(sys.argv):
            vault_arg = sys.argv[i + 1]
            i += 2
        elif arg == "--dry-run":
            dry_run = True
            i += 1
        elif arg == "--json":
            json_mode = True
            i += 1
        else:
            i += 1

    vault_root = str(find_vault_root(vault_arg))
    result = migrate_vault(vault_root, dry_run=dry_run)

    if json_mode:
        print(json.dumps(result, indent=2))
    else:
        prefix = "[DRY RUN] " if dry_run else ""
        if result.get("error"):
            print(f"Error: {result['error']}", file=sys.stderr)
            sys.exit(1)

        for item in result.get("details", []):
            links_str = f" ({item['links_updated']} links)" if not dry_run else ""
            print(f"  {prefix}{item['source']} → {item['dest']}{links_str}")

        for err in result.get("errors", []):
            print(f"  ERROR {err['file']}: {err['error']}", file=sys.stderr)

        total = result["renamed"]
        print(f"\n{prefix}{total} file(s) renamed, {result['skipped']} skipped, {result['error_count']} error(s)")

    if result.get("error_count", 0) > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
