"""Tests for rename.py — wikilink-aware file renaming."""

import os

import pytest

import rename


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def vault(tmp_path):
    """Create a minimal vault with linked files."""
    # .brain-core/VERSION (needed for find_vault_root)
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("0.7.0\n")
    (bc / "session-core.md").write_text("# Session Core\n")

    # Living type: Wiki
    wiki = tmp_path / "Wiki"
    wiki.mkdir()
    (wiki / "topic-a.md").write_text(
        "---\ntype: living/wiki\ntags: []\n---\n\n# Topic A\n\nSee also [[Wiki/topic-b]].\n"
    )
    (wiki / "topic-b.md").write_text(
        "---\ntype: living/wiki\ntags: []\n---\n\n# Topic B\n\nRelated to [[Wiki/topic-a|Topic A]].\n"
    )

    # Temporal type
    temporal = tmp_path / "_Temporal"
    temporal.mkdir()
    logs = temporal / "Logs" / "2026-03"
    logs.mkdir(parents=True)
    (logs / "20260324-log.md").write_text(
        "---\ntype: temporal/logs\ntags: []\n---\n\nWorked on [[Wiki/topic-a]] today.\n"
    )

    return tmp_path


# ---------------------------------------------------------------------------
# Core rename tests
# ---------------------------------------------------------------------------

class TestRenameAndUpdateLinks:
    def test_renames_file(self, vault):
        rename.rename_and_update_links(str(vault), "Wiki/topic-a.md", "Wiki/topic-a-renamed.md")
        assert not (vault / "Wiki" / "topic-a.md").exists()
        assert (vault / "Wiki" / "topic-a-renamed.md").exists()

    def test_updates_wikilinks_in_other_files(self, vault):
        rename.rename_and_update_links(str(vault), "Wiki/topic-a.md", "Wiki/topic-a-renamed.md")
        content_b = (vault / "Wiki" / "topic-b.md").read_text()
        assert "[[Wiki/topic-a-renamed|Topic A]]" in content_b

    def test_updates_wikilinks_in_temporal_files(self, vault):
        rename.rename_and_update_links(str(vault), "Wiki/topic-a.md", "Wiki/topic-a-renamed.md")
        content_log = (vault / "_Temporal" / "Logs" / "2026-03" / "20260324-log.md").read_text()
        assert "[[Wiki/topic-a-renamed]]" in content_log

    def test_returns_links_updated_count(self, vault):
        count = rename.rename_and_update_links(str(vault), "Wiki/topic-a.md", "Wiki/topic-a-renamed.md")
        # topic-b has one link, log has one link = 2
        assert count == 2

    def test_preserves_alias_in_wikilink(self, vault):
        rename.rename_and_update_links(str(vault), "Wiki/topic-a.md", "Wiki/new-name.md")
        content = (vault / "Wiki" / "topic-b.md").read_text()
        assert "[[Wiki/new-name|Topic A]]" in content

    def test_drops_alias_when_rewriting_wikilink_inside_table(self, vault):
        (vault / "Wiki" / "table.md").write_text(
            "| Name | Link |\n"
            "|---|---|\n"
            "| A | [[Wiki/topic-a|Topic A]] |\n"
            "| B | [[Wiki/topic-a|Topic B]] |\n"
        )

        rename.rename_and_update_links(str(vault), "Wiki/topic-a.md", "Wiki/new-name.md")

        content = (vault / "Wiki" / "table.md").read_text()
        assert "| A | [[Wiki/new-name]] |" in content
        assert "| B | [[Wiki/new-name]] |" in content
        assert "[[Wiki/new-name|Topic A]]" not in content
        assert "[[Wiki/new-name|Topic B]]" not in content

    def test_drops_alias_in_no_outer_pipe_table_row(self, vault):
        (vault / "Wiki" / "table.md").write_text(
            "Name | Link\n"
            "---|---\n"
            "A | [[Wiki/topic-a|Topic A]]\n"
        )

        rename.rename_and_update_links(str(vault), "Wiki/topic-a.md", "Wiki/new-name.md")

        content = (vault / "Wiki" / "table.md").read_text()
        assert "A | [[Wiki/new-name]]" in content
        assert "[[Wiki/new-name|Topic A]]" not in content

    def test_raises_on_missing_source(self, vault):
        with pytest.raises(FileNotFoundError, match="Source file not found"):
            rename.rename_and_update_links(str(vault), "Wiki/nonexistent.md", "Wiki/dest.md")

    def test_creates_destination_directory(self, vault):
        rename.rename_and_update_links(str(vault), "Wiki/topic-a.md", "Wiki/sub/topic-a.md")
        assert (vault / "Wiki" / "sub" / "topic-a.md").exists()

    def test_no_links_updated_when_no_references(self, vault):
        # topic-b is referenced only by topic-a with an alias
        # Remove all references first
        (vault / "Wiki" / "topic-a.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n# Topic A\n\nNo links here.\n"
        )
        (vault / "_Temporal" / "Logs" / "2026-03" / "20260324-log.md").write_text(
            "---\ntype: temporal/logs\ntags: []\n---\n\nNothing linked.\n"
        )
        count = rename.rename_and_update_links(str(vault), "Wiki/topic-b.md", "Wiki/topic-b-new.md")
        assert count == 0

    def test_does_not_update_links_in_system_dirs(self, vault):
        """System directories (other than _Temporal) should be skipped."""
        config = vault / "_Config"
        config.mkdir(exist_ok=True)
        (config / "notes.md").write_text("See [[Wiki/topic-a]].\n")

        rename.rename_and_update_links(str(vault), "Wiki/topic-a.md", "Wiki/renamed.md")
        # _Config file should NOT be updated
        assert "[[Wiki/topic-a]]" in (config / "notes.md").read_text()

    def test_single_rename_uses_move_set_engine(self, vault, monkeypatch):
        calls = []

        def fake_move_and_update_links(
            vault_root,
            moves,
            *,
            allow_archive_paths=False,
            prune_router=None,
        ):
            calls.append({
                "vault_root": vault_root,
                "moves": moves,
                "allow_archive_paths": allow_archive_paths,
                "prune_router": prune_router,
            })
            return {"moves": moves, "applied": moves, "links_updated": 7}

        monkeypatch.setattr(rename, "move_and_update_links", fake_move_and_update_links)

        prune_router = {"artefacts": [], "marker": "prune"}
        count = rename.rename_and_update_links(
            str(vault),
            "Wiki/topic-a.md",
            "Wiki/topic-a-renamed.md",
            router={"artefacts": []},
            allow_archive_paths=True,
            prune_router=prune_router,
        )

        assert count == 7
        assert calls == [{
            "vault_root": str(vault),
            "moves": [{"source": "Wiki/topic-a.md", "dest": "Wiki/topic-a-renamed.md"}],
            "allow_archive_paths": True,
            "prune_router": prune_router,
        }]

    def test_cli_reports_runtime_error_without_traceback(self, vault, monkeypatch, capsys):
        def fail_rename(*_args, **_kwargs):
            raise rename.PartialApplyError("move set partially applied")

        monkeypatch.setattr(rename, "load_fresh_compiled_router", lambda _vault_root: {"artefacts": []})
        monkeypatch.setattr(rename, "rename_and_update_links", fail_rename)
        monkeypatch.setattr(
            rename.sys,
            "argv",
            [
                "rename.py",
                "Wiki/topic-a.md",
                "Wiki/topic-a-renamed.md",
                "--vault",
                str(vault),
            ],
        )

        with pytest.raises(SystemExit) as exc_info:
            rename.main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Error: move set partially applied" in captured.err
        assert "Traceback" not in captured.err

    def test_cli_propagates_unrelated_runtime_error(self, vault, monkeypatch):
        def fail_rename(*_args, **_kwargs):
            raise RuntimeError("programmer bug")

        monkeypatch.setattr(rename, "load_fresh_compiled_router", lambda _vault_root: {"artefacts": []})
        monkeypatch.setattr(rename, "rename_and_update_links", fail_rename)
        monkeypatch.setattr(
            rename.sys,
            "argv",
            [
                "rename.py",
                "Wiki/topic-a.md",
                "Wiki/topic-a-renamed.md",
                "--vault",
                str(vault),
            ],
        )

        with pytest.raises(RuntimeError, match="programmer bug"):
            rename.main()

    def test_cli_refuses_stale_compiled_router_before_mutating(self, vault, monkeypatch, capsys):
        calls = []

        def fail_if_called(*_args, **_kwargs):
            calls.append(True)
            raise AssertionError("rename should not run with a stale router")

        monkeypatch.setattr(
            rename,
            "load_fresh_compiled_router",
            lambda _vault_root: {"error": "Compiled router cache is stale or unreadable (source-newer-than-router)."},
        )
        monkeypatch.setattr(rename, "rename_and_update_links", fail_if_called)
        monkeypatch.setattr(
            rename.sys,
            "argv",
            [
                "rename.py",
                "Wiki/topic-a.md",
                "Wiki/topic-a-renamed.md",
                "--vault",
                str(vault),
            ],
        )

        with pytest.raises(SystemExit) as exc_info:
            rename.main()

        assert exc_info.value.code == 1
        assert calls == []
        captured = capsys.readouterr()
        assert "Compiled router cache is stale or unreadable" in captured.err
        assert (vault / "Wiki" / "topic-a.md").exists()
        assert not (vault / "Wiki" / "topic-a-renamed.md").exists()


