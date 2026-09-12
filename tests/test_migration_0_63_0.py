from __future__ import annotations

from pathlib import Path

import pytest

import migrate_to_0_63_0


def _note(path: Path, title: str = "Rust Lifetimes") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\ntype: living/note\nkey: rust-lifetimes\ntags:\n  - rust\n---\n\n"
        f"# {title}\n",
        encoding="utf-8",
    )


def test_renames_legacy_notes_recursively_and_updates_links(tmp_path: Path) -> None:
    old_root = tmp_path / "Notes" / "20260315 - Rust Lifetimes.md"
    old_owned = tmp_path / "Notes" / "project~brain" / "20260316 - API Notes.md"
    _note(old_root)
    _note(old_owned, "API Notes")
    reference = tmp_path / "Projects" / "Brain.md"
    reference.parent.mkdir()
    reference.write_text(
        "See [[20260315 - Rust Lifetimes]] and [[20260316 - API Notes|API notes]].\n",
        encoding="utf-8",
    )

    result = migrate_to_0_63_0.migrate(str(tmp_path))

    assert result["status"] == "ok"
    assert result["links_updated"] == 2
    assert not old_root.exists()
    assert not old_owned.exists()
    assert (tmp_path / "Notes" / "Rust Lifetimes.md").is_file()
    assert (tmp_path / "Notes" / "project~brain" / "API Notes.md").is_file()
    assert reference.read_text(encoding="utf-8") == (
        "See [[Rust Lifetimes]] and [[API Notes|API notes]].\n"
    )
    assert migrate_to_0_63_0.migrate(str(tmp_path)) == {
        "status": "skipped",
        "moves": [],
        "links_updated": 0,
    }


def test_ignores_non_notes_and_modern_note_names(tmp_path: Path) -> None:
    _note(tmp_path / "Notes" / "Modern Note.md", "Modern Note")
    other = tmp_path / "Notes" / "20260315 - Not A Note.md"
    other.write_text("---\ntype: living/wiki\n---\n", encoding="utf-8")

    result = migrate_to_0_63_0.migrate(str(tmp_path))

    assert result["status"] == "skipped"
    assert other.is_file()


def test_collision_fails_before_any_rename(tmp_path: Path) -> None:
    first = tmp_path / "Notes" / "20260315 - Alpha.md"
    second = tmp_path / "Notes" / "nested" / "20260316 - Beta.md"
    _note(first, "Alpha")
    _note(second, "Beta")
    _note(tmp_path / "Notes" / "nested" / "Beta.md", "Existing Beta")

    with pytest.raises(FileExistsError, match="Destination file already exists"):
        migrate_to_0_63_0.migrate(str(tmp_path))

    assert first.is_file()
    assert second.is_file()
    assert not (tmp_path / "Notes" / "Alpha.md").exists()


@pytest.mark.parametrize(
    "folder,first_title,second_title",
    [("", "Review", "review"), ("project~two", "Review", "Review"),
     ("", "Caf\u00e9", "Cafe\u0301")],
)
def test_destination_title_collisions_fail_before_rewriting(
    tmp_path, folder, first_title, second_title
):
    first = tmp_path / "Notes" / f"20260910 - {first_title}.md"
    second = tmp_path / "Notes" / folder / f"20260911 - {second_title}.md"
    _note(first, "First")
    _note(second, "Second")
    link = tmp_path / "AGENTS.md"
    link.write_text(f"See [[20260910 - {first_title}]].\n")
    original = {path: path.read_bytes() for path in (first, second, link)}

    with pytest.raises(ValueError, match="ambiguous Note title"):
        migrate_to_0_63_0.migrate(str(tmp_path))

    assert {path: path.read_bytes() for path in original} == original


def test_existing_basename_in_another_folder_fails_before_rewriting(tmp_path):
    source = tmp_path / "Notes" / "20260910 - Review.md"
    _note(source)
    existing = tmp_path / "Wiki" / "Review.md"
    existing.parent.mkdir()
    existing.write_text("Existing reference\n")
    with pytest.raises(ValueError, match="ambiguous Note title"):
        migrate_to_0_63_0.migrate(str(tmp_path))
    assert source.exists()
    assert existing.read_text() == "Existing reference\n"


def test_declared_effects_restore_all_link_edits_after_a_failed_move(tmp_path, monkeypatch):
    import rename
    import upgrade
    from _common import PartialApplyError

    source = tmp_path / "Notes" / "20260910 - Review.md"
    _note(source)
    root_link = tmp_path / "AGENTS.md"
    other_link = tmp_path / "Unconfigured" / "Reference.md"
    other_link.parent.mkdir()
    for path in (root_link, other_link):
        path.write_text("See [[20260910 - Review]].\n")
    snapshots = {}
    for path in migrate_to_0_63_0.prospective_effects(str(tmp_path)):
        upgrade._snapshot_file(str(path), snapshots)
    def fail_move(*_args):
        raise OSError("injected move failure")

    monkeypatch.setattr(rename.os, "rename", fail_move)
    with pytest.raises(PartialApplyError, match="injected move failure"):
        migrate_to_0_63_0.migrate(str(tmp_path))
    restored = upgrade._restore_snapshots(snapshots)
    assert not restored.errors
    assert source.exists()
    for path in (root_link, other_link):
        assert path.read_text() == "See [[20260910 - Review]].\n"
