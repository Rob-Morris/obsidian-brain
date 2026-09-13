"""Tests for migrate_to_0_67_0 — flatten temporal date folders."""

from __future__ import annotations

import os

import pytest

import json
import shutil
from pathlib import Path

import compile_router
import migrate_to_0_67_0 as migration
import upgrade
import _search.paths as search_paths
import _semantic.runtime as semantic_runtime


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


LOG = "---\ntype: temporal/log\ntags:\n  - log\ndate: 2026-03-01\n---\n\nBody.\n"


@pytest.fixture
def vault(tmp_path):
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("0.67.0\n")
    (bc / "session-core.md").write_text("# Session Core\n\n## Core Docs\n\n## Standards\n")
    _write(tmp_path / "_Config" / "router.md", "Always:\n- Keep a tidy vault.\n")
    _write(
        tmp_path / "_Config" / "Taxonomy" / "Temporal" / "logs.md",
        "# Logs\n\n## Naming\n\n`yyyymmdd-log.md` in `_Temporal/Logs/`, date source `date`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: temporal/log\ntags:\n  - log\ndate:\n---\n```\n",
    )
    _write(
        tmp_path / "_Config" / "Taxonomy" / "Living" / "projects.md",
        "# Projects\n\n## Naming\n\n`{Title}.md` in `Projects/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: living/project\ntags:\n  - project\nkey:\n---\n```\n",
    )
    _write(
        tmp_path / "Projects" / "Brain.md",
        "---\ntype: living/project\ntags:\n  - project/brain\nkey: brain\n---\n\n"
        "See [[_Temporal/Logs/2026-03/20260301-log]] and "
        "[[_Temporal/Logs/project~brain/2026-04/20260402-log|owned]].\n",
    )
    _write(tmp_path / "_Temporal" / "Logs" / "2026-03" / "20260301-log.md", LOG)
    _write(tmp_path / "_Temporal" / "Logs" / "project~brain" / "2026-04" / "20260402-log.md", LOG)
    _write(tmp_path / ".brain" / "local" / "retrieval-index.json", "{}")
    return tmp_path


class TestPlan:
    def test_moves_every_file_out_of_month_folders(self, vault):
        moves = migration._planned_moves(str(vault))
        assert moves == [
            {"source": "_Temporal/Logs/2026-03/20260301-log.md", "dest": "_Temporal/Logs/20260301-log.md"},
            {
                "source": "_Temporal/Logs/project~brain/2026-04/20260402-log.md",
                "dest": "_Temporal/Logs/project~brain/20260402-log.md",
            },
        ]

    def test_archive_segments_are_excluded(self, vault):
        _write(vault / "_Archive" / "_Temporal" / "Logs" / "2025-12" / "20251201-log.md", LOG)
        _write(vault / "_Temporal" / "Logs" / "_Archive" / "2026-01" / "20260101-log.md", LOG)
        sources = [move["source"] for move in migration._planned_moves(str(vault))]
        assert not any("_Archive" in source for source in sources)

    def test_hidden_directories_are_neither_planned_nor_reported(self, vault):
        hidden = vault / "_Temporal" / ".trash" / "2026-01" / "20260101-log.md"
        _write(hidden, LOG)
        sources = [move["source"] for move in migration._planned_moves(str(vault))]
        assert not any(".trash" in source for source in sources)
        migration.migrate(str(vault))
        assert migration._remaining_month_dirs(str(vault)) == []
        assert hidden.is_file()

    def test_symlinked_source_is_refused(self, vault, tmp_path_factory):
        outside = tmp_path_factory.mktemp("outside") / "elsewhere.md"
        outside.write_text(LOG)
        os.symlink(outside, vault / "_Temporal" / "Logs" / "2026-03" / "linked.md")
        with pytest.raises(ValueError, match="symlink"):
            migration._planned_moves(str(vault))

    def test_prospective_effects_declare_rewrite_surface_and_destinations(self, vault):
        effects = migration.prospective_effects(str(vault))
        assert os.path.join(str(vault), "Projects", "Brain.md") in effects
        assert os.path.join(str(vault), "_Temporal", "Logs", "20260301-log.md") in effects
        assert os.path.join(str(vault), search_paths.OUTPUT_PATH) not in effects

    def test_nothing_to_do_declares_nothing(self, tmp_path):
        (tmp_path / "_Temporal").mkdir()
        assert migration.prospective_effects(str(tmp_path)) == []
        assert migration.migrate(str(tmp_path))["status"] == "skipped"