class TestMoveAndUpdateLinks:
    def test_batch_moves_rewrite_multiple_path_links(self, vault):
        (vault / "Wiki" / "index.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "[[Wiki/topic-a]] [[Wiki/topic-b|Topic B]]\n"
        )

        result = rename.move_and_update_links(
            str(vault),
            [
                {"source": "Wiki/topic-a.md", "dest": "Wiki/topic-a-renamed.md"},
                {"source": "Wiki/topic-b.md", "dest": "Wiki/topic-b-renamed.md"},
            ],
        )

        assert result["links_updated"] == 5
        assert (vault / "Wiki" / "topic-a-renamed.md").exists()
        assert (vault / "Wiki" / "topic-b-renamed.md").exists()
        content = (vault / "Wiki" / "index.md").read_text()
        assert "[[Wiki/topic-a-renamed]]" in content
        assert "[[Wiki/topic-b-renamed|Topic B]]" in content

    def test_batch_rewrites_links_in_one_vault_pass(self, vault, monkeypatch):
        calls = []
        real_replace = rename.replace_wikilinks_in_vault

        def counting_replace(vault_root, pattern, replacement, **kwargs):
            calls.append(pattern)
            return real_replace(vault_root, pattern, replacement, **kwargs)

        monkeypatch.setattr(rename, "replace_wikilinks_in_vault", counting_replace)

        rename.move_and_update_links(
            str(vault),
            [
                {"source": "Wiki/topic-a.md", "dest": "Wiki/topic-a-renamed.md"},
                {"source": "Wiki/topic-b.md", "dest": "Wiki/topic-b-renamed.md"},
            ],
        )

        assert len(calls) == 1

    def test_batch_builds_basename_counts_once(self, vault, monkeypatch):
        calls = []
        real_counts = rename.build_md_basename_counts

        def counting_counts(vault_root):
            calls.append(vault_root)
            return real_counts(vault_root)

        monkeypatch.setattr(rename, "build_md_basename_counts", counting_counts)

        rename.move_and_update_links(
            str(vault),
            [
                {"source": "Wiki/topic-a.md", "dest": "Wiki/topic-a-renamed.md"},
                {"source": "Wiki/topic-b.md", "dest": "Wiki/topic-b-renamed.md"},
            ],
        )

        assert calls == [str(vault)]

    def test_nested_moves_vacate_destinations_before_reuse(self, vault):
        (vault / "Wiki" / "topic-c.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n# Topic C\n"
        )

        result = rename.move_and_update_links(
            str(vault),
            [
                {"source": "Wiki/topic-a.md", "dest": "Wiki/topic-b.md"},
                {"source": "Wiki/topic-b.md", "dest": "Wiki/topic-c.md"},
                {"source": "Wiki/topic-c.md", "dest": "Wiki/topic-d.md"},
            ],
        )

        assert result["applied"] == [
            {"source": "Wiki/topic-c.md", "dest": "Wiki/topic-d.md"},
            {"source": "Wiki/topic-b.md", "dest": "Wiki/topic-c.md"},
            {"source": "Wiki/topic-a.md", "dest": "Wiki/topic-b.md"},
        ]
        assert (vault / "Wiki" / "topic-b.md").exists()
        assert (vault / "Wiki" / "topic-c.md").exists()
        assert (vault / "Wiki" / "topic-d.md").exists()

    def test_preflight_collision_happens_before_link_rewrite_or_moves(self, vault, monkeypatch):
        calls = []
        (vault / "Wiki" / "existing.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n# Existing\n"
        )

        def counting_replace(vault_root, pattern, replacement, **_kwargs):
            calls.append(pattern)
            return 0

        monkeypatch.setattr(rename, "replace_wikilinks_in_vault", counting_replace)

        with pytest.raises(FileExistsError, match="Destination file already exists"):
            rename.move_and_update_links(
                str(vault),
                [
                    {"source": "Wiki/topic-a.md", "dest": "Wiki/existing.md"},
                ],
            )

        assert calls == []
        assert (vault / "Wiki" / "topic-a.md").exists()

    def test_duplicate_destinations_rejected_before_any_move(self, vault):
        with pytest.raises(ValueError, match="Duplicate move destination"):
            rename.move_and_update_links(
                str(vault),
                [
                    {"source": "Wiki/topic-a.md", "dest": "Wiki/renamed.md"},
                    {"source": "Wiki/topic-b.md", "dest": "Wiki/renamed.md"},
                ],
            )
        assert (vault / "Wiki" / "topic-a.md").exists()
        assert (vault / "Wiki" / "topic-b.md").exists()

    def test_aliased_duplicate_destinations_rejected_before_rewrite_or_move(
        self, vault, monkeypatch
    ):
        calls = []
        (vault / "Wiki" / "a.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n# A\n"
        )
        (vault / "Wiki" / "b.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n# B\n"
        )
        (vault / "Wiki" / "links.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "[[Wiki/a]] [[Wiki/b]]\n"
        )

        def counting_replace(vault_root, pattern, replacement, **_kwargs):
            calls.append(pattern)
            return 0

        monkeypatch.setattr(rename, "replace_wikilinks_in_vault", counting_replace)

        with pytest.raises(ValueError, match="Duplicate move destination"):
            rename.move_and_update_links(
                str(vault),
                [
                    {"source": "Wiki/a.md", "dest": "Wiki/out.md"},
                    {"source": "Wiki/b.md", "dest": "Wiki/./out.md"},
                ],
            )

        assert calls == []
        assert (vault / "Wiki" / "a.md").exists()
        assert (vault / "Wiki" / "b.md").exists()
        assert not (vault / "Wiki" / "out.md").exists()
        assert "[[Wiki/a]] [[Wiki/b]]" in (vault / "Wiki" / "links.md").read_text()

    def test_aliased_duplicate_sources_rejected_before_rewrite_or_move(
        self, vault, monkeypatch
    ):
        calls = []

        def counting_replace(vault_root, pattern, replacement, **_kwargs):
            calls.append(pattern)
            return 0

        monkeypatch.setattr(rename, "replace_wikilinks_in_vault", counting_replace)

        with pytest.raises(ValueError, match="Duplicate move source"):
            rename.move_and_update_links(
                str(vault),
                [
                    {"source": "Wiki/topic-a.md", "dest": "Wiki/out-a.md"},
                    {"source": "Wiki/./topic-a.md", "dest": "Wiki/out-b.md"},
                ],
            )

        assert calls == []
        assert (vault / "Wiki" / "topic-a.md").exists()
        assert not (vault / "Wiki" / "out-a.md").exists()
        assert not (vault / "Wiki" / "out-b.md").exists()

    def test_aliased_vacated_destination_still_moves(self, vault):
        (vault / "Wiki" / "topic-c.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n# Topic C\n"
        )

        result = rename.move_and_update_links(
            str(vault),
            [
                {"source": "Wiki/topic-a.md", "dest": "Wiki/./topic-b.md"},
                {"source": "Wiki/topic-b.md", "dest": "Wiki/topic-c.md"},
                {"source": "Wiki/topic-c.md", "dest": "Wiki/topic-d.md"},
            ],
        )

        assert result["applied"] == [
            {"source": "Wiki/topic-c.md", "dest": "Wiki/topic-d.md"},
            {"source": "Wiki/topic-b.md", "dest": "Wiki/topic-c.md"},
            {"source": "Wiki/topic-a.md", "dest": "Wiki/topic-b.md"},
        ]
        assert (vault / "Wiki" / "topic-b.md").exists()
        assert (vault / "Wiki" / "topic-c.md").exists()
        assert (vault / "Wiki" / "topic-d.md").exists()

    def test_aliased_source_rewrites_canonical_wikilinks(self, vault):
        (vault / "Wiki" / "a.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n# A\n"
        )
        (vault / "Wiki" / "links.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "canonical [[Wiki/a]] short [[a]] alias [[Wiki/./a]]\n"
        )

        result = rename.move_and_update_links(
            str(vault),
            [{"source": "Wiki/./a.md", "dest": "Wiki/b.md"}],
        )

        assert result["moves"] == [{"source": "Wiki/a.md", "dest": "Wiki/b.md"}]
        assert result["applied"] == [{"source": "Wiki/a.md", "dest": "Wiki/b.md"}]
        content = (vault / "Wiki" / "links.md").read_text()
        assert "[[Wiki/b]]" in content
        assert "[[b]]" in content
        assert "[[Wiki/a]]" not in content
        assert "[[a]]" not in content
        assert "[[Wiki/./a]]" not in content
        assert not (vault / "Wiki" / "a.md").exists()
        assert (vault / "Wiki" / "b.md").is_file()

    def test_symlink_source_rejected_before_rewrite_or_move(self, vault, monkeypatch):
        calls = []
        target = vault / "Wiki" / "target.md"
        alias = vault / "Wiki" / "alias.md"
        target.write_text("---\ntype: living/wiki\ntags: []\n---\n\n# Target\n")
        alias.symlink_to(target)
        (vault / "Wiki" / "links.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "canonical [[Wiki/target]] short [[target]] alias [[Wiki/alias]]\n"
        )

        def counting_replace(vault_root, pattern, replacement, **_kwargs):
            calls.append(pattern)
            return 0

        monkeypatch.setattr(rename, "replace_wikilinks_in_vault", counting_replace)

        with pytest.raises(ValueError, match="Move source cannot be a symlink"):
            rename.move_and_update_links(
                str(vault),
                [{"source": "Wiki/alias.md", "dest": "Wiki/moved.md"}],
            )

        assert calls == []
        assert target.is_file()
        assert alias.is_symlink()
        assert not (vault / "Wiki" / "moved.md").exists()
        content = (vault / "Wiki" / "links.md").read_text()
        assert "[[Wiki/target]]" in content
        assert "[[target]]" in content
        assert "[[Wiki/alias]]" in content

    def test_symlink_destination_rejected_before_rewrite_or_move(self, vault, monkeypatch):
        calls = []
        source = vault / "Wiki" / "source.md"
        target = vault / "Wiki" / "target.md"
        dest = vault / "Wiki" / "dest.md"
        source.write_text("---\ntype: living/wiki\ntags: []\n---\n\n# Source\n")
        target.write_text("---\ntype: living/wiki\ntags: []\n---\n\n# Target\n")
        dest.symlink_to(target)
        (vault / "Wiki" / "links.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "source [[Wiki/source]] target [[Wiki/target]] dest [[Wiki/dest]]\n"
        )

        def counting_replace(vault_root, pattern, replacement, **_kwargs):
            calls.append(pattern)
            return 0

        monkeypatch.setattr(rename, "replace_wikilinks_in_vault", counting_replace)

        with pytest.raises(ValueError, match="Move destination cannot be a symlink"):
            rename.move_and_update_links(
                str(vault),
                [{"source": "Wiki/source.md", "dest": "Wiki/dest.md"}],
            )

        assert calls == []
        assert source.is_file()
        assert target.is_file()
        assert dest.is_symlink()
        content = (vault / "Wiki" / "links.md").read_text()
        assert "[[Wiki/source]]" in content
        assert "[[Wiki/target]]" in content
        assert "[[Wiki/dest]]" in content

    def test_cyclic_move_set_rejected_before_link_rewrite(self, vault, monkeypatch):
        calls = []

        def counting_replace(vault_root, pattern, replacement, **_kwargs):
            calls.append(pattern)
            return 0

        monkeypatch.setattr(rename, "replace_wikilinks_in_vault", counting_replace)

        with pytest.raises(ValueError, match="Cyclic move set"):
            rename.move_and_update_links(
                str(vault),
                [
                    {"source": "Wiki/topic-a.md", "dest": "Wiki/topic-b.md"},
                    {"source": "Wiki/topic-b.md", "dest": "Wiki/topic-a.md"},
                ],
            )

        assert calls == []
        assert (vault / "Wiki" / "topic-a.md").exists()
        assert (vault / "Wiki" / "topic-b.md").exists()

    def test_exported_preflight_rejects_cyclic_move_set(self, vault):
        with pytest.raises(ValueError, match="Cyclic move set"):
            rename.preflight_move_set(
                str(vault),
                [
                    {"source": "Wiki/topic-a.md", "dest": "Wiki/topic-b.md"},
                    {"source": "Wiki/topic-b.md", "dest": "Wiki/topic-a.md"},
                ],
            )

        assert (vault / "Wiki" / "topic-a.md").exists()
        assert (vault / "Wiki" / "topic-b.md").exists()

    def test_mid_apply_failure_reports_partial_commit_after_link_rewrite(self, vault, monkeypatch):
        (vault / "Wiki" / "links.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "[[Wiki/topic-a]] [[Wiki/topic-b]]\n"
        )
        real_rename = os.rename
        calls = []

        def failing_rename(source, dest):
            calls.append((source, dest))
            if len(calls) == 2:
                raise OSError("simulated rename failure")
            return real_rename(source, dest)

        monkeypatch.setattr(rename.os, "rename", failing_rename)

        with pytest.raises(rename.PartialApplyError) as excinfo:
            rename.move_and_update_links(
                str(vault),
                [
                    {"source": "Wiki/topic-a.md", "dest": "Wiki/topic-a-renamed.md"},
                    {"source": "Wiki/topic-b.md", "dest": "Wiki/topic-b-renamed.md"},
                ],
            )

        message = str(excinfo.value)
        assert "move set partially applied" in message
        assert "links already rewritten" in message
        assert "committed [{'source': 'Wiki/topic-a.md', 'dest': 'Wiki/topic-a-renamed.md'}]" in message
        assert "failed at Wiki/topic-b.md->Wiki/topic-b-renamed.md: simulated rename failure" in message
        assert (vault / "Wiki" / "topic-a-renamed.md").exists()
        assert (vault / "Wiki" / "topic-b.md").exists()
        content = (vault / "Wiki" / "links.md").read_text()
        assert "[[Wiki/topic-a-renamed]]" in content
        assert "[[Wiki/topic-b-renamed]]" in content

    def test_destination_parent_file_rejected_before_link_rewrite(self, vault):
        (vault / "Wiki" / "links.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "[[Wiki/topic-a]]\n"
        )
        (vault / "Wiki" / "blocked").write_text("not a directory\n")

        with pytest.raises(NotADirectoryError) as excinfo:
            rename.move_and_update_links(
                str(vault),
                [
                    {"source": "Wiki/topic-a.md", "dest": "Wiki/blocked/topic-a.md"},
                ],
            )

        message = str(excinfo.value)
        assert "Destination parent is not a directory: Wiki/blocked" in message
        assert (vault / "Wiki" / "topic-a.md").exists()
        assert not (vault / "Wiki" / "blocked" / "topic-a.md").exists()
        content = (vault / "Wiki" / "links.md").read_text()
        assert "[[Wiki/topic-a]]" in content
        assert "[[Wiki/blocked/topic-a]]" not in content

    def test_broken_symlink_destination_parent_rejected_before_link_rewrite(self, vault):
        (vault / "Wiki" / "links.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "[[Wiki/topic-a]]\n"
        )
        blocked = vault / "Wiki" / "blocked"
        blocked.symlink_to(vault / "Wiki" / "missing-target")

        with pytest.raises(NotADirectoryError) as excinfo:
            rename.move_and_update_links(
                str(vault),
                [
                    {"source": "Wiki/topic-a.md", "dest": "Wiki/blocked/topic-a.md"},
                ],
            )

        message = str(excinfo.value)
        assert "Destination parent is not a directory: Wiki/blocked" in message
        assert (vault / "Wiki" / "topic-a.md").exists()
        assert blocked.is_symlink()
        assert not (vault / "Wiki" / "missing-target" / "topic-a.md").exists()
        content = (vault / "Wiki" / "links.md").read_text()
        assert "[[Wiki/topic-a]]" in content
        assert "[[Wiki/blocked/topic-a]]" not in content
        assert "[[Wiki/missing-target/topic-a]]" not in content

    def test_conflicting_wikilink_stem_replacements_raise_value_error(self):
        def item_stems(item, _counts):
            return ["Wiki/shared"], {"Wiki/shared": item}

        with pytest.raises(ValueError) as excinfo:
            rename._accumulate_wikilink_stems(
                ["Wiki/first", "Wiki/second"],
                {},
                item_stems,
            )

        message = str(excinfo.value)
        assert "Conflicting wikilink replacement" in message
        assert "Wiki/shared" in message
        assert "Wiki/first" in message
        assert "Wiki/second" in message

    def test_unreadable_unrelated_note_does_not_abort_interactive_rename(self, vault, monkeypatch):
        links = vault / "Wiki" / "links.md"
        links.write_text("---\ntype: living/wiki\ntags: []\n---\n\n[[Wiki/topic-a]]\n")
        real_open = open

        def flaky_open(path, *args, **kwargs):
            if os.fspath(path) == os.fspath(links) and args and "r" in args[0]:
                raise OSError("cloud placeholder")
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr("builtins.open", flaky_open)

        result = rename.move_and_update_links(
            str(vault),
            [{"source": "Wiki/topic-a.md", "dest": "Wiki/topic-a-renamed.md"}],
        )

        assert result["applied"] == [
            {"source": "Wiki/topic-a.md", "dest": "Wiki/topic-a-renamed.md"}
        ]
        assert not (vault / "Wiki" / "topic-a.md").exists()
        assert (vault / "Wiki" / "topic-a-renamed.md").is_file()

    def test_noop_entries_are_reported_but_not_rewritten_or_moved(self, vault, monkeypatch):
        calls = []

        def counting_replace(vault_root, pattern, replacement, **_kwargs):
            calls.append(pattern)
            return 0

        monkeypatch.setattr(rename, "replace_wikilinks_in_vault", counting_replace)

        result = rename.move_and_update_links(
            str(vault),
            [{"source": "Wiki/topic-a.md", "dest": "Wiki/topic-a.md"}],
        )

        assert result["moves"] == [{"source": "Wiki/topic-a.md", "dest": "Wiki/topic-a.md"}]
        assert result["applied"] == []
        assert result["links_updated"] == 0
        assert calls == []
        assert (vault / "Wiki" / "topic-a.md").exists()

    def test_filename_only_links_are_not_rewritten_for_folder_only_move(self, tmp_path):
        bc = tmp_path / ".brain-core"
        bc.mkdir()
        (bc / "VERSION").write_text("0.7.0\n")
        (bc / "session-core.md").write_text("# Session Core\n")
        source_dir = tmp_path / "Wiki" / "old-hub"
        source_dir.mkdir(parents=True)
        (source_dir / "topic.md").write_text("---\ntype: living/wiki\n---\n\n# Topic\n")
        (tmp_path / "Wiki" / "links.md").write_text(
            "---\ntype: living/wiki\n---\n\n"
            "Short [[topic]]. Path [[Wiki/old-hub/topic]].\n"
        )

        result = rename.move_and_update_links(
            str(tmp_path),
            [{"source": "Wiki/old-hub/topic.md", "dest": "Wiki/new-hub/topic.md"}],
        )

        assert result["links_updated"] == 1
        content = (tmp_path / "Wiki" / "links.md").read_text()
        assert "Short [[topic]]." in content
        assert "Path [[Wiki/new-hub/topic]]." in content

    def test_nested_folder_batch_moves(self, tmp_path):
        bc = tmp_path / ".brain-core"
        bc.mkdir()
        (bc / "VERSION").write_text("0.7.0\n")
        (bc / "session-core.md").write_text("# Session Core\n")
        old = tmp_path / "Wiki" / "old-hub"
        (old / "child").mkdir(parents=True)
        (old / "Parent.md").write_text("---\ntype: living/wiki\n---\n\n# Parent\n")
        (old / "child" / "Child.md").write_text("---\ntype: living/wiki\n---\n\n# Child\n")
        (tmp_path / "Wiki" / "links.md").write_text(
            "---\ntype: living/wiki\n---\n\n"
            "[[Wiki/old-hub/Parent]] [[Wiki/old-hub/child/Child]]\n"
        )

        result = rename.move_and_update_links(
            str(tmp_path),
            [
                {"source": "Wiki/old-hub/Parent.md", "dest": "Wiki/new-hub/Parent.md"},
                {"source": "Wiki/old-hub/child/Child.md", "dest": "Wiki/new-hub/child/Child.md"},
            ],
        )

        assert result["links_updated"] == 2
        assert (tmp_path / "Wiki" / "new-hub" / "Parent.md").exists()
        assert (tmp_path / "Wiki" / "new-hub" / "child" / "Child.md").exists()
        content = (tmp_path / "Wiki" / "links.md").read_text()
        assert "[[Wiki/new-hub/Parent]]" in content
        assert "[[Wiki/new-hub/child/Child]]" in content

    def test_source_read_failure_falls_back_during_naming_validation(self, vault, monkeypatch):
        def failing_open(*args, **kwargs):
            raise OSError("cannot read")

        monkeypatch.setattr("builtins.open", failing_open)

        rename.validate_rename_request(
            str(vault),
            "Wiki/topic-a.md",
            "Wiki/Readable Title.md",
            router={
                "artefacts": [{
                    "key": "wiki",
                    "path": "Wiki",
                    "naming": {"pattern": "{Title}.md"},
                }]
            },
        )


