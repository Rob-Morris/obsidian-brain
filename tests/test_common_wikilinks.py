"""Tests for _common._wikilinks — extraction, file index, and broken link resolution."""

import os

import pytest

import _common as common
from _common._wikilinks import discover_temporal_prefixes


# ---------------------------------------------------------------------------
# Wikilink extraction
# ---------------------------------------------------------------------------

class TestExtractWikilinks:
    def test_basic(self):
        results = common.extract_wikilinks("See [[my page]] for details.")
        assert len(results) == 1
        assert results[0]["stem"] == "my page"
        assert results[0]["anchor"] is None
        assert results[0]["alias"] is None
        assert results[0]["is_embed"] is False

    def test_anchor_and_alias(self):
        results = common.extract_wikilinks("See [[target#heading|display text]].")
        assert len(results) == 1
        r = results[0]
        assert r["stem"] == "target"
        assert r["anchor"] == "#heading"
        assert r["alias"] == "display text"

    def test_embed(self):
        results = common.extract_wikilinks("Image: ![[photo.png]]")
        assert len(results) == 1
        assert results[0]["stem"] == "photo.png"
        assert results[0]["is_embed"] is True

    def test_skips_anchor_only(self):
        results = common.extract_wikilinks("See [[#heading]] above.")
        assert results == []

    def test_skips_template_placeholder(self):
        results = common.extract_wikilinks("Yesterday: [[{{yesterday}}]]")
        assert results == []

    def test_multiple_links(self):
        text = "Links: [[page-a]], [[page-b#sec|alias]], and ![[img.png]]"
        results = common.extract_wikilinks(text)
        assert len(results) == 3
        stems = [r["stem"] for r in results]
        assert stems == ["page-a", "page-b", "img.png"]

    def test_start_offset(self):
        text = "Before [[target]] after"
        results = common.extract_wikilinks(text)
        assert results[0]["start"] == text.index("[[")

    def test_path_qualified(self):
        results = common.extract_wikilinks("See [[Wiki/my page]].")
        assert results[0]["stem"] == "Wiki/my page"


# ---------------------------------------------------------------------------
# Vault file index
# ---------------------------------------------------------------------------

