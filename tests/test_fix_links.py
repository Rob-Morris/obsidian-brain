"""Tests for fix_links.py — broken wikilink auto-repair."""

import json
import os

import pytest

import fix_links
from _common import build_vault_file_index
from conftest import make_router, write_md


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

WIKI_ARTEFACT = {
    "type": "living/wiki",
    "folder": "Wiki",
    "naming_pattern": "{Title}.md",
    "frontmatter": {
        "required_fields": ["type", "tags"],
        "type_value": "living/wiki",
    },
}

DESIGN_ARTEFACT = {
    "type": "living/design",
    "folder": "Designs",
    "naming_pattern": "{Title}.md",
    "frontmatter": {
        "required_fields": ["type", "tags", "status"],
        "type_value": "living/design",
        "status_enum": ["proposed", "shaping", "ready", "active", "implemented", "parked", "rejected"],
    },
}


@pytest.fixture
def vault(tmp_path):
    """Create a minimal vault with wiki and design folders."""
    (tmp_path / "Wiki").mkdir()
    (tmp_path / "Designs").mkdir()
    (tmp_path / ".obsidian").mkdir()
    return tmp_path


@pytest.fixture
def router():
    return make_router({
        "living/wiki": WIKI_ARTEFACT,
        "living/design": DESIGN_ARTEFACT,
    })


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestScanAndResolve:
    def test_no_broken_links(self, vault, router):
        write_md(vault / "Wiki" / "My Page.md",
                 {"type": "living/wiki", "tags": ["test"]},
                 "See [[My Page]] self-ref.")
        result = fix_links.scan_and_resolve(str(vault), router)
        assert result["summary"]["total_broken"] == 0

    def test_detects_fixable_slug(self, vault, router):
        write_md(vault / "Wiki" / "Brain Inbox.md",
                 {"type": "living/wiki", "tags": ["test"]},
                 "# Brain Inbox")
        write_md(vault / "Wiki" / "linker.md",
                 {"type": "living/wiki", "tags": ["test"]},
                 "See [[brain-inbox]] for details.")
        result = fix_links.scan_and_resolve(str(vault), router)
        assert result["summary"]["fixed"] == 1
        assert result["fixed"][0]["target"] == "brain-inbox"
        assert result["fixed"][0]["resolved_to"] == "Brain Inbox"
        assert result["fixed"][0]["strategy"] == "slug_to_title"

    def test_detects_unresolvable(self, vault, router):
        write_md(vault / "Wiki" / "linker.md",
                 {"type": "living/wiki", "tags": ["test"]},
                 "See [[totally-nonexistent]] for details.")
        result = fix_links.scan_and_resolve(str(vault), router)
        assert result["summary"]["unresolvable"] == 1
        assert result["unresolvable"][0]["target"] == "totally-nonexistent"

    def test_detects_ambiguous(self, vault, router):
        write_md(vault / "Wiki" / "Foo Bar.md",
                 {"type": "living/wiki", "tags": ["test"]}, "# Foo")
        write_md(vault / "Designs" / "Foo Bar.md",
                 {"type": "living/design", "tags": ["test"], "status": "shaping"},
                 "# Foo Design")
        write_md(vault / "Wiki" / "linker.md",
                 {"type": "living/wiki", "tags": ["test"]},
                 "See [[foo-bar]] for details.")
        result = fix_links.scan_and_resolve(str(vault), router)
        assert result["summary"]["ambiguous"] == 1


class TestApplyFixes:
    def test_dry_run_does_not_modify(self, vault, router):
        write_md(vault / "Wiki" / "Brain Inbox.md",
                 {"type": "living/wiki", "tags": ["test"]}, "# Brain Inbox")
        write_md(vault / "Wiki" / "linker.md",
                 {"type": "living/wiki", "tags": ["test"]},
                 "See [[brain-inbox]] for details.")
        # Just scan — no fix
        result = fix_links.scan_and_resolve(str(vault), router)
        assert result["summary"]["fixed"] == 1
        # File should still have the old link
        content = (vault / "Wiki" / "linker.md").read_text()
        assert "[[brain-inbox]]" in content

    def test_fix_applies_substitutions(self, vault, router):
        write_md(vault / "Wiki" / "Brain Inbox.md",
                 {"type": "living/wiki", "tags": ["test"]}, "# Brain Inbox")
        write_md(vault / "Wiki" / "linker.md",
                 {"type": "living/wiki", "tags": ["test"]},
                 "See [[brain-inbox]] for details.")
        result = fix_links.scan_and_resolve(str(vault), router)
        total = fix_links.apply_fixes(str(vault), result["fixed"])
        assert total >= 1
        content = (vault / "Wiki" / "linker.md").read_text()
        assert "[[Brain Inbox]]" in content
        assert "[[brain-inbox]]" not in content

    def test_fix_preserves_alias(self, vault, router):
        write_md(vault / "Wiki" / "Brain Inbox.md",
                 {"type": "living/wiki", "tags": ["test"]}, "# Brain Inbox")
        write_md(vault / "Wiki" / "linker.md",
                 {"type": "living/wiki", "tags": ["test"]},
                 "See [[brain-inbox|my inbox]] for details.")
        result = fix_links.scan_and_resolve(str(vault), router)
        fix_links.apply_fixes(str(vault), result["fixed"])
        content = (vault / "Wiki" / "linker.md").read_text()
        assert "[[Brain Inbox|my inbox]]" in content

    def test_fix_skips_ambiguous(self, vault, router):
        write_md(vault / "Wiki" / "Foo Bar.md",
                 {"type": "living/wiki", "tags": ["test"]}, "# Foo")
        write_md(vault / "Designs" / "Foo Bar.md",
                 {"type": "living/design", "tags": ["test"], "status": "shaping"},
                 "# Foo Design")
        write_md(vault / "Wiki" / "linker.md",
                 {"type": "living/wiki", "tags": ["test"]},
                 "See [[foo-bar]] for details.")
        result = fix_links.scan_and_resolve(str(vault), router)
        # Nothing in the fixed list — it's ambiguous
        total = fix_links.apply_fixes(str(vault), result["fixed"])
        assert total == 0
        content = (vault / "Wiki" / "linker.md").read_text()
        assert "[[foo-bar]]" in content


class TestJsonOutput:
    def test_json_structure(self, vault, router):
        write_md(vault / "Wiki" / "Brain Inbox.md",
                 {"type": "living/wiki", "tags": ["test"]}, "# Brain Inbox")
        write_md(vault / "Wiki" / "linker.md",
                 {"type": "living/wiki", "tags": ["test"]},
                 "See [[brain-inbox]] and [[nonexistent]].")
        result = fix_links.scan_and_resolve(str(vault), router)
        assert "summary" in result
        assert "fixed" in result
        assert "ambiguous" in result
        assert "unresolvable" in result
        assert result["summary"]["total_broken"] == 2
        assert result["summary"]["fixed"] == 1
        assert result["summary"]["unresolvable"] == 1