# ---------------------------------------------------------------------------
# Delete and clean links tests
# ---------------------------------------------------------------------------

class TestDeleteAndCleanLinks:
    def test_delete_removes_file(self, vault):
        rename.delete_and_clean_links(str(vault), "Wiki/topic-a.md")
        assert not (vault / "Wiki" / "topic-a.md").exists()

    def test_delete_replaces_wikilinks_with_strikethrough(self, vault):
        rename.delete_and_clean_links(str(vault), "Wiki/topic-a.md")
        # topic-b has [[Wiki/topic-a|Topic A]] → should become ~~Topic A~~
        content_b = (vault / "Wiki" / "topic-b.md").read_text()
        assert "~~Topic A~~" in content_b
        assert "[[Wiki/topic-a" not in content_b

    def test_delete_replaces_plain_wikilinks(self, vault):
        rename.delete_and_clean_links(str(vault), "Wiki/topic-a.md")
        # log has [[Wiki/topic-a]] (no alias) → should become ~~topic-a~~
        content_log = (vault / "_Temporal" / "Logs" / "2026-03" / "20260324-log.md").read_text()
        assert "~~topic-a~~" in content_log
        assert "[[Wiki/topic-a]]" not in content_log

    def test_delete_returns_links_replaced_count(self, vault):
        count = rename.delete_and_clean_links(str(vault), "Wiki/topic-a.md")
        # topic-b has one link, log has one link = 2
        assert count == 2

    def test_delete_raises_on_missing_file(self, vault):
        with pytest.raises(FileNotFoundError, match="File not found"):
            rename.delete_and_clean_links(str(vault), "Wiki/nonexistent.md")

    def test_delete_no_references(self, vault):
        # Remove all references to topic-b first
        (vault / "Wiki" / "topic-a.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n# Topic A\n\nNo links.\n"
        )
        (vault / "_Temporal" / "Logs" / "2026-03" / "20260324-log.md").write_text(
            "---\ntype: temporal/logs\ntags: []\n---\n\nNothing linked.\n"
        )
        count = rename.delete_and_clean_links(str(vault), "Wiki/topic-b.md")
        assert count == 0
        assert not (vault / "Wiki" / "topic-b.md").exists()


