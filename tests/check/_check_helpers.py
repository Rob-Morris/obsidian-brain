"""Shared plain helpers for the check test suite."""

import json
import os
import sys
import time

import pytest

import check
import compile_router as cr
import _lifecycle.semantic_repairs as semantic_repairs
import _search.index as search_index
import _search.paths as search_paths

from brain_test_support import make_router, write_md
from brain_test_support import filesystem_is_case_sensitive


def compile_minimal_router(vault_root):
    """Build a small real compiled router with freshness metadata."""
    bc = vault_root / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("0.32.5\n")
    (bc / "session-core.md").write_text("# Session Core\n\n## Core Docs\n\n## Standards\n")

    config = vault_root / "_Config"
    config.mkdir()
    (config / "router.md").write_text("Always:\n- Keep a tidy vault.\n")
    taxonomy = config / "Taxonomy" / "Living"
    taxonomy.mkdir(parents=True)
    (taxonomy / "wiki.md").write_text(
        "# Wiki\n\n"
        "## Naming\n\n`{Title}.md` in `Wiki/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: living/wiki\ntags:\n  - wiki\n---\n```\n"
    )
    write_md(
        vault_root / "Wiki" / "Reference.md",
        {"type": "living/wiki", "tags": ["wiki"], "key": "reference"},
        "# Reference",
    )
    router = cr.compile(str(vault_root))
    brain_local = vault_root / ".brain" / "local"
    brain_local.mkdir(parents=True, exist_ok=True)
    (brain_local / "compiled-router.json").write_text(json.dumps(router, indent=2) + "\n")
    return router

