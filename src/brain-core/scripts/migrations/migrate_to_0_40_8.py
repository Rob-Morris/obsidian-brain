#!/usr/bin/env python3
"""
migrate_to_0_40_8.py — normalise duplicate artefact frontmatter blocks.

Some malformed create flows wrote a full frontmatter block into the markdown
body after Brain had already generated document frontmatter. This migration
merges that nested block into the canonical document frontmatter and strips the
duplicate block from the body.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from _common import find_vault_root
from _lifecycle.frontmatter_repairs import normalize_duplicate_frontmatter_documents


VERSION = "0.40.8"


def backfill_vault(vault_root: str, *, dry_run: bool = False) -> dict:
    """Merge duplicate frontmatter blocks in artefact documents."""
    return normalize_duplicate_frontmatter_documents(vault_root, dry_run=dry_run)


def migrate(vault_root: str) -> dict:
    """Upgrade runner entry point."""
    return backfill_vault(vault_root, dry_run=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Repair duplicate artefact frontmatter blocks.")
    parser.add_argument("--vault", help="Path to the Brain vault (default: auto-detect).")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing them.")
    args = parser.parse_args(argv)

    vault_root = find_vault_root(args.vault)
    result = backfill_vault(vault_root, dry_run=args.dry_run)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
