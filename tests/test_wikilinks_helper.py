"""Tests for _common.check_wikilinks_in_file — per-file wikilink resolution helper."""

import _common as common
from _common._wikilinks import check_wikilinks_in_file


class TestCheckWikilinksInFile:
    def test_clean_file_returns_empty(self, vault):
        (vault / "Wiki" / "target.md").write_text("# Target\n")
        (vault / "Wiki" / "source.md").write_text("See [[target]].\n")
        findings = check_wikilinks_in_file(str(vault), "Wiki/source.md")
        assert findings == []

    def test_broken_link(self, vault):
        (vault / "Wiki" / "source.md").write_text("See [[nowhere]].\n")
        findings = check_wikilinks_in_file(str(vault), "Wiki/source.md")
        assert len(findings) == 1
        assert findings[0]["stem"] == "nowhere"
        assert findings[0]["status"] == "broken"
        assert findings[0]["resolved_to"] is None

    def test_ambiguous_link(self, vault):
        (vault / "Wiki" / "dup.md").write_text("# D1\n")
        (vault / "Designs" / "dup.md").write_text("# D2\n")
        (vault / "Wiki" / "source.md").write_text("See [[dup]].\n")
        findings = check_wikilinks_in_file(str(vault), "Wiki/source.md")
        assert len(findings) == 1
        assert findings[0]["status"] == "ambiguous"
        assert len(findings[0]["candidates"]) == 2

    def test_resolvable_via_slug_to_title(self, vault):
        (vault / "Wiki" / "My Page.md").write_text("# My Page\n")
        (vault / "Wiki" / "source.md").write_text("See [[my-page]].\n")
        findings = check_wikilinks_in_file(str(vault), "Wiki/source.md")
        assert len(findings) == 1
        assert findings[0]["status"] == "resolvable"
        assert findings[0]["resolved_to"] == "My Page"
        assert findings[0]["strategy"] == "slug_to_title"

    def test_scans_frontmatter_property_links(self, vault):
        """Wikilinks in YAML property values are real links and should be checked."""
        (vault / "Wiki" / "source.md").write_text(
            "---\ntype: notes/wiki\nlinks:\n  - [[nowhere]]\n---\n# Title\n"
        )
        findings = check_wikilinks_in_file(str(vault), "Wiki/source.md")
        assert len(findings) == 1
        assert findings[0]["stem"] == "nowhere"
        assert findings[0]["status"] == "broken"

    def test_scans_frontmatter_valid_property_link(self, vault):
        (vault / "Wiki" / "target.md").write_text("# Target\n")
        (vault / "Wiki" / "source.md").write_text(
            "---\ntype: notes/wiki\nparent: \"[[target]]\"\n---\n# Title\n"
        )
        findings = check_wikilinks_in_file(str(vault), "Wiki/source.md")
        assert findings == []

    def test_skips_code_blocks(self, vault):
        (vault / "Wiki" / "source.md").write_text(
            "# Title\n\n```\n[[nowhere]]\n```\n"
        )
        findings = check_wikilinks_in_file(str(vault), "Wiki/source.md")
        assert findings == []

    def test_with_prebuilt_index_and_prefixes(self, vault):
        (vault / "Wiki" / "target.md").write_text("# Target\n")
        (vault / "Wiki" / "source.md").write_text("See [[target]] and [[nope]].\n")
        file_index = common.build_vault_file_index(str(vault))
        temporal_prefixes = common.discover_temporal_prefixes(file_index["md_basenames"])
        findings = check_wikilinks_in_file(
            str(vault), "Wiki/source.md",
            file_index=file_index,
            temporal_prefixes=temporal_prefixes,
        )
        assert len(findings) == 1
        assert findings[0]["stem"] == "nope"

    def test_missing_file_returns_empty(self, vault):
        findings = check_wikilinks_in_file(str(vault), "Wiki/missing.md")
        assert findings == []

    def test_embed_wikilink(self, vault):
        assets = vault / "_Assets"
        assets.mkdir(exist_ok=True)
        (assets / "photo.png").write_bytes(b"\x89PNG")
        (vault / "Wiki" / "source.md").write_text("Image: ![[photo.png]]\n")
        findings = check_wikilinks_in_file(str(vault), "Wiki/source.md")
        assert findings == []

    def test_path_qualified_wikilink(self, vault):
        (vault / "Wiki" / "target.md").write_text("# Target\n")
        (vault / "Wiki" / "source.md").write_text("See [[Wiki/target]].\n")
        findings = check_wikilinks_in_file(str(vault), "Wiki/source.md")
        assert findings == []

    def test_multiple_findings_per_file(self, vault):
        (vault / "Wiki" / "source.md").write_text(
            "See [[gone]] and [[also-gone]].\n"
        )
        findings = check_wikilinks_in_file(str(vault), "Wiki/source.md")
        assert len(findings) == 2
        stems = sorted(f["stem"] for f in findings)
        assert stems == ["also-gone", "gone"]
