"""Tests for _common.py — shared utilities for brain-core scripts."""

import os

import pytest

import _common as common


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

    def test_empty_value_skipped(self):
        text = "---\ntype: living/wiki\nempty_field:\nstatus: active\n---\nBody"
        fields, body = common.parse_frontmatter(text)
        assert "empty_field" not in fields
        assert fields["type"] == "living/wiki"
        assert fields["status"] == "active"

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

    def test_archive_match(self, vault_with_files):
        idx = self._index(vault_with_files)
        r = common.resolve_broken_link("Old Design", idx)
        # "Old Design" doesn't match directly (it's "20260317-Old Design" in archive)
        # but archive matching looks for substring containment
        assert r.status == "resolved"
        assert "20260317-Old Design" in r.resolved_to
        assert r.strategy == "archive_match"

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
