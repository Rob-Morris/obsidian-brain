"""Tests for _common.py — shared utilities for brain-core scripts."""

import os
import tempfile

import pytest

import _common as common
import list_artefacts as la


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def vault(tmp_path):
    """Create a minimal vault fixture in a temp directory."""
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("1.2.3\n")

    config = tmp_path / "_Config"
    config.mkdir()

    # Living types
    (tmp_path / "Wiki").mkdir()
    (tmp_path / "Designs").mkdir()
    (tmp_path / "Daily Notes").mkdir()

    # System dirs (should be excluded)
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / "_Plugins").mkdir()

    # Temporal types
    temporal = tmp_path / "_Temporal"
    temporal.mkdir()
    (temporal / "Logs").mkdir()
    (temporal / "Plans").mkdir()
    (temporal / "Research").mkdir()
    (temporal / ".hidden").mkdir()  # should be excluded

    return tmp_path


# ---------------------------------------------------------------------------
# _is_vault_root
# ---------------------------------------------------------------------------

class TestIsVaultRoot:
    def test_vault_with_version(self, vault):
        assert common._is_vault_root(vault) is True

    def test_vault_with_agents_md(self, tmp_path):
        (tmp_path / "Agents.md").write_text("agent entry\n")
        assert common._is_vault_root(tmp_path) is True

    def test_non_vault(self, tmp_path):
        assert common._is_vault_root(tmp_path) is False


# ---------------------------------------------------------------------------
# find_vault_root
# ---------------------------------------------------------------------------

class TestFindVaultRoot:
    def test_finds_vault_from_cwd(self, vault, monkeypatch):
        monkeypatch.chdir(vault)
        assert common.find_vault_root() == vault

    def test_with_vault_arg(self, vault):
        result = common.find_vault_root(str(vault))
        assert result == vault

    def test_invalid_vault_arg_exits(self, tmp_path):
        with pytest.raises(SystemExit):
            common.find_vault_root(str(tmp_path))


# ---------------------------------------------------------------------------
# read_version
# ---------------------------------------------------------------------------

class TestReadVersion:
    def test_reads_version(self, vault):
        assert common.read_version(vault) == "1.2.3"

    def test_reads_version_with_string_path(self, vault):
        assert common.read_version(str(vault)) == "1.2.3"


# ---------------------------------------------------------------------------
# is_system_dir
# ---------------------------------------------------------------------------

class TestIsSystemDir:
    def test_underscore_prefix(self):
        assert common.is_system_dir("_Config") is True
        assert common.is_system_dir("_Temporal") is True

    def test_dot_prefix(self):
        assert common.is_system_dir(".obsidian") is True
        assert common.is_system_dir(".brain-core") is True

    def test_regular_dir(self):
        assert common.is_system_dir("Wiki") is False
        assert common.is_system_dir("Daily Notes") is False


class TestIsArchivedPath:
    def test_type_root_archive(self):
        assert common.is_archived_path("Ideas/_Archive/20260101-old.md") is True

    def test_project_subfolder_archive(self):
        assert common.is_archived_path("Ideas/Brain/_Archive/20260101-old.md") is True

    def test_regular_path(self):
        assert common.is_archived_path("Ideas/my-idea.md") is False

    def test_plus_status_path(self):
        assert common.is_archived_path("Ideas/+Adopted/my-idea.md") is False

    def test_archive_in_filename_not_matched(self):
        assert common.is_archived_path("Ideas/_Archive-notes.md") is False


# ---------------------------------------------------------------------------
# scan_living_types
# ---------------------------------------------------------------------------

