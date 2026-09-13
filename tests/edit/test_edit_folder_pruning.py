"""Vacated owner folders are pruned by every lifecycle move through the move engine."""

import compile_router
import edit
import rename


def _idea(vault, rel, *, key, parent=None, status="shaping"):
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    tags = f"  - {parent}\n" if parent else ""
    parent_line = f"parent: {parent}\n" if parent else ""
    path.write_text(
        "---\n"
        "type: living/ideas\n"
        f"tags:\n{tags}"
        f"key: {key}\n"
        f"{parent_line}"
        f"status: {status}\n"
        "---\n\n"
        f"{key}.\n"
    )


def _parent_child(vault):
    _idea(vault, "Ideas/Parent.md", key="parent")
    _idea(vault, "Ideas/parent/Child.md", key="child", parent="ideas/parent")
    return compile_router.compile(str(vault))


class TestDeletePrunesOwnerFolders:
    def test_delete_last_child_prunes_owner_folder(self, vault):
        router = _parent_child(vault)
        rename.delete_and_clean_links(
            str(vault), "Ideas/parent/Child.md", router=router, prune_router=router
        )
        assert not (vault / "Ideas" / "parent").exists()
        assert (vault / "Ideas" / "Parent.md").is_file()

    def test_recursive_delete_prunes_nested_empty_dirs(self, vault):
        router = _parent_child(vault)
        (vault / "Ideas" / "parent" / "child").mkdir()
        rename.delete_and_clean_links(
            str(vault), "Ideas/Parent.md", router=router, recursive=True, prune_router=router
        )
        assert not (vault / "Ideas" / "parent").exists()
        assert (vault / "Ideas").is_dir()


class TestArchivePrunesOwnerFolders:
    def test_archive_child_prunes_owner_folder(self, vault):
        router = _parent_child(vault)
        edit.archive_artefact(str(vault), router, "Ideas/parent/Child.md")
        assert not (vault / "Ideas" / "parent").exists()

    def test_unarchive_prunes_empty_archive_mirror_chain(self, vault, router):
        archived = vault / "_Archive" / "Ideas" / "Brain" / "20260101-my-idea.md"
        archived.parent.mkdir(parents=True)
        archived.write_text(
            "---\ntype: living/ideas\ntags: []\nstatus: adopted\n"
            "archiveddate: 2026-01-01\n---\n\nOld idea.\n"
        )
        result = edit.unarchive_artefact(
            str(vault), router, "_Archive/Ideas/Brain/20260101-my-idea.md"
        )
        assert (vault / result["new_path"]).is_file()
        assert not (vault / "_Archive" / "Ideas").exists()
        assert (vault / "_Archive").is_dir()


class TestStatusMovePrunesFolders:
    def test_revive_prunes_status_folder_and_empty_owner_chain(self, vault):
        _idea(vault, "Ideas/Parent.md", key="parent", status="adopted")
        _idea(
            vault,
            "Ideas/parent/+Adopted/Child.md",
            key="child",
            parent="ideas/parent",
            status="adopted",
        )
        router = compile_router.compile(str(vault))
        edit.edit_artefact(
            str(vault),
            router,
            "Ideas/parent/+Adopted/Child.md",
            "",
            frontmatter_changes={"status": "shaping"},
        )
        assert (vault / "Ideas" / "parent" / "Child.md").is_file()
        assert not (vault / "Ideas" / "parent" / "+Adopted").exists()


class TestTemporalRelocationPrunesFolders:
    def test_reparent_prunes_vacated_owner_folder(self, vault):
        (vault / "Projects" / "Other.md").write_text(
            "---\ntype: living/project\ntags:\n  - project/other\nkey: other\n---\n\n# Other\n"
        )
        log = vault / "_Temporal" / "Logs" / "project~brain" / "log-Session.md"
        log.parent.mkdir(parents=True)
        log.write_text(
            "---\ntype: temporal/logs\ntags:\n  - session\n  - project/brain\n"
            "parent: project/brain\ncreated: 2026-03-04T09:00:00+10:00\n---\n\nSession.\n"
        )
        router = compile_router.compile(str(vault))
        result = edit.edit_artefact(
            str(vault),
            router,
            "_Temporal/Logs/project~brain/log-Session.md",
            "",
            frontmatter_changes={"parent": "project/other"},
        )
        assert result["path"].startswith("_Temporal/Logs/project~other/")
        assert not (vault / "_Temporal" / "Logs" / "project~brain").exists()
        assert (vault / "_Temporal" / "Logs").is_dir()
