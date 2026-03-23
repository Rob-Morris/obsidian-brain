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
