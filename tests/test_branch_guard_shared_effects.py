"""Shared cleanup/backlink owners cannot become a durable-write bypass."""

import pytest

from _bootstrap import branch_guard
from _bootstrap.local_config import patch_local_config


@pytest.fixture
def guarded(tmp_path, monkeypatch):
    state = {"branch": "dev"}
    patch_local_config(tmp_path, lambda d: d.update(branch_guard={"allowed_branches": ["dev", "feature"]}))
    monkeypatch.setattr(branch_guard, "observe_git",
                        lambda _: branch_guard.GitState(str(tmp_path), state["branch"]))
    return tmp_path, state


def test_backlinks_stop_after_first_committed_rewrite(guarded, monkeypatch):
    from types import SimpleNamespace
    from _common import _wikilinks
    root, state = guarded
    notes = root / "Notes"
    notes.mkdir()
    for name in ("a", "b"):
        (notes / f"{name}.md").write_text("before")
    plan = SimpleNamespace(unreadable=(), writes=[
        SimpleNamespace(path=f"Notes/{name}.md", after="after", substitutions=1)
        for name in ("a", "b")])
    original = _wikilinks.safe_write_active_or_archived_artefact
    def write(*args, **kwargs):
        original(*args, **kwargs)
        state["branch"] = "feature"
    monkeypatch.setattr(_wikilinks, "safe_write_active_or_archived_artefact", write)
    with pytest.raises(branch_guard.BranchGuardError, match="branch-changed"):
        _wikilinks.apply_wikilink_rewrites(root, plan)
    assert (notes / "a.md").read_text() == "after"
    assert (notes / "b.md").read_text() == "before"


def test_empty_folder_repair_refuses_before_junk_removal(guarded):
    from _common._artefacts import remove_empty_artefact_folders
    root, state = guarded
    child = root / "Notes" / "vacated"
    child.mkdir(parents=True)
    junk = child / ".DS_Store"
    junk.write_bytes(b"junk")
    state["branch"] = "main"
    router = {"artefacts": [{"path": "Notes"}]}
    with pytest.raises(branch_guard.BranchGuardError):
        remove_empty_artefact_folders(root, router, ["Notes/vacated"])
    assert junk.read_bytes() == b"junk"
    assert child.is_dir()


def test_frontmatter_repair_dry_run_allowed_but_write_blocked(guarded):
    from _lifecycle.frontmatter_repairs import normalize_duplicate_frontmatter_documents
    root, state = guarded
    target = root / "Notes" / "note.md"
    target.parent.mkdir()
    target.write_text("before")
    findings = [{"file": "Notes/note.md", "merged_fields": {"title": "Note"}, "body": "body"}]
    state["branch"] = "main"
    assert normalize_duplicate_frontmatter_documents(root, dry_run=True, prepared_findings=findings)["dry_run"]
    with pytest.raises(branch_guard.BranchGuardError):
        normalize_duplicate_frontmatter_documents(root, prepared_findings=findings)
    assert target.read_text() == "before"