class TestBuildVaultFileIndex:
    def test_indexes_md_files(self, vault):
        (vault / "Wiki" / "my-page.md").write_text("# My Page\n")
        idx = common.build_vault_file_index(str(vault))
        assert "my-page" in idx["md_basenames"]
        assert idx["md_basenames"]["my-page"] == ["Wiki/my-page.md"]

    def test_indexes_non_md_files(self, vault):
        assets = vault / "_Assets"
        assets.mkdir()
        (assets / "photo.png").write_bytes(b"\x89PNG")
        idx = common.build_vault_file_index(str(vault))
        assert "photo.png" in idx["all_basenames"]

    def test_md_relpaths(self, vault):
        (vault / "Wiki" / "my-page.md").write_text("# My Page\n")
        idx = common.build_vault_file_index(str(vault))
        assert "wiki/my-page" in idx["md_relpaths"]

    def test_skips_git_and_obsidian(self, vault):
        (vault / ".obsidian" / "config.json").write_text("{}")
        (vault / ".git").mkdir(exist_ok=True)
        (vault / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
        idx = common.build_vault_file_index(str(vault))
        assert not any("config.json" == os.path.basename(p)
                        for paths in idx["all_basenames"].values()
                        for p in paths
                        if ".obsidian" in p or ".git" in p)

    def test_case_insensitive_stems(self, vault):
        (vault / "Wiki" / "My-Page.md").write_text("# My Page\n")
        idx = common.build_vault_file_index(str(vault))
        assert "my-page" in idx["md_basenames"]

    def test_duplicate_basenames(self, vault):
        (vault / "Wiki" / "jwt-refresh.md").write_text("# JWT\n")
        (vault / "Designs" / "jwt-refresh.md").write_text("# JWT Design\n")
        idx = common.build_vault_file_index(str(vault))
        assert len(idx["md_basenames"]["jwt-refresh"]) == 2

    def test_excludes_top_level_archive(self, vault):
        archive = vault / "_Archive" / "Ideas" / "Brain"
        archive.mkdir(parents=True)
        (archive / "20260101-old-idea.md").write_text("# Old\n")
        idx = common.build_vault_file_index(str(vault))
        assert "20260101-old-idea" not in idx["md_basenames"]
        assert "20260101-old-idea.md" not in idx["all_basenames"]
        assert "_archive/ideas/brain/20260101-old-idea" not in idx["md_relpaths"]

    def test_excludes_per_type_archive(self, vault):
        archive = vault / "Ideas" / "_Archive"
        archive.mkdir(parents=True)
        (archive / "20260101-old-idea.md").write_text("# Old\n")
        idx = common.build_vault_file_index(str(vault))
        assert "20260101-old-idea" not in idx["md_basenames"]


# ---------------------------------------------------------------------------
# Broken wikilink resolution
# ---------------------------------------------------------------------------

class TestResolveBrokenLink:
    @pytest.fixture
    def vault_with_files(self, vault):
        """Vault with a realistic set of files for resolution testing."""
        (vault / "Wiki" / "Brain Inbox.md").write_text("# Brain Inbox\n")
        (vault / "Designs" / "Auth Redesign.md").write_text("# Auth\n")
        temporal = vault / "_Temporal" / "Research" / "2026-03"
        temporal.mkdir(parents=True, exist_ok=True)
        (temporal / "20260325-research~Foo Bar.md").write_text("# Foo\n")
        plans = vault / "_Temporal" / "Plans" / "2026-03"
        plans.mkdir(parents=True, exist_ok=True)
        (plans / "20260317-plan~My Plan.md").write_text("# Plan\n")
        logs = vault / "_Temporal" / "Idea Logs" / "2026-03"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "20260324-idea-log~Brain Heartbeat System.md").write_text("# Idea\n")
        archive = vault / "Designs" / "_Archive"
        archive.mkdir(parents=True, exist_ok=True)
        (archive / "20260317-Old Design.md").write_text("# Old\n")
        return vault

    def _index(self, vault):
        return common.build_vault_file_index(str(vault))

    def test_slug_to_title(self, vault_with_files):
        idx = self._index(vault_with_files)
        r = common.resolve_broken_link("brain-inbox", idx)
        assert r.status == "resolved"
        assert r.resolved_to == "Brain Inbox"
        assert r.strategy == "slug_to_title"

    def test_doubledash_to_tilde(self, vault_with_files):
        idx = self._index(vault_with_files)
        r = common.resolve_broken_link("20260324-idea-log--brain-heartbeat-system", idx)
        assert r.status == "resolved"
        assert r.resolved_to == "20260324-idea-log~Brain Heartbeat System"
        assert r.strategy == "doubledash_to_tilde"

    def test_dated_slug_prefix(self, vault_with_files):
        idx = self._index(vault_with_files)
        r = common.resolve_broken_link("20260325-foo-bar", idx)
        assert r.status == "resolved"
        assert r.resolved_to == "20260325-research~Foo Bar"
        assert "dated_slug_prefix:research" == r.strategy

    def test_trailing_backslash(self, vault_with_files):
        idx = self._index(vault_with_files)
        r = common.resolve_broken_link("brain-inbox\\", idx)
        assert r.status == "resolved"
        assert r.resolved_to == "Brain Inbox"
        assert r.strategy.startswith("trailing_backslash")

    def test_path_stripping(self, vault_with_files):
        idx = self._index(vault_with_files)
        r = common.resolve_broken_link("Designs/auth-redesign", idx)
        assert r.status == "resolved"
        assert r.resolved_to == "Auth Redesign"
        assert "path_strip" in r.strategy

    def test_tilde_space(self, vault_with_files):
        idx = self._index(vault_with_files)
        r = common.resolve_broken_link("20260317-plan~ My Plan", idx)
        assert r.status == "resolved"
        assert r.resolved_to == "20260317-plan~My Plan"
        assert r.strategy == "tilde_space"

    def test_ambiguous(self, vault_with_files):
        vault = vault_with_files
        # Create a second file that slug_to_title would also match
        (vault / "Ideas").mkdir(exist_ok=True)
        (vault / "Ideas" / "Brain Inbox.md").write_text("# Dup\n")
        idx = self._index(vault)
        r = common.resolve_broken_link("brain-inbox", idx)
        assert r.status == "ambiguous"
        assert len(r.candidates) == 2

    def test_unresolvable(self, vault_with_files):
        idx = self._index(vault_with_files)
        r = common.resolve_broken_link("completely-nonexistent-thing", idx)
        assert r.status == "unresolvable"
        assert r.resolved_to is None
        assert r.candidates == []

    def test_path_segment_title_casing(self, vault_with_files):
        idx = self._index(vault_with_files)
        r = common.resolve_broken_link("Designs/auth-redesign", idx)
        assert r.status == "resolved"
        assert r.resolved_to == "Auth Redesign"

    def test_temporal_prefix_discovery(self, vault_with_files):
        idx = self._index(vault_with_files)
        prefixes = discover_temporal_prefixes(idx["md_basenames"])
        assert "research" in prefixes
        assert "plan" in prefixes
        assert "idea-log" in prefixes