# ---------------------------------------------------------------------------
# Filename-only wikilinks
# ---------------------------------------------------------------------------

@pytest.fixture
def vault_with_filename_links(tmp_path):
    """Vault where wikilinks use filename-only format (Obsidian default)."""
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("0.7.0\n")
    (bc / "session-core.md").write_text("# Session Core\n")

    wiki = tmp_path / "Wiki"
    wiki.mkdir()
    (wiki / "topic-a.md").write_text(
        "---\ntype: living/wiki\ntags: []\n---\n\n# Topic A\n"
    )
    (wiki / "topic-b.md").write_text(
        "---\ntype: living/wiki\ntags: []\n---\n\n# Topic B\n\n"
        "Related to [[topic-a|Topic A]] and [[topic-a]].\n"
    )

    temporal = tmp_path / "_Temporal" / "Logs" / "2026-03"
    temporal.mkdir(parents=True)
    (temporal / "20260324-log.md").write_text(
        "---\ntype: temporal/logs\ntags: []\n---\n\nWorked on [[topic-a]] today.\n"
    )

    return tmp_path


class TestFilenameOnlyRename:
    def test_updates_filename_only_wikilinks(self, vault_with_filename_links):
        vault = vault_with_filename_links
        rename.rename_and_update_links(str(vault), "Wiki/topic-a.md", "Wiki/new-name.md")
        content = (vault / "Wiki" / "topic-b.md").read_text()
        assert "[[new-name]]" in content

    def test_preserves_filename_only_format(self, vault_with_filename_links):
        vault = vault_with_filename_links
        rename.rename_and_update_links(str(vault), "Wiki/topic-a.md", "Wiki/new-name.md")
        content = (vault / "Wiki" / "topic-b.md").read_text()
        # Should NOT upgrade to full-path format
        assert "[[Wiki/new-name]]" not in content
        assert "[[new-name|Topic A]]" in content
        assert "[[new-name]]" in content

    def test_updates_both_full_path_and_filename_links(self, vault_with_filename_links):
        vault = vault_with_filename_links
        # Add a full-path link alongside the existing filename-only links
        (vault / "_Temporal" / "Logs" / "2026-03" / "20260324-log.md").write_text(
            "---\ntype: temporal/logs\ntags: []\n---\n\n"
            "Full: [[Wiki/topic-a]]. Short: [[topic-a]].\n"
        )
        rename.rename_and_update_links(str(vault), "Wiki/topic-a.md", "Wiki/new-name.md")
        content = (vault / "_Temporal" / "Logs" / "2026-03" / "20260324-log.md").read_text()
        assert "[[Wiki/new-name]]" in content
        assert "[[new-name]]" in content

    def test_returns_correct_count_with_filename_links(self, vault_with_filename_links):
        vault = vault_with_filename_links
        count = rename.rename_and_update_links(
            str(vault), "Wiki/topic-a.md", "Wiki/new-name.md"
        )
        # topic-b has 2 links ([[topic-a|Topic A]] and [[topic-a]]), log has 1
        assert count == 3

    def test_skips_filename_match_when_ambiguous(self, vault_with_filename_links):
        vault = vault_with_filename_links
        # Create a second file with the same basename in a different folder
        other = vault / "Notes"
        other.mkdir()
        (other / "topic-a.md").write_text("---\ntype: living/note\n---\n\n# Other\n")
        count = rename.rename_and_update_links(
            str(vault), "Wiki/topic-a.md", "Wiki/new-name.md"
        )
        # Ambiguous: only full-path links matched; filename-only links are skipped
        assert count == 0
        content = (vault / "Wiki" / "topic-b.md").read_text()
        assert "[[topic-a|Topic A]]" in content  # unchanged
        assert "[[topic-a]]" in content  # unchanged