class TestMigrate:
    def test_caches_are_invalidated_before_the_moves(self, vault, monkeypatch):
        order = []
        real_move = migration.move_and_update_links
        real_drop = migration.drop_path_keyed_retrieval_caches

        def spy_move(*args, **kwargs):
            order.append("move")
            return real_move(*args, **kwargs)

        def spy_drop(*args, **kwargs):
            order.append("drop")
            return real_drop(*args, **kwargs)

        monkeypatch.setattr(migration, "move_and_update_links", spy_move)
        monkeypatch.setattr(migration, "drop_path_keyed_retrieval_caches", spy_drop)
        migration.migrate(str(vault))
        assert order == ["drop", "move"]

    def test_flattens_rewrites_links_prunes_and_invalidates_caches(self, vault):
        (vault / semantic_runtime.EMBEDDINGS_META_REL).parent.mkdir(parents=True, exist_ok=True)
        (vault / semantic_runtime.EMBEDDINGS_META_REL).write_text("{}")

        result = migration.migrate(str(vault))

        assert result["status"] == "ok"
        assert result["links_updated"] == 2
        assert (vault / "_Temporal" / "Logs" / "20260301-log.md").is_file()
        assert (vault / "_Temporal" / "Logs" / "project~brain" / "20260402-log.md").is_file()
        assert not (vault / "_Temporal" / "Logs" / "2026-03").exists()
        assert not (vault / "_Temporal" / "Logs" / "project~brain" / "2026-04").exists()
        body = (vault / "Projects" / "Brain.md").read_text()
        assert "[[_Temporal/Logs/20260301-log]]" in body
        assert "[[_Temporal/Logs/project~brain/20260402-log|owned]]" in body
        assert not (vault / search_paths.OUTPUT_PATH).exists()
        assert not (vault / semantic_runtime.EMBEDDINGS_META_REL).exists()
        assert sorted(result["retrieval_caches_removed"]) == sorted(
            [search_paths.OUTPUT_PATH, semantic_runtime.EMBEDDINGS_META_REL]
        )
        assert "warnings" not in result
        assert migration._remaining_month_dirs(str(vault)) == []

    def test_non_markdown_files_move_without_disturbing_links(self, vault):
        _write(vault / "_Temporal" / "Logs" / "2026-03" / "sketch.png", "png")
        before = (vault / "Projects" / "Brain.md").read_text()
        result = migration.migrate(str(vault))
        assert (vault / "_Temporal" / "Logs" / "sketch.png").is_file()
        assert result["links_updated"] == 2
        after = (vault / "Projects" / "Brain.md").read_text()
        assert after == before.replace("2026-03/", "").replace("2026-04/", "")

    def test_archive_is_left_untouched(self, vault):
        archived = vault / "_Archive" / "_Temporal" / "Logs" / "2025-12" / "20251201-log.md"
        legacy = vault / "_Temporal" / "Logs" / "_Archive" / "2026-01" / "20260101-log.md"
        _write(archived, LOG)
        _write(legacy, LOG)
        migration.migrate(str(vault))
        assert archived.is_file()
        assert legacy.is_file()

    def test_junk_blocked_month_folder_is_reported_not_deleted(self, vault):
        junk = vault / "_Temporal" / "Logs" / "2026-02" / ".DS_Store"
        _write(junk, "\x00")
        result = migration.migrate(str(vault))
        assert result["status"] == "ok"
        assert result["warnings"] and "_Temporal/Logs/2026-02" in result["warnings"][0]
        assert "empty_folders" in result["warnings"][0]
        assert junk.is_file()

    def test_destination_collision_stops_before_any_write(self, vault):
        _write(vault / "_Temporal" / "Logs" / "20260301-log.md", LOG)
        before = (vault / "Projects" / "Brain.md").read_text()
        with pytest.raises(FileExistsError):
            migration.migrate(str(vault))
        assert (vault / "_Temporal" / "Logs" / "2026-03" / "20260301-log.md").is_file()
        assert (vault / "Projects" / "Brain.md").read_text() == before

    def test_second_run_is_a_noop(self, vault):
        migration.migrate(str(vault))
        assert migration.migrate(str(vault)) == {"status": "skipped", "moves": [], "links_updated": 0}

    def test_definition_files_are_untouched(self, vault):
        taxonomy = vault / "_Config" / "Taxonomy" / "Temporal" / "logs.md"
        taxonomy.write_text(taxonomy.read_text().replace("`_Temporal/Logs/`", "`_Temporal/Logs/yyyy-mm/`"))
        before = taxonomy.read_text()
        migration.migrate(str(vault))
        assert taxonomy.read_text() == before, "definition files belong to definition sync, not migrations"


class TestThroughTheUpgradeRunner:
    def test_runner_discovers_imports_and_records_the_migration(self, vault, monkeypatch):
        """Exercise discovery, the fresh import context, effect validation and the ledger."""
        real_scripts = Path(migration.__file__).resolve().parents[1]
        scripts = vault / ".brain-core" / "scripts"
        shutil.copytree(real_scripts, scripts, ignore=shutil.ignore_patterns("__pycache__"))
        declared = []
        results, ledger = upgrade._run_migrations(
            str(vault), "0.66.1", "0.67.0", raise_on_error=True,
            prepare_effects=lambda effects: declared.extend(effects),
        )
        assert [item["version"] for item in results] == ["0.67.0"]
        assert results[0]["status"] == "ok"
        assert results[0]["links_updated"] == 2
        assert ledger["migrations"]["0.67.0"]["status"] == "ok"
        assert (vault / "_Temporal" / "Logs" / "20260301-log.md").is_file()
        assert os.path.join(str(vault), "Projects", "Brain.md") in declared
        assert os.path.join(str(vault), "_Temporal", "Logs", "20260301-log.md") in declared
        # A second runner pass finds the ledger entry and does not replay.
        again, _ledger = upgrade._run_migrations(str(vault), "0.66.1", "0.67.0", raise_on_error=True)
        assert again == []