class TestResolveArtefactPath:
    @pytest.fixture
    def vault_with_files(self, vault):
        (vault / "Wiki").mkdir(exist_ok=True)
        (vault / "Wiki" / "Brain Inbox.md").write_text("# Brain Inbox\n")
        (vault / "Ideas").mkdir(exist_ok=True)
        (vault / "Ideas" / "My Idea.md").write_text("# My Idea\n")
        temporal = vault / "_Temporal" / "Reports" / "2026-03"
        temporal.mkdir(parents=True, exist_ok=True)
        (temporal / "20260329-report~Broken Link Prevention Briefing.md").write_text("# Report\n")
        return vault

    def test_exact_basename_resolves(self, vault_with_files):
        result = common.resolve_artefact_path("Brain Inbox", vault_with_files)
        assert result == "Wiki/Brain Inbox.md"

    def test_basename_with_md_extension(self, vault_with_files):
        result = common.resolve_artefact_path("Brain Inbox.md", vault_with_files)
        assert result == "Wiki/Brain Inbox.md"

    def test_case_insensitive(self, vault_with_files):
        result = common.resolve_artefact_path("brain inbox", vault_with_files)
        assert result == "Wiki/Brain Inbox.md"

    def test_no_match_raises(self, vault_with_files):
        with pytest.raises(ValueError, match="No artefact found"):
            common.resolve_artefact_path("nonexistent", vault_with_files)

    def test_ambiguous_raises_with_candidates(self, vault_with_files):
        # Create a duplicate basename in a different folder
        other = vault_with_files / "Notes"
        other.mkdir(exist_ok=True)
        (other / "Brain Inbox.md").write_text("# Duplicate\n")
        with pytest.raises(ValueError, match="matches multiple files"):
            common.resolve_artefact_path("Brain Inbox", vault_with_files)

    def test_partial_path_extracts_basename(self, vault_with_files):
        result = common.resolve_artefact_path("Wrong/Folder/My Idea", vault_with_files)
        assert result == "Ideas/My Idea.md"

    def test_temporal_basename(self, vault_with_files):
        result = common.resolve_artefact_path(
            "20260329-report~Broken Link Prevention Briefing", vault_with_files
        )
        assert result == "_Temporal/Reports/2026-03/20260329-report~Broken Link Prevention Briefing.md"

    def test_temporal_display_name_resolves(self, vault_with_files):
        """Looking up by display name (without dated prefix) should find the temporal artefact."""
        result = common.resolve_artefact_path(
            "Broken Link Prevention Briefing", vault_with_files
        )
        assert result == "_Temporal/Reports/2026-03/20260329-report~Broken Link Prevention Briefing.md"