class TestScanLivingTypes:
    def test_discovers_living_types(self, vault):
        types = common.scan_living_types(vault)
        keys = [t["key"] for t in types]
        assert "wiki" in keys
        assert "designs" in keys
        assert "daily-notes" in keys

    def test_excludes_system_dirs(self, vault):
        types = common.scan_living_types(vault)
        keys = [t["key"] for t in types]
        assert "_config" not in keys
        assert ".obsidian" not in keys

    def test_includes_all_fields(self, vault):
        types = common.scan_living_types(vault)
        wiki = next(t for t in types if t["key"] == "wiki")
        assert wiki["folder"] == "Wiki"
        assert wiki["classification"] == "living"
        assert wiki["type"] == "living/wiki"
        assert wiki["path"] == "Wiki"

    def test_space_to_dash_in_key(self, vault):
        types = common.scan_living_types(vault)
        dn = next(t for t in types if t["folder"] == "Daily Notes")
        assert dn["key"] == "daily-notes"


# ---------------------------------------------------------------------------
# scan_temporal_types
# ---------------------------------------------------------------------------

class TestScanTemporalTypes:
    def test_discovers_temporal_types(self, vault):
        types = common.scan_temporal_types(vault)
        keys = [t["key"] for t in types]
        assert "logs" in keys
        assert "plans" in keys
        assert "research" in keys

    def test_excludes_hidden_dirs(self, vault):
        types = common.scan_temporal_types(vault)
        keys = [t["key"] for t in types]
        assert ".hidden" not in keys

    def test_includes_all_fields(self, vault):
        types = common.scan_temporal_types(vault)
        logs = next(t for t in types if t["key"] == "logs")
        assert logs["folder"] == "Logs"
        assert logs["classification"] == "temporal"
        assert logs["type"] == "temporal/logs"
        assert logs["path"] == os.path.join("_Temporal", "Logs")

    def test_no_temporal_dir(self, tmp_path):
        bc = tmp_path / ".brain-core"
        bc.mkdir()
        (bc / "VERSION").write_text("1.0.0\n")
        assert common.scan_temporal_types(tmp_path) == []


# ---------------------------------------------------------------------------
# parse_frontmatter
# ---------------------------------------------------------------------------

class TestParseFrontmatter:
    def test_basic_fields(self):
        text = "---\ntype: living/wiki\nstatus: active\n---\n\n# Title\n\nBody"
        fields, body = common.parse_frontmatter(text)
        assert fields["type"] == "living/wiki"
        assert fields["status"] == "active"
        assert "# Title" in body

    def test_inline_tags(self):
        text = "---\ntype: x\ntags: [foo, bar]\n---\nBody"
        fields, body = common.parse_frontmatter(text)
        assert fields["tags"] == ["foo", "bar"]

    def test_multiline_tags(self):
        text = "---\ntype: x\ntags:\n  - alpha\n  - beta\n---\nBody"
        fields, body = common.parse_frontmatter(text)
        assert fields["tags"] == ["alpha", "beta"]

    def test_no_frontmatter(self):
        text = "# Just a heading\n\nBody text"
        fields, body = common.parse_frontmatter(text)
        assert fields == {}
        assert body == text

    def test_empty_value_becomes_empty_list(self):
        text = "---\ntype: living/wiki\nempty_field:\nstatus: active\n---\nBody"
        fields, body = common.parse_frontmatter(text)
        assert fields["empty_field"] == []
        assert fields["type"] == "living/wiki"
        assert fields["status"] == "active"

    def test_multiline_aliases(self):
        text = "---\ntype: x\naliases:\n  - brain-master-design\n  - master-design\n---\nBody"
        fields, body = common.parse_frontmatter(text)
        assert fields["aliases"] == ["brain-master-design", "master-design"]

    def test_inline_aliases(self):
        text = "---\ntype: x\naliases: [foo, bar]\n---\nBody"
        fields, body = common.parse_frontmatter(text)
        assert fields["aliases"] == ["foo", "bar"]

    def test_quoted_values(self):
        text = "---\ntitle: 'Hello World'\n---\nBody"
        fields, _ = common.parse_frontmatter(text)
        assert fields["title"] == "Hello World"


# ---------------------------------------------------------------------------
# title_to_slug
# ---------------------------------------------------------------------------

