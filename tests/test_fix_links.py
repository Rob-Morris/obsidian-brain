"""Tests for fix_links.py — broken wikilink auto-repair."""

import json
import os

import pytest

import fix_links
from _common import file_index_from_documents
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

    def test_fix_preserves_literal_wikilink_in_inline_code(self, vault, router):
        """fix_links must not rewrite a documentation example inside backticks."""
        write_md(vault / "Wiki" / "Brain Inbox.md",
                 {"type": "living/wiki", "tags": ["test"]}, "# Brain Inbox")
        write_md(vault / "Wiki" / "linker.md",
                 {"type": "living/wiki", "tags": ["test"]},
                 "Doc: `[[brain-inbox]]` example.\nReal: [[brain-inbox]] here.")
        result = fix_links.scan_and_resolve(str(vault), router)
        fix_links.apply_fixes(str(vault), result["fixed"])
        content = (vault / "Wiki" / "linker.md").read_text()
        assert "`[[brain-inbox]]`" in content
        assert "Real: [[Brain Inbox]] here." in content

    def test_fix_preserves_literal_wikilink_in_fence(self, vault, router):
        write_md(vault / "Wiki" / "Brain Inbox.md",
                 {"type": "living/wiki", "tags": ["test"]}, "# Brain Inbox")
        write_md(vault / "Wiki" / "linker.md",
                 {"type": "living/wiki", "tags": ["test"]},
                 "```\n[[brain-inbox]]\n```\n[[brain-inbox]]\n")
        result = fix_links.scan_and_resolve(str(vault), router)
        fix_links.apply_fixes(str(vault), result["fixed"])
        content = (vault / "Wiki" / "linker.md").read_text()
        assert "```\n[[brain-inbox]]\n```" in content
        assert "\n[[Brain Inbox]]\n" in content

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


class TestScanAndResolveFile:
    def test_returns_only_resolvable(self, vault, router):
        write_md(vault / "Wiki" / "Real Page.md",
                 {"type": "living/wiki", "tags": ["test"]}, "# Real")
        write_md(vault / "Wiki" / "linker.md",
                 {"type": "living/wiki", "tags": ["test"]},
                 "See [[real-page]] and [[definitely-gone]].")
        fixes = fix_links.scan_and_resolve_file(str(vault), "Wiki/linker.md")
        assert len(fixes) == 1
        assert fixes[0]["target"] == "real-page"
        assert fixes[0]["resolved_to"] == "Real Page"

    def test_clean_file(self, vault, router):
        write_md(vault / "Wiki" / "linker.md",
                 {"type": "living/wiki", "tags": ["test"]}, "# No links")
        fixes = fix_links.scan_and_resolve_file(str(vault), "Wiki/linker.md")
        assert fixes == []

    def test_scan_file_result_shape(self, vault, router):
        write_md(vault / "Wiki" / "Real Page.md",
                 {"type": "living/wiki", "tags": ["test"]}, "# Real")
        write_md(vault / "Wiki" / "linker.md",
                 {"type": "living/wiki", "tags": ["test"]},
                 "See [[real-page]] and [[gone]].")
        result = fix_links.scan_file(str(vault), "Wiki/linker.md", router=router)
        assert result["summary"]["fixed"] == 1
        assert result["summary"]["unresolvable"] == 1
        assert result["path"] == "Wiki/linker.md"


class TestApplyFixesToFile:
    def test_applies_all(self, vault, router):
        write_md(vault / "Wiki" / "Real Page.md",
                 {"type": "living/wiki", "tags": ["test"]}, "# Real")
        write_md(vault / "Wiki" / "linker.md",
                 {"type": "living/wiki", "tags": ["test"]},
                 "See [[real-page]] for details.")
        fixes = fix_links.scan_and_resolve_file(str(vault), "Wiki/linker.md")
        count = fix_links.apply_fixes_to_file(str(vault), "Wiki/linker.md", fixes)
        assert count >= 1
        content = (vault / "Wiki" / "linker.md").read_text()
        assert "[[Real Page]]" in content

    def test_applies_subset_via_filter(self, vault, router):
        write_md(vault / "Wiki" / "Real One.md",
                 {"type": "living/wiki", "tags": ["test"]}, "# R1")
        write_md(vault / "Wiki" / "Real Two.md",
                 {"type": "living/wiki", "tags": ["test"]}, "# R2")
        write_md(vault / "Wiki" / "linker.md",
                 {"type": "living/wiki", "tags": ["test"]},
                 "See [[real-one]] and [[real-two]].")
        fixes = fix_links.scan_and_resolve_file(str(vault), "Wiki/linker.md")
        count = fix_links.apply_fixes_to_file(
            str(vault), "Wiki/linker.md", fixes, links_filter=["real-one"],
        )
        assert count == 1
        content = (vault / "Wiki" / "linker.md").read_text()
        assert "[[Real One]]" in content
        assert "[[real-two]]" in content

    def test_dry_run_via_empty_fixes(self, vault, router):
        write_md(vault / "Wiki" / "Real.md",
                 {"type": "living/wiki", "tags": ["test"]}, "# R")
        write_md(vault / "Wiki" / "linker.md",
                 {"type": "living/wiki", "tags": ["test"]},
                 "See [[real]] nothing.")
        count = fix_links.apply_fixes_to_file(str(vault), "Wiki/linker.md", [])
        assert count == 0

    def test_filter_no_matches_zero(self, vault, router):
        write_md(vault / "Wiki" / "Real.md",
                 {"type": "living/wiki", "tags": ["test"]}, "# R")
        write_md(vault / "Wiki" / "linker.md",
                 {"type": "living/wiki", "tags": ["test"]},
                 "See [[real]] nothing.")
        fixes = fix_links.scan_and_resolve_file(str(vault), "Wiki/linker.md")
        count = fix_links.apply_fixes_to_file(
            str(vault), "Wiki/linker.md", fixes, links_filter=["not-there"],
        )
        assert count == 0


class TestAttachWikilinkWarnings:
    def test_apply_fixes_reuses_lazy_asset_cache_on_rescan(self, vault, monkeypatch):
        import _common._wikilinks as wikilinks

        assets = vault / "_Assets"
        assets.mkdir()
        (assets / "photo.png").write_bytes(b"\x89PNG")

        write_md(
            vault / "Wiki" / "Brain Inbox.md",
            {"type": "living/wiki", "tags": ["test"]},
            "# Brain Inbox",
        )
        write_md(
            vault / "Wiki" / "linker.md",
            {"type": "living/wiki", "tags": ["test"]},
            "See [[brain-inbox]].\n![[photo.png]]\n",
        )

        called = {"count": 0}
        original = wikilinks.build_vault_basename_index

        def spy(*args, **kwargs):
            called["count"] += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(wikilinks, "build_vault_basename_index", spy)

        file_index = file_index_from_documents(
            [
                {"path": "Wiki/Brain Inbox.md"},
                {"path": "Wiki/linker.md"},
            ],
            vault_root=str(vault),
        )
        result = {"path": "Wiki/linker.md"}

        fix_links.attach_wikilink_warnings(
            str(vault), result, apply_fixes=True, file_index=file_index
        )

        assert called["count"] == 1
        assert result["wikilink_fixes"]["applied"] == 1
        assert "wikilink_warnings" not in result
