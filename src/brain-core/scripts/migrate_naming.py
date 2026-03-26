#!/usr/bin/env python3
"""
migrate_naming.py — Migrate vault filenames to generous naming conventions.

Renames existing artefact files from aggressive slugs to human-readable titles:
  - Living: my-project.md → My Project.md
  - Temporal: 20260324-plan--api-refactor.md → 20260324-plan~ API Refactor.md

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

from _common import find_vault_root, title_to_filename
from check import find_type_files, load_router, naming_pattern_to_regex
from rename import rename_and_update_links


# ---------------------------------------------------------------------------
# Slug humanisation
# ---------------------------------------------------------------------------

def slug_to_title(slug):
    """Convert a hyphenated slug to a human-readable title.

    Replaces hyphens with spaces and title-cases each word.
    This is a best-guess reverse of title_to_slug() — it won't recover
    the original title exactly (e.g. acronyms, punctuation) but gives
    a reasonable starting point.
    """
    return slug.replace("-", " ").title()


# ---------------------------------------------------------------------------
# Pattern matching for old conventions
# ---------------------------------------------------------------------------

# Old temporal pattern: yyyymmdd-{prefix}--{slug}.md
_OLD_TEMPORAL_RE = re.compile(
    r"^(\d{8}-[a-z]+(?:-[a-z]+)*)--([a-z0-9]+(?:-[a-z0-9]+)*)\.md$"
)

# Old shaping transcript: yyyymmdd-{doctype}-transcript--{slug}.md
_OLD_SHAPING_RE = re.compile(
    r"^(\d{8}-[a-z]+-transcript)--([a-z0-9]+(?:-[a-z0-9]+)*)\.md$"
)

# Old living pattern: aggressive-slug.md (all lowercase, hyphens, no spaces)
_OLD_LIVING_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*\.md$")


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def compute_new_filename(filename, artefact):
    """Compute the new filename for an old-convention file.

    Returns the new filename string, or None if no migration needed.
    """
    classification = artefact.get("classification", "living")

    if classification == "temporal":
        # Try shaping transcript first (more specific pattern)
        m = _OLD_SHAPING_RE.match(filename)
        if m:
            prefix, slug = m.group(1), m.group(2)
            return f"{prefix}~ {slug_to_title(slug)}.md"

        # Standard temporal: yyyymmdd-{prefix}--{slug}.md
        m = _OLD_TEMPORAL_RE.match(filename)
        if m:
            prefix, slug = m.group(1), m.group(2)
            return f"{prefix}~ {slug_to_title(slug)}.md"

        return None  # doesn't match old pattern (e.g. logs, daily notes)

    # Living types: aggressive-slug.md → Title Case.md
    if _OLD_LIVING_RE.match(filename):
        stem = filename[:-3]  # strip .md
        new_stem = slug_to_title(stem)
        new_filename = title_to_filename(new_stem) + ".md"
        if new_filename != filename:
            return new_filename

    return None


def migrate_vault(vault_root, router=None, dry_run=False):
    """Migrate all vault files to new naming conventions.

    Returns a summary dict with counts and details of what was (or would be) done.
    """
    vault_root = str(vault_root)

    if router is None:
        router = load_router(vault_root)
    if "error" in router:
        return {"error": router["error"], "renamed": 0, "skipped": 0, "errors": []}

    renamed = []
    skipped = 0
    errors = []

    for art in router.get("artefacts", []):
        if not art.get("configured"):
            continue

        # Find all files for this type
        files = find_type_files(vault_root, art["path"], skip_archive=True)

        for rel_path in files:
            filename = os.path.basename(rel_path)
            new_filename = compute_new_filename(filename, art)

            if new_filename is None or new_filename == filename:
                skipped += 1
                continue

            # Compute new relative path (same directory, new filename)
            dir_part = os.path.dirname(rel_path)
            new_rel_path = os.path.join(dir_part, new_filename) if dir_part else new_filename

            # Check if target already exists
            if os.path.isfile(os.path.join(vault_root, new_rel_path)):
                errors.append({
                    "file": rel_path,
                    "target": new_rel_path,
                    "error": "Target file already exists",
                })
                continue

            if dry_run:
                renamed.append({
                    "source": rel_path,
                    "dest": new_rel_path,
                    "links_updated": 0,
                })
            else:
                try:
                    links = rename_and_update_links(vault_root, rel_path, new_rel_path)
                    renamed.append({
                        "source": rel_path,
                        "dest": new_rel_path,
                        "links_updated": links,
                    })
                except (FileNotFoundError, OSError) as e:
                    errors.append({
                        "file": rel_path,
                        "target": new_rel_path,
                        "error": str(e),
                    })

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