class TestTitleToSlug:
    def test_basic_title(self):
        assert common.title_to_slug("My Great Idea") == "my-great-idea"

    def test_special_characters(self):
        assert common.title_to_slug("Hello, World! (2026)") == "hello-world-2026"

    def test_unicode(self):
        assert common.title_to_slug("Café Résumé") == "cafe-resume"

    def test_multiple_spaces(self):
        assert common.title_to_slug("  lots   of   spaces  ") == "lots-of-spaces"

    def test_hyphens_and_underscores(self):
        assert common.title_to_slug("already-has-hyphens_and_underscores") == "already-has-hyphens-and-underscores"

    def test_all_special(self):
        assert common.title_to_slug("!!!") == ""

    def test_single_word(self):
        assert common.title_to_slug("Python") == "python"

    def test_numbers(self):
        assert common.title_to_slug("3 Ways to Code") == "3-ways-to-code"


# ---------------------------------------------------------------------------
# title_to_filename
# ---------------------------------------------------------------------------

class TestTitleToFilename:
    def test_basic_title(self):
        assert common.title_to_filename("My Project") == "My Project"

    def test_preserves_caps(self):
        assert common.title_to_filename("API Refactor") == "API Refactor"

    def test_strips_unsafe_chars(self):
        assert common.title_to_filename('Rob\'s Q3/Q4 Review') == "Rob's Q3Q4 Review"

    def test_strips_all_unsafe(self):
        assert common.title_to_filename('a/b\\c:d*e?f"g<h>i|j') == "abcdefghij"

    def test_preserves_unicode(self):
        assert common.title_to_filename("Café Notes") == "Café Notes"

    def test_trims_whitespace(self):
        assert common.title_to_filename("  My Project  ") == "My Project"

    def test_collapses_spaces(self):
        assert common.title_to_filename("lots   of   spaces") == "lots of spaces"

    def test_collapses_spaces_from_stripped_chars(self):
        # Stripping / leaves double space which gets collapsed
        assert common.title_to_filename("Q3 / Q4 Review") == "Q3 Q4 Review"

    def test_empty_title(self):
        assert common.title_to_filename("") == ""

    def test_all_unsafe(self):
        assert common.title_to_filename('/:*?"<>|') == ""

    def test_hyphens_preserved(self):
        assert common.title_to_filename("brain-core") == "brain-core"

    def test_underscores_preserved(self):
        assert common.title_to_filename("my_project") == "my_project"

    def test_numbers(self):
        assert common.title_to_filename("3 Ways to Code") == "3 Ways to Code"

    def test_parentheses_preserved(self):
        assert common.title_to_filename("Hello (World)") == "Hello (World)"


# ---------------------------------------------------------------------------
# serialize_frontmatter
# ---------------------------------------------------------------------------

class TestSerializeFrontmatter:
    def test_basic_fields(self):
        result = common.serialize_frontmatter({"type": "living/wiki", "status": "active"})
        assert result.startswith("---\n")
        assert "type: living/wiki\n" in result
        assert "status: active\n" in result

    def test_tags_list(self):
        result = common.serialize_frontmatter({"tags": ["brain-core", "overview"]})
        assert "tags:\n  - brain-core\n  - overview\n" in result

    def test_empty_tags(self):
        result = common.serialize_frontmatter({"tags": []})
        assert "tags: []\n" in result

    def test_with_body(self):
        result = common.serialize_frontmatter({"type": "x"}, body="# Title\n\nBody text")
        assert result.endswith("# Title\n\nBody text")

    def test_empty_body(self):
        result = common.serialize_frontmatter({"type": "x"})
        assert result.endswith("---\n")

    def test_roundtrip_with_parse(self):
        original_fields = {"type": "living/wiki", "status": "active"}
        original_body = "# Title\n\nBody content.\n"
        serialized = common.serialize_frontmatter(original_fields, body=original_body)
        parsed_fields, parsed_body = common.parse_frontmatter(serialized)
        assert parsed_fields["type"] == "living/wiki"
        assert parsed_fields["status"] == "active"
        assert "# Title" in parsed_body
        assert "Body content." in parsed_body

    def test_roundtrip_tags(self):
        original_fields = {"type": "x", "tags": ["alpha", "beta"]}
        serialized = common.serialize_frontmatter(original_fields)
        parsed_fields, _ = common.parse_frontmatter(serialized)
        assert parsed_fields["tags"] == ["alpha", "beta"]

    def test_aliases_list(self):
        result = common.serialize_frontmatter({"aliases": ["brain-master", "master"]})
        assert "aliases:\n  - brain-master\n  - master\n" in result

    def test_roundtrip_aliases(self):
        original_fields = {"type": "x", "aliases": ["brain-master-design"]}
        serialized = common.serialize_frontmatter(original_fields)
        parsed_fields, _ = common.parse_frontmatter(serialized)
        assert parsed_fields["aliases"] == ["brain-master-design"]

    def test_roundtrip_multiple_list_fields(self):
        original_fields = {"type": "x", "tags": ["a", "b"], "aliases": ["c"], "cssclasses": ["d"]}
        serialized = common.serialize_frontmatter(original_fields)
        parsed_fields, _ = common.parse_frontmatter(serialized)
        assert parsed_fields["tags"] == ["a", "b"]
        assert parsed_fields["aliases"] == ["c"]
        assert parsed_fields["cssclasses"] == ["d"]