class TestFilenameOnlyDelete:
    def test_cleans_filename_only_wikilinks(self, vault_with_filename_links):
        vault = vault_with_filename_links
        rename.delete_and_clean_links(str(vault), "Wiki/topic-a.md")
        content = (vault / "Wiki" / "topic-b.md").read_text()
        assert "~~topic-a~~" in content
        assert "[[topic-a]]" not in content

    def test_cleans_filename_only_with_alias(self, vault_with_filename_links):
        vault = vault_with_filename_links
        rename.delete_and_clean_links(str(vault), "Wiki/topic-a.md")
        content = (vault / "Wiki" / "topic-b.md").read_text()
        assert "~~Topic A~~" in content
        assert "[[topic-a|Topic A]]" not in content


# ---------------------------------------------------------------------------
# Heading anchors, block refs, and embeds
# ---------------------------------------------------------------------------

@pytest.fixture
def vault_with_anchors(tmp_path):
    """Vault with heading anchors, block refs, and embeds."""
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("0.7.0\n")
    (bc / "session-core.md").write_text("# Session Core\n")

    wiki = tmp_path / "Wiki"
    wiki.mkdir()
    (wiki / "topic-a.md").write_text(
        "---\ntype: living/wiki\ntags: []\n---\n\n# Topic A\n\n"
        "Some content. ^block123\n"
    )
    (wiki / "topic-b.md").write_text(
        "---\ntype: living/wiki\ntags: []\n---\n\n# Topic B\n\n"
        "Anchor: [[Wiki/topic-a#Overview]].\n"
        "Anchor alias: [[Wiki/topic-a#Overview|see overview]].\n"
        "Block ref: [[Wiki/topic-a#^block123]].\n"
        "Embed: ![[Wiki/topic-a]].\n"
        "Embed anchor: ![[Wiki/topic-a#Overview]].\n"
        "Filename anchor: [[topic-a#Details]].\n"
        "Plain: [[Wiki/topic-a]].\n"
    )

    return tmp_path


