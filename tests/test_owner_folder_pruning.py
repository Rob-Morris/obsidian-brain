"""Tests for rmdir-only owner-folder pruning inside the move engine.

Covers ``prune_vacated_owner_folders`` (bounded post-order pass plus upward
walk) and the ``prune_router`` opt-in on ``rename.move_and_update_links``.
"""

import os
import pathlib

import pytest

import rename
from _common import artefact_territory_roots, prune_vacated_owner_folders
from _common._artefacts import owner_folder_stop_dirs


ROUTER = {
    "artefacts": [
        {"path": "Ideas", "key": "ideas", "classification": "living", "type": "living/ideas", "configured": True},
    ]
}


@pytest.fixture
def vault(tmp_path):
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("0.66.0\n")
    (tmp_path / "Ideas").mkdir()
    (tmp_path / "_Archive").mkdir()
    (tmp_path / "_Assets" / "Attachments").mkdir(parents=True)
    return tmp_path


def _write(path, text="---\ntype: living/ideas\ntags: []\n---\n\nBody.\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class TestPruneVacatedOwnerFolders:
    def test_stop_dirs_cover_root_type_roots_and_archive(self, vault):
        stops = owner_folder_stop_dirs(str(vault), ROUTER)
        assert os.path.abspath(str(vault)) in stops
        assert os.path.abspath(str(vault / "Ideas")) in stops
        assert os.path.abspath(str(vault / "_Archive")) in stops

    def test_removes_nested_empty_subtree_then_ascends(self, vault):
        (vault / "Ideas" / "a" / "b" / "c").mkdir(parents=True)
        # The file has already been moved away; only the vacated dir remains.
        prune_vacated_owner_folders(str(vault), ["Ideas/a/x.md"], ROUTER)
        assert not (vault / "Ideas" / "a").exists()
        assert (vault / "Ideas").is_dir()

    def test_ascends_through_empty_ancestors_until_content(self, vault):
        (vault / "Ideas" / "owner" / "child" / "grand").mkdir(parents=True)
        _write(vault / "Ideas" / "owner" / "Sibling.md")
        prune_vacated_owner_folders(str(vault), ["Ideas/owner/child/grand/x.md"], ROUTER)
        assert not (vault / "Ideas" / "owner" / "child").exists()
        assert (vault / "Ideas" / "owner" / "Sibling.md").is_file()

    def test_subtree_pass_skipped_when_vacated_dir_is_type_root(self, vault):
        (vault / "Ideas" / "empty" / "deeper").mkdir(parents=True)
        prune_vacated_owner_folders(str(vault), ["Ideas/x.md"], ROUTER)
        assert (vault / "Ideas" / "empty" / "deeper").is_dir()
        assert (vault / "Ideas").is_dir()

    def test_junk_file_blocks_branch_silently(self, vault):
        (vault / "Ideas" / "a" / "b").mkdir(parents=True)
        (vault / "Ideas" / "a" / ".DS_Store").write_bytes(b"\x00")
        prune_vacated_owner_folders(str(vault), ["Ideas/a/x.md"], ROUTER)
        assert not (vault / "Ideas" / "a" / "b").exists()
        assert (vault / "Ideas" / "a" / ".DS_Store").is_file()

    def test_real_file_in_nested_dir_keeps_the_chain(self, vault):
        (vault / "Ideas" / "a" / "b").mkdir(parents=True)
        _write(vault / "Ideas" / "a" / "b" / "Keep.md")
        prune_vacated_owner_folders(str(vault), ["Ideas/a/x.md"], ROUTER)
        assert (vault / "Ideas" / "a" / "b" / "Keep.md").is_file()

    def test_never_removes_archive_root_or_vault_root(self, vault):
        (vault / "_Archive" / "Ideas" / "owner").mkdir(parents=True)
        prune_vacated_owner_folders(str(vault), ["_Archive/Ideas/owner/x.md"], ROUTER)
        assert not (vault / "_Archive" / "Ideas").exists()
        assert (vault / "_Archive").is_dir()
        prune_vacated_owner_folders(str(vault), ["stray.md"], ROUTER)
        assert vault.is_dir()

    def test_symlinked_vacated_dir_is_not_descended(self, vault, tmp_path_factory):
        outside = tmp_path_factory.mktemp("outside")
        (outside / "inner").mkdir()
        os.symlink(outside, vault / "Ideas" / "linked")
        prune_vacated_owner_folders(str(vault), ["Ideas/linked/x.md"], ROUTER)
        assert (outside / "inner").is_dir()
        assert os.path.islink(vault / "Ideas" / "linked")

    def test_sources_outside_artefact_territory_are_ignored(self, vault):
        (vault / "_Config" / "Memories").mkdir(parents=True)
        (vault / "_Assets" / "Attachments" / "ideas~x").mkdir(parents=True)
        prune_vacated_owner_folders(
            str(vault),
            ["_Config/Memories/note.md", "_Assets/Attachments/ideas~x/pic.png"],
            ROUTER,
        )
        assert (vault / "_Config" / "Memories").is_dir()
        assert (vault / "_Assets" / "Attachments" / "ideas~x").is_dir()
        assert artefact_territory_roots(str(vault), ROUTER) == {
            os.path.abspath(str(vault / "Ideas")),
            os.path.abspath(str(vault / "_Archive")),
        }

    def test_archive_mirror_root_is_not_descended(self, vault):
        (vault / "_Archive" / "Ideas" / "stale").mkdir(parents=True)
        prune_vacated_owner_folders(str(vault), ["_Archive/Ideas/x.md"], ROUTER)
        assert (vault / "_Archive" / "Ideas" / "stale").is_dir()

    def test_sibling_prefixed_directory_is_not_treated_as_inside_vault(self, vault, tmp_path_factory):
        sibling = pathlib.Path(str(vault) + "-sibling")
        sibling.mkdir()
        (sibling / "Ideas" / "owner").mkdir(parents=True)
        try:
            prune_vacated_owner_folders(
                str(vault), [os.path.join("..", sibling.name, "Ideas", "owner", "x.md")], ROUTER
            )
            assert (sibling / "Ideas" / "owner").is_dir()
        finally:
            import shutil
            shutil.rmtree(sibling)

    def test_idempotent_on_missing_dirs(self, vault):
        prune_vacated_owner_folders(str(vault), ["Ideas/gone/x.md"], ROUTER)
        prune_vacated_owner_folders(str(vault), ["Ideas/gone/x.md"], ROUTER)
        assert (vault / "Ideas").is_dir()


class TestMoveEnginePruning:
    def test_prune_router_prunes_vacated_source_dir(self, vault):
        _write(vault / "Ideas" / "owner" / "x.md")
        rename.move_and_update_links(
            str(vault),
            [{"source": "Ideas/owner/x.md", "dest": "Ideas/x.md"}],
            prune_router=ROUTER,
        )
        assert not (vault / "Ideas" / "owner").exists()
        assert (vault / "Ideas" / "x.md").is_file()

    def test_without_prune_router_nothing_is_pruned(self, vault):
        _write(vault / "Ideas" / "owner" / "x.md")
        rename.move_and_update_links(
            str(vault), [{"source": "Ideas/owner/x.md", "dest": "Ideas/x.md"}]
        )
        assert (vault / "Ideas" / "owner").is_dir()

    def test_attachment_sources_are_never_prune_sources(self, vault):
        scope = vault / "_Assets" / "Attachments" / "ideas~x"
        scope.mkdir(parents=True)
        (scope / "pic.png").write_bytes(b"png")
        rename.move_and_update_links(
            str(vault),
            [{
                "source": "_Assets/Attachments/ideas~x/pic.png",
                "dest": "_Assets/Attachments/ideas~y/pic.png",
            }],
            allow_attachment_paths=True,
            prune_router=ROUTER,
        )
        assert scope.is_dir(), "attachment scope cleanup belongs to upload_attachment"
        assert (vault / "_Assets" / "Attachments" / "ideas~y" / "pic.png").is_file()

    def test_prune_does_not_run_after_partial_apply(self, vault, monkeypatch):
        _write(vault / "Ideas" / "a" / "x.md")
        _write(vault / "Ideas" / "b" / "y.md")
        real_rename = os.rename
        calls = []

        def flaky_rename(src, dst):
            calls.append(src)
            if len(calls) == 2:
                raise OSError("disk says no")
            return real_rename(src, dst)

        monkeypatch.setattr(rename.os, "rename", flaky_rename)
        with pytest.raises(rename.PartialApplyError):
            rename.move_and_update_links(
                str(vault),
                [
                    {"source": "Ideas/a/x.md", "dest": "Ideas/x.md"},
                    {"source": "Ideas/b/y.md", "dest": "Ideas/y.md"},
                ],
                prune_router=ROUTER,
            )
        assert (vault / "Ideas" / "a").is_dir(), "prune must not run on a partial move set"

    def test_validation_router_alone_does_not_prune(self, vault):
        _write(vault / "Ideas" / "owner" / "x.md")
        rename.rename_and_update_links(
            str(vault), "Ideas/owner/x.md", "Ideas/x.md", router=ROUTER
        )
        assert (vault / "Ideas" / "owner").is_dir()

    def test_rename_prune_router_prunes(self, vault):
        _write(vault / "Ideas" / "owner" / "x.md")
        rename.rename_and_update_links(
            str(vault), "Ideas/owner/x.md", "Ideas/x.md", prune_router=ROUTER
        )
        assert not (vault / "Ideas" / "owner").exists()

    def test_rename_artefact_prunes_vacated_owner_folder(self, vault):
        _write(vault / "Ideas" / "owner" / "x.md")
        rename.rename_artefact(str(vault), ROUTER, "Ideas/owner/x.md", "Ideas/x.md")
        assert not (vault / "Ideas" / "owner").exists()

    def test_cli_rename_prunes_vacated_owner_folder(self, vault, monkeypatch, capsys):
        _write(vault / "Ideas" / "owner" / "x.md")
        monkeypatch.setattr(rename, "load_fresh_compiled_router", lambda _vault_root: ROUTER)
        rename.main(["Ideas/owner/x.md", "Ideas/x.md", "--vault", str(vault), "--json"])
        assert not (vault / "Ideas" / "owner").exists()


class TestDeletePruning:
    def test_prune_router_delete_prunes_vacated_owner_folder(self, vault):
        _write(vault / "Ideas" / "owner" / "x.md")
        rename.delete_and_clean_links(
            str(vault), "Ideas/owner/x.md", router=ROUTER, prune_router=ROUTER
        )
        assert not (vault / "Ideas" / "owner").exists()
        assert (vault / "Ideas").is_dir()

    def test_gating_router_alone_never_prunes(self, vault):
        _write(vault / "Ideas" / "owner" / "x.md")
        rename.delete_and_clean_links(str(vault), "Ideas/owner/x.md", router=ROUTER)
        assert (vault / "Ideas" / "owner").is_dir()

    def test_router_less_delete_never_prunes(self, vault):
        _write(vault / "Ideas" / "owner" / "x.md")
        rename.delete_and_clean_links(str(vault), "Ideas/owner/x.md")
        assert (vault / "Ideas" / "owner").is_dir()
