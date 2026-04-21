"""Tests for _common._vault — vault discovery, scanning, and artefact matching."""

import os

import pytest

import _common as common
from _common import is_vault_root

from conftest import filesystem_is_case_sensitive


# ---------------------------------------------------------------------------
# is_vault_root
# ---------------------------------------------------------------------------

class TestIsVaultRoot:
    def test_vault_with_version(self, vault):
        assert is_vault_root(vault) is True

    @pytest.mark.parametrize("filename", ["AGENTS.md", "Agents.md"])
    def test_vault_with_agents_md_variants(self, tmp_path, filename):
        (tmp_path / filename).write_text("agent entry\n")
        assert is_vault_root(tmp_path) is True

    def test_non_vault(self, tmp_path):
        assert is_vault_root(tmp_path) is False

    def test_directory_named_agents_md_is_not_a_vault(self, tmp_path):
        (tmp_path / "AGENTS.md").mkdir()
        assert is_vault_root(tmp_path) is False


class TestFindRootBootstrapFile:
    @pytest.mark.parametrize(
        ("canonical_name", "filename"),
        [
            ("AGENTS.md", "AGENTS.md"),
            ("AGENTS.md", "Agents.md"),
            ("CLAUDE.md", "CLAUDE.md"),
        ],
    )
    def test_finds_enumerated_variants(self, tmp_path, canonical_name, filename):
        (tmp_path / filename).write_text("bootstrap\n")
        found = common.find_root_bootstrap_file(tmp_path, canonical_name)
        assert found is not None
        if filesystem_is_case_sensitive(tmp_path):
            assert found == tmp_path / filename
        else:
            assert found.name.lower() == filename.lower()
            assert found.name == canonical_name

    def test_prefers_canonical_name_when_both_exist(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("canonical\n")
        (tmp_path / "Agents.md").write_text("legacy\n")
        found = common.find_root_bootstrap_file(tmp_path, "AGENTS.md")
        assert found == tmp_path / "AGENTS.md"

    @pytest.mark.parametrize(
        ("canonical_name", "filename"),
        [
            ("AGENTS.md", "agents.md"),
            ("CLAUDE.md", "Claude.md"),
            ("CLAUDE.md", "claude.md"),
        ],
    )
    def test_rejects_non_enumerated_variants_on_case_sensitive_fs(self, tmp_path, canonical_name, filename):
        if not filesystem_is_case_sensitive(tmp_path):
            pytest.skip("case-insensitive filesystem accepts alternate casing automatically")
        (tmp_path / filename).write_text("bootstrap\n")
        assert common.find_root_bootstrap_file(tmp_path, canonical_name) is None

    def test_ignores_directory_named_like_bootstrap(self, tmp_path):
        (tmp_path / "AGENTS.md").mkdir()
        assert common.find_root_bootstrap_file(tmp_path, "AGENTS.md") is None


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
        (bc / "session-core.md").write_text("# Session Core\n")
        assert common.scan_temporal_types(tmp_path) == []


# ---------------------------------------------------------------------------
# TEMPORAL_DIR constant
# ---------------------------------------------------------------------------

def test_temporal_dir_constant():
    assert common.TEMPORAL_DIR == "_Temporal"


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