class TestAnchorAndEmbedRename:
    def test_preserves_heading_anchor(self, vault_with_anchors):
        vault = vault_with_anchors
        rename.rename_and_update_links(str(vault), "Wiki/topic-a.md", "Wiki/new-name.md")
        content = (vault / "Wiki" / "topic-b.md").read_text()
        assert "[[Wiki/new-name#Overview]]" in content

    def test_preserves_anchor_with_alias(self, vault_with_anchors):
        vault = vault_with_anchors
        rename.rename_and_update_links(str(vault), "Wiki/topic-a.md", "Wiki/new-name.md")
        content = (vault / "Wiki" / "topic-b.md").read_text()
        assert "[[Wiki/new-name#Overview|see overview]]" in content

    def test_preserves_block_ref(self, vault_with_anchors):
        vault = vault_with_anchors
        rename.rename_and_update_links(str(vault), "Wiki/topic-a.md", "Wiki/new-name.md")
        content = (vault / "Wiki" / "topic-b.md").read_text()
        assert "[[Wiki/new-name#^block123]]" in content

    def test_preserves_embed_prefix(self, vault_with_anchors):
        vault = vault_with_anchors
        rename.rename_and_update_links(str(vault), "Wiki/topic-a.md", "Wiki/new-name.md")
        content = (vault / "Wiki" / "topic-b.md").read_text()
        assert "![[Wiki/new-name]]" in content

    def test_preserves_embed_with_anchor(self, vault_with_anchors):
        vault = vault_with_anchors
        rename.rename_and_update_links(str(vault), "Wiki/topic-a.md", "Wiki/new-name.md")
        content = (vault / "Wiki" / "topic-b.md").read_text()
        assert "![[Wiki/new-name#Overview]]" in content

    def test_filename_only_with_anchor(self, vault_with_anchors):
        vault = vault_with_anchors
        rename.rename_and_update_links(str(vault), "Wiki/topic-a.md", "Wiki/new-name.md")
        content = (vault / "Wiki" / "topic-b.md").read_text()
        assert "[[new-name#Details]]" in content

    def test_plain_link_still_works(self, vault_with_anchors):
        vault = vault_with_anchors
        rename.rename_and_update_links(str(vault), "Wiki/topic-a.md", "Wiki/new-name.md")
        content = (vault / "Wiki" / "topic-b.md").read_text()
        assert "[[Wiki/new-name]]" in content

    def test_returns_correct_count(self, vault_with_anchors):
        vault = vault_with_anchors
        count = rename.rename_and_update_links(
            str(vault), "Wiki/topic-a.md", "Wiki/new-name.md"
        )
        # 7 links in topic-b: anchor, anchor+alias, blockref, embed, embed+anchor, filename+anchor, plain
        assert count == 7