# ---------------------------------------------------------------------------
# tokenise
# ---------------------------------------------------------------------------

class TestTokenise:
    def test_basic_tokenisation(self):
        tokens = common.tokenise("Hello World 123")
        assert "hello" in tokens
        assert "world" in tokens
        assert "123" in tokens

    def test_strips_short_tokens(self):
        tokens = common.tokenise("I am a great coder")
        assert "i" not in tokens
        assert "am" in tokens
        assert "a" not in tokens
        assert "great" in tokens

    def test_splits_on_non_alphanumeric(self):
        tokens = common.tokenise("foo-bar_baz.qux")
        assert "foo" in tokens
        assert "bar" in tokens
        assert "baz" in tokens
        assert "qux" in tokens

    def test_empty_string(self):
        assert common.tokenise("") == []


# ---------------------------------------------------------------------------
# TEMPORAL_DIR constant
# ---------------------------------------------------------------------------

def test_temporal_dir_constant():
    assert common.TEMPORAL_DIR == "_Temporal"


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
        prefixes = common._discover_temporal_prefixes(idx["md_basenames"])
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


# ---------------------------------------------------------------------------
# resolve_and_check_bounds
# ---------------------------------------------------------------------------

class TestResolveAndCheckBounds:
    def test_path_within_bounds(self, tmp_path):
        target = tmp_path / "sub" / "file.txt"
        result = common.resolve_and_check_bounds(target, tmp_path)
        assert result == str(target)

    def test_path_at_bounds_root(self, tmp_path):
        target = tmp_path / "file.txt"
        result = common.resolve_and_check_bounds(target, tmp_path)
        assert result == str(target)

    def test_path_outside_bounds(self, tmp_path):
        target = tmp_path / ".." / "escape.txt"
        with pytest.raises(ValueError, match="outside allowed boundary"):
            common.resolve_and_check_bounds(target, tmp_path)

    def test_symlink_resolved_within_bounds(self, tmp_path):
        real = tmp_path / "real.txt"
        real.write_text("x")
        link = tmp_path / "link.txt"
        link.symlink_to(real)
        result = common.resolve_and_check_bounds(link, tmp_path)
        assert result == str(real)

    def test_symlink_resolved_outside_bounds(self, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        real = outside / "real.txt"
        real.write_text("x")
        bounded = tmp_path / "bounded"
        bounded.mkdir()
        link = bounded / "link.txt"
        link.symlink_to(real)
        with pytest.raises(ValueError, match="outside allowed boundary"):
            common.resolve_and_check_bounds(link, bounded)

    def test_follow_symlinks_false_rejects_symlink(self, tmp_path):
        real = tmp_path / "real.txt"
        real.write_text("x")
        link = tmp_path / "link.txt"
        link.symlink_to(real)
        with pytest.raises(ValueError, match="Refusing to follow symlink"):
            common.resolve_and_check_bounds(link, tmp_path, follow_symlinks=False)

    def test_follow_symlinks_false_allows_regular_file(self, tmp_path):
        target = tmp_path / "file.txt"
        result = common.resolve_and_check_bounds(target, tmp_path, follow_symlinks=False)
        assert result == str(target)

    def test_prefix_collision(self, tmp_path):
        """'/home/foo' must not match bounds '/home/fo'."""
        bounds = tmp_path / "fo"
        bounds.mkdir()
        target = tmp_path / "foobar" / "file.txt"
        with pytest.raises(ValueError, match="outside allowed boundary"):
            common.resolve_and_check_bounds(target, bounds)


# ---------------------------------------------------------------------------
# safe_write
# ---------------------------------------------------------------------------

class TestSafeWrite:
    def test_basic_write_new_file(self, tmp_path):
        target = tmp_path / "out.txt"
        result = common.safe_write(target, "hello")
        assert result == str(target)
        assert target.read_text() == "hello"

    def test_overwrite_existing(self, tmp_path):
        target = tmp_path / "out.txt"
        target.write_text("old")
        common.safe_write(target, "new")
        assert target.read_text() == "new"

    def test_creates_parent_directories(self, tmp_path):
        target = tmp_path / "a" / "b" / "c" / "file.txt"
        common.safe_write(target, "deep")
        assert target.read_text() == "deep"

    def test_exclusive_fails_if_exists(self, tmp_path):
        target = tmp_path / "out.txt"
        target.write_text("existing")
        with pytest.raises(FileExistsError):
            common.safe_write(target, "new", exclusive=True)
        assert target.read_text() == "existing"

    def test_exclusive_succeeds_if_new(self, tmp_path):
        target = tmp_path / "out.txt"
        common.safe_write(target, "new", exclusive=True)
        assert target.read_text() == "new"

    def test_symlink_followed(self, tmp_path):
        real = tmp_path / "real.txt"
        real.write_text("old")
        link = tmp_path / "link.txt"
        link.symlink_to(real)
        common.safe_write(link, "updated")
        assert real.read_text() == "updated"

    def test_symlink_with_bounds_inside(self, tmp_path):
        real = tmp_path / "real.txt"
        real.write_text("old")
        link = tmp_path / "link.txt"
        link.symlink_to(real)
        common.safe_write(link, "updated", bounds=tmp_path)
        assert real.read_text() == "updated"

    def test_symlink_with_bounds_outside(self, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        real = outside / "real.txt"
        real.write_text("old")
        bounded = tmp_path / "bounded"
        bounded.mkdir()
        link = bounded / "link.txt"
        link.symlink_to(real)
        with pytest.raises(ValueError, match="outside allowed boundary"):
            common.safe_write(link, "new", bounds=bounded)
        assert real.read_text() == "old"

    def test_follow_symlinks_false_rejects(self, tmp_path):
        real = tmp_path / "real.txt"
        real.write_text("old")
        link = tmp_path / "link.txt"
        link.symlink_to(real)
        with pytest.raises(ValueError, match="Refusing to follow symlink"):
            common.safe_write(link, "new", follow_symlinks=False)

    def test_no_stale_tmp_on_failure(self, tmp_path, monkeypatch):
        target = tmp_path / "out.txt"

        def failing_replace(src, dst):
            raise OSError("simulated failure")

        monkeypatch.setattr("os.replace", failing_replace)
        with pytest.raises(OSError, match="simulated failure"):
            common.safe_write(target, "content")
        remaining = list(tmp_path.iterdir())
        assert not any(str(f).endswith(".tmp") for f in remaining)


# ---------------------------------------------------------------------------
# safe_write_json
# ---------------------------------------------------------------------------

class TestSafeWriteJson:
    def test_writes_valid_json(self, tmp_path):
        import json
        target = tmp_path / "data.json"
        common.safe_write_json(target, {"key": "value"})
        content = target.read_text()
        assert content.endswith("\n")
        assert json.loads(content) == {"key": "value"}

    def test_custom_indent(self, tmp_path):
        target = tmp_path / "data.json"
        common.safe_write_json(target, {"a": 1}, indent=4)
        assert '    "a": 1' in target.read_text()

    def test_with_bounds(self, tmp_path):
        target = tmp_path / "data.json"
        common.safe_write_json(target, {"a": 1}, bounds=tmp_path)
        assert target.exists()

    def test_unicode_preserved(self, tmp_path):
        target = tmp_path / "data.json"
        common.safe_write_json(target, {"emoji": "\U0001f600"})
        assert "\U0001f600" in target.read_text()


# ---------------------------------------------------------------------------
# match_artefact
# ---------------------------------------------------------------------------

class TestMatchArtefact:
    """Test match_artefact against all type representations."""

    @pytest.fixture
    def artefacts(self):
        return [
            {"key": "ideas", "type": "living/ideas", "frontmatter_type": "living/idea"},
            {"key": "journal-entries", "type": "living/journal-entries", "frontmatter_type": "living/journal-entry"},
            {"key": "wiki", "type": "living/wiki", "frontmatter_type": "living/wiki"},
            {"key": "research", "type": "temporal/research", "frontmatter_type": "temporal/research"},
            {"key": "logs", "type": "temporal/logs", "frontmatter_type": "temporal/log"},
            {"key": "people", "type": "living/people", "frontmatter_type": "living/person"},
        ]

    def test_match_by_key(self, artefacts):
        assert common.match_artefact(artefacts, "ideas")["key"] == "ideas"

    def test_match_by_full_type(self, artefacts):
        assert common.match_artefact(artefacts, "living/ideas")["key"] == "ideas"

    def test_match_by_frontmatter_type(self, artefacts):
        assert common.match_artefact(artefacts, "living/idea")["key"] == "ideas"

    def test_match_by_bare_singular(self, artefacts):
        """Bare singular 'idea' matches frontmatter_type 'living/idea'."""
        assert common.match_artefact(artefacts, "idea")["key"] == "ideas"

    def test_match_journal_entry_singular(self, artefacts):
        """Hyphenated singular that removesuffix('s') can't handle."""
        assert common.match_artefact(artefacts, "journal-entry")["key"] == "journal-entries"

    def test_match_person_singular(self, artefacts):
        """Irregular plural that removesuffix('s') can't handle."""
        assert common.match_artefact(artefacts, "person")["key"] == "people"

    def test_match_where_singular_equals_plural(self, artefacts):
        """Types like 'research' where singular == plural."""
        assert common.match_artefact(artefacts, "research")["key"] == "research"

    def test_no_match_returns_none(self, artefacts):
        assert common.match_artefact(artefacts, "nonexistent") is None

    def test_no_match_with_slash_returns_none(self, artefacts):
        assert common.match_artefact(artefacts, "living/nonexistent") is None


# ---------------------------------------------------------------------------
# TestListArtefactsTypeFilter
# ---------------------------------------------------------------------------

class TestListArtefactsTypeFilter:
    """Verify list_artefacts matches index docs using frontmatter_type."""

    @pytest.fixture
    def index(self):
        return {
            "documents": [
                {"path": "Ideas/foo.md", "type": "living/idea", "tags": [], "status": "new", "modified": "2026-04-01", "title": "foo"},
                {"path": "Ideas/bar.md", "type": "living/idea", "tags": [], "status": "new", "modified": "2026-04-02", "title": "bar"},
                {"path": "Wiki/baz.md", "type": "living/wiki", "tags": [], "status": None, "modified": "2026-04-03", "title": "baz"},
            ],
        }

    @pytest.fixture
    def router(self):
        return {
            "artefacts": [
                {"key": "ideas", "type": "living/ideas", "frontmatter_type": "living/idea"},
                {"key": "wiki", "type": "living/wiki", "frontmatter_type": "living/wiki"},
            ],
        }

    def test_filter_by_plural_key(self, index, router):
        results = la.list_artefacts(index, router, type_filter="ideas")
        assert len(results) == 2
        assert all(r["type"] == "living/idea" for r in results)

    def test_filter_by_singular(self, index, router):
        results = la.list_artefacts(index, router, type_filter="idea")
        assert len(results) == 2

    def test_filter_by_full_singular(self, index, router):
        results = la.list_artefacts(index, router, type_filter="living/idea")
        assert len(results) == 2

    def test_filter_by_full_plural(self, index, router):
        results = la.list_artefacts(index, router, type_filter="living/ideas")
        assert len(results) == 2

    def test_no_filter_returns_all(self, index, router):
        results = la.list_artefacts(index, router, type_filter=None)
        assert len(results) == 3


# ---------------------------------------------------------------------------
# resolve_body_file — path boundary checks
# ---------------------------------------------------------------------------

@pytest.fixture
def non_tmp_vault():
    """Vault directory outside the system temp dir."""
    import shutil
    vault_dir = os.path.join(os.path.expanduser("~"), ".brain-test-vault")
    os.makedirs(os.path.join(vault_dir, "Wiki"), exist_ok=True)
    os.makedirs(os.path.join(vault_dir, ".brain-core"), exist_ok=True)
    with open(os.path.join(vault_dir, ".brain-core", "VERSION"), "w") as f:
        f.write("1.0.0\n")
    yield vault_dir
    shutil.rmtree(vault_dir, ignore_errors=True)


class TestResolveBodyFile:
    """Tests for resolve_body_file with vault_root boundary enforcement.

    Note: pytest's tmp_path lives under the system temp directory, so we
    create a non-tmp vault directory under the *home* dir for tests that
    need to distinguish vault-reads from tmp-reads.
    """

    def test_body_only(self):
        body, cleanup = common.resolve_body_file("hello", "")
        assert body == "hello"
        assert cleanup is None

    def test_both_raises(self, tmp_path):
        f = tmp_path / "f.txt"
        f.write_text("x")
        with pytest.raises(ValueError, match="Cannot specify both"):
            common.resolve_body_file("body", str(f))

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(ValueError, match="not found"):
            common.resolve_body_file("", str(tmp_path / "nope.txt"), vault_root=str(tmp_path))

    def test_vault_file_ok_no_cleanup(self, non_tmp_vault):
        f = os.path.join(non_tmp_vault, "Wiki", "source.md")
        with open(f, "w") as fh:
            fh.write("vault content")
        body, cleanup = common.resolve_body_file("", f, vault_root=non_tmp_vault)
        assert body == "vault content"
        assert cleanup is None
        assert os.path.exists(f), "vault file must not be deleted"

    def test_tmp_file_returns_cleanup_path(self, non_tmp_vault):
        tmp_dir = tempfile.gettempdir()
        tmp_file = os.path.join(tmp_dir, "brain-test-body.txt")
        try:
            with open(tmp_file, "w") as fh:
                fh.write("tmp content")
            body, cleanup = common.resolve_body_file("", tmp_file, vault_root=non_tmp_vault)
            assert body == "tmp content"
            assert cleanup == os.path.realpath(tmp_file)
        finally:
            if os.path.exists(tmp_file):
                os.remove(tmp_file)

    def test_outside_vault_and_tmp_raises(self, non_tmp_vault):
        outside = os.path.join(non_tmp_vault, "..", "outside-secret.txt")
        with open(outside, "w") as fh:
            fh.write("secret")
        try:
            with pytest.raises(ValueError, match="outside allowed boundary"):
                common.resolve_body_file("", outside, vault_root=non_tmp_vault)
        finally:
            os.remove(outside)

    def test_symlink_escape_raises(self, non_tmp_vault):
        # Create a file outside the vault
        outside = os.path.join(non_tmp_vault, "..", "symlink-target.txt")
        with open(outside, "w") as fh:
            fh.write("secret")
        link = os.path.join(non_tmp_vault, "Wiki", "escape.md")
        try:
            os.symlink(outside, link)
            with pytest.raises(ValueError, match="outside allowed boundary"):
                common.resolve_body_file("", link, vault_root=non_tmp_vault)
        finally:
            os.unlink(link)
            os.remove(outside)

    def test_no_vault_root_allows_any_path(self, tmp_path):
        """Without vault_root (CLI mode), any readable path works."""
        f = tmp_path / "anywhere.txt"
        f.write_text("anything")
        body, cleanup = common.resolve_body_file("", str(f))
        assert body == "anything"
        assert cleanup is None


# ---------------------------------------------------------------------------
# make_temp_path
# ---------------------------------------------------------------------------

class TestMakeTempPath:
    def test_returns_writable_path(self):
        path = common.make_temp_path()
        try:
            assert os.path.exists(path)
            with open(path, "w") as f:
                f.write("test")
            with open(path) as f:
                assert f.read() == "test"
        finally:
            os.remove(path)

    def test_default_suffix_is_md(self):
        path = common.make_temp_path()
        try:
            assert path.endswith(".md")
        finally:
            os.remove(path)

    def test_custom_suffix(self):
        path = common.make_temp_path(suffix=".txt")
        try:
            assert path.endswith(".txt")
        finally:
            os.remove(path)

    def test_path_inside_system_temp_dir(self):
        path = common.make_temp_path()
        try:
            real_path = os.path.realpath(path)
            real_tmp = os.path.realpath(tempfile.gettempdir())
            assert real_path.startswith(real_tmp)
        finally:
            os.remove(path)

    def test_resolve_body_file_accepts_make_temp_path(self, non_tmp_vault):
        """make_temp_path output is accepted by resolve_body_file."""
        path = common.make_temp_path()
        try:
            with open(path, "w") as f:
                f.write("staged content")
            body, cleanup = common.resolve_body_file("", path, vault_root=non_tmp_vault)
            assert body == "staged content"
            assert cleanup == os.path.realpath(path)
        finally:
            if os.path.exists(path):
                os.remove(path)


# ---------------------------------------------------------------------------
# check_write_allowed
# ---------------------------------------------------------------------------

class TestCheckWriteAllowed:
    """Write guard: block dot-prefixed and protected underscore folders."""

    # -- Dot-prefixed: always blocked --

    def test_dot_brain_blocked(self):
        with pytest.raises(ValueError, match="dot-prefixed"):
            common.check_write_allowed(".brain/local/index.json")

    def test_dot_brain_core_blocked(self):
        with pytest.raises(ValueError, match="dot-prefixed"):
            common.check_write_allowed(".brain-core/scripts/foo.py")

    def test_dot_obsidian_blocked(self):
        with pytest.raises(ValueError, match="dot-prefixed"):
            common.check_write_allowed(".obsidian/config")

    # -- Underscore-prefixed: blocked unless in allowlist --

    def test_plugins_blocked(self):
        with pytest.raises(ValueError, match="protected folder"):
            common.check_write_allowed("_Plugins/my-plugin/SKILL.md")

    def test_workspaces_blocked(self):
        with pytest.raises(ValueError, match="protected folder"):
            common.check_write_allowed("_Workspaces/ws1/config.md")

    def test_assets_blocked(self):
        with pytest.raises(ValueError, match="protected folder"):
            common.check_write_allowed("_Assets/image.png")

    def test_archive_blocked(self):
        with pytest.raises(ValueError, match="protected folder"):
            common.check_write_allowed("_Archive/old-doc.md")

    # -- Underscore-prefixed: allowed exceptions --

    def test_temporal_allowed(self):
        common.check_write_allowed("_Temporal/Research/2026-04/foo.md")

    def test_config_allowed(self):
        common.check_write_allowed("_Config/Skills/my-skill/SKILL.md")

    # -- Normal folders: allowed --

    def test_ideas_allowed(self):
        common.check_write_allowed("Ideas/my-idea.md")

    def test_wiki_allowed(self):
        common.check_write_allowed("Wiki/my-page.md")

    def test_daily_notes_allowed(self):
        common.check_write_allowed("Daily Notes/2026-04-06 Mon.md")

    # -- Edge cases --

    def test_bare_filename_allowed(self):
        common.check_write_allowed("README.md")

    def test_empty_path_raises(self):
        with pytest.raises(ValueError, match="Empty path"):
            common.check_write_allowed("")
