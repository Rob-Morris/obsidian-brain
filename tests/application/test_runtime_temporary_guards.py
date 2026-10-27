"""Fixed-scope temporary removal preserves known effects on layout refusal."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from _application.results import ErrorCode
from _application.runtime import remove_temporaries as owner


def _context(root):
    return SimpleNamespace(
        selected_brain=SimpleNamespace(vault_root=root),
        clock=SimpleNamespace(now=lambda: datetime.now(timezone.utc) + timedelta(days=2)),
        dry_run=False,
    )


@pytest.mark.parametrize("refuse_after", [0, 1])
def test_removal_layout_refusal_tracks_only_committed_effects(tmp_path, monkeypatch, refuse_after):
    local = tmp_path / ".brain/local"
    local.mkdir(parents=True)
    paths = [local / f"{name}.json.ab12cd34.tmp" for name in ("a", "b")]
    for path in paths:
        path.write_text("synthetic temporary")
    marker = local / "unrelated.txt"
    marker.write_text("keep")
    real_owned_file = owner.owned_file
    calls = []

    def changed_layout(root, relative, **kwargs):
        if len(calls) == refuse_after:
            path = root / relative
            path.unlink()
            path.mkdir()
        calls.append(relative)
        return real_owned_file(root, relative, **kwargs)

    monkeypatch.setattr(owner, "owned_file", changed_layout)
    monkeypatch.setattr(owner, "admit_owner", lambda *args: None)
    result = owner.execute(_context(tmp_path), owner.RuntimeRemoveTemporariesRequest())
    assert result.error.code is ErrorCode.CONFLICT
    assert result.status == ("partial" if refuse_after else "error")
    if refuse_after:
        assert len(result.committed_effects) == refuse_after
        assert result.committed_effects[0].subject == ".brain/local/a.json.ab12cd34.tmp"
        assert not paths[0].exists()
    else:
        assert result.effects == "none"
        assert result.retryable is False
        assert paths[1].read_text() == "synthetic temporary"
    assert marker.read_text() == "keep"


def test_removal_refuses_non_directory_scope_without_changing_it(tmp_path):
    (tmp_path / ".brain").write_text("keep")
    result = owner.execute(_context(tmp_path), owner.RuntimeRemoveTemporariesRequest())
    assert result.error.code is ErrorCode.CONFLICT
    assert result.retryable is False
    assert result.effects == "none"
    assert (tmp_path / ".brain").read_text() == "keep"


def test_unsafe_atomic_endpoint_refuses_before_any_other_temporary_is_removed(tmp_path):
    local = tmp_path / ".brain/local"
    local.mkdir(parents=True)
    valid = local / "a.json.ab12cd34.tmp"
    valid.write_text("keep until layout is repaired")
    unsafe = local / "b.json.ab12cd34.tmp"
    unsafe.mkdir()
    result = owner.execute(_context(tmp_path), owner.RuntimeRemoveTemporariesRequest())
    assert result.error.code is ErrorCode.CONFLICT
    assert result.effects == "none"
    assert result.retryable is False
    assert valid.read_text() == "keep until layout is repaired"
    assert unsafe.is_dir()