class TestAnchorAndEmbedDelete:
    def test_delete_anchor_link(self, vault_with_anchors):
        vault = vault_with_anchors
        rename.delete_and_clean_links(str(vault), "Wiki/topic-a.md")
        content = (vault / "Wiki" / "topic-b.md").read_text()
        assert "~~topic-a~~" in content
        assert "[[Wiki/topic-a#Overview]]" not in content

    def test_delete_anchor_alias_uses_alias(self, vault_with_anchors):
        vault = vault_with_anchors
        rename.delete_and_clean_links(str(vault), "Wiki/topic-a.md")
        content = (vault / "Wiki" / "topic-b.md").read_text()
        assert "~~see overview~~" in content

    def test_delete_embed(self, vault_with_anchors):
        vault = vault_with_anchors
        rename.delete_and_clean_links(str(vault), "Wiki/topic-a.md")
        content = (vault / "Wiki" / "topic-b.md").read_text()
        assert "![[Wiki/topic-a]]" not in content


# ---------------------------------------------------------------------------
# Path boundary checks
# ---------------------------------------------------------------------------

class TestPathBoundary:
    """Ensure path traversal attacks are rejected."""

    def test_rename_source_traversal(self, vault):
        with pytest.raises(ValueError, match="outside allowed boundary"):
            rename.rename_and_update_links(
                str(vault), "../../etc/passwd", "Wiki/stolen.md",
            )

    def test_rename_dest_traversal(self, vault):
        with pytest.raises(ValueError, match="outside allowed boundary"):
            rename.rename_and_update_links(
                str(vault), "Wiki/topic-a.md", "../../tmp/exfil.md",
            )

    def test_delete_traversal(self, vault):
        with pytest.raises(ValueError, match="outside allowed boundary"):
            rename.delete_and_clean_links(str(vault), "../../etc/hosts")


