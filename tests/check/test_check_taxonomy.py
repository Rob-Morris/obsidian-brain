"""Tests for check.py — router-driven vault compliance checker."""

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


class TestCheckTaxonomyTypeConsistency:
    def test_no_finding_when_singular_differs(self, vault_cr):
        """Normal case: frontmatter_type is singular, type is plural — no finding."""
        router = cr.compile(vault_cr)
        findings = check.check_taxonomy_type_consistency(str(vault_cr), router)
        type_consistency = [f for f in findings if f["check"] == "taxonomy_type_consistency"]
        assert len(type_consistency) == 0

    def test_flags_when_frontmatter_type_equals_folder_type(self, vault_cr):
        """When taxonomy omits type: field, frontmatter_type falls back to plural — flag it."""
        (vault_cr / "Notes").mkdir()
        tax = vault_cr / "_Config" / "Taxonomy" / "Living"
        (tax / "Notes.md").write_text(
            "# Notes\n\n## Naming\n\n`{title}.md` in `Notes/`.\n\n"
            "## Frontmatter\n\n```yaml\n---\ntags:\n  - note\n---\n```\n"
        )
        router = cr.compile(vault_cr)
        findings = check.check_taxonomy_type_consistency(str(vault_cr), router)
        type_consistency = [f for f in findings if f["check"] == "taxonomy_type_consistency"]
        assert len(type_consistency) == 1
        assert "notes" in type_consistency[0]["message"]

    def test_no_finding_for_unconfigured(self, vault_cr):
        """Unconfigured artefacts (no taxonomy) should not be flagged."""
        (vault_cr / "Projects").mkdir()
        router = cr.compile(vault_cr)
        findings = check.check_taxonomy_type_consistency(str(vault_cr), router)
        type_consistency = [f for f in findings if f["check"] == "taxonomy_type_consistency"]
        projects_findings = [f for f in type_consistency if "projects" in f["message"]]
        assert len(projects_findings) == 0