class TestRenameRegionAwareness:
    """Rename honours literal-region skip ranges and the FM-property contract."""

    def test_literal_wikilink_in_inline_code_preserved(self, vault):
        (vault / "Wiki" / "docs.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "Use `[[Wiki/topic-a]]` as an example.\n"
            "Real link: [[Wiki/topic-a]].\n"
        )
        rename.rename_and_update_links(
            str(vault), "Wiki/topic-a.md", "Wiki/topic-a-renamed.md"
        )
        content = (vault / "Wiki" / "docs.md").read_text()
        assert "`[[Wiki/topic-a]]`" in content
        assert "Real link: [[Wiki/topic-a-renamed]]." in content

    def test_literal_wikilink_in_fence_preserved(self, vault):
        (vault / "Wiki" / "docs.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "```\n[[Wiki/topic-a]]\n```\n"
            "[[Wiki/topic-a]]\n"
        )
        rename.rename_and_update_links(
            str(vault), "Wiki/topic-a.md", "Wiki/topic-a-renamed.md"
        )
        content = (vault / "Wiki" / "docs.md").read_text()
        assert "```\n[[Wiki/topic-a]]\n```" in content
        assert "\n[[Wiki/topic-a-renamed]]\n" in content

    def test_frontmatter_property_is_rewritten(self, vault):
        """YAML property wikilinks are real links (D10) and must be renamed."""
        (vault / "Wiki" / "has-parent.md").write_text(
            "---\ntype: living/wiki\ntags: []\nparent: \"[[Wiki/topic-a]]\"\n---\n\n"
            "body\n"
        )
        rename.rename_and_update_links(
            str(vault), "Wiki/topic-a.md", "Wiki/topic-a-renamed.md"
        )
        content = (vault / "Wiki" / "has-parent.md").read_text()
        assert 'parent: "[[Wiki/topic-a-renamed]]"' in content


class TestBrainCoreProtection:
    """Ensure .brain-core/ files cannot be modified via rename/delete."""

    def test_rename_dest_into_brain_core(self, vault):
        with pytest.raises(ValueError, match="Cannot modify files inside .brain-core/"):
            rename.rename_and_update_links(
                str(vault), "Wiki/topic-a.md", ".brain-core/hijack.md",
            )

    def test_rename_source_out_of_brain_core_allowed(self, vault):
        """Moving a file OUT of .brain-core is allowed (source not checked)."""
        bc_file = vault / ".brain-core" / "movable.md"
        bc_file.write_text("---\ntype: living/wiki\n---\n\ntemp\n")
        # Should not raise — only dest is checked
        rename.rename_and_update_links(
            str(vault), ".brain-core/movable.md", "Wiki/rescued.md",
        )
        assert (vault / "Wiki" / "rescued.md").exists()
        assert not bc_file.exists()

    def test_delete_inside_brain_core(self, vault):
        with pytest.raises(ValueError, match="Cannot modify files inside .brain-core/"):
            rename.delete_and_clean_links(str(vault), ".brain-core/VERSION")

    def test_rename_dest_into_brain_core_subdir(self, vault):
        with pytest.raises(ValueError, match="Cannot modify files inside .brain-core/"):
            rename.rename_and_update_links(
                str(vault), "Wiki/topic-a.md", ".brain-core/scripts/evil.py",
            )
