"""Only maintained active-list revisions are eligible for dynamic delivery."""
from pathlib import Path

import pytest

from _command_interface import derived_snapshots
from _command_interface.derived_snapshots import FileDerivedSnapshotStore


@pytest.mark.parametrize('location', ['archived', 'all'])
def test_archive_revision_is_unavailable_without_filesystem_work(tmp_path, monkeypatch, location):
    snapshots = FileDerivedSnapshotStore(tmp_path)

    def forbidden(*args, **kwargs):
        pytest.fail('archive revision validation accessed the filesystem')

    monkeypatch.setattr(derived_snapshots, '_signature', forbidden)
    monkeypatch.setattr(Path, 'rglob', forbidden)
    assert snapshots.list_revision(location) is None


def test_active_revision_observes_only_router_and_lexical_projection(tmp_path, monkeypatch):
    snapshots = FileDerivedSnapshotStore(tmp_path)
    calls = []
    version = [1]

    def signature(path):
        calls.append(path)
        return (1, 2, 3, version[0])

    monkeypatch.setattr(derived_snapshots, '_signature', signature)
    first = snapshots.list_revision('active')
    assert first == snapshots.list_revision('active')
    version[0] = 2
    assert snapshots.list_revision('active') != first
    expected = [tmp_path / derived_snapshots.COMPILED_ROUTER_REL,
                tmp_path / derived_snapshots.OUTPUT_PATH]
    assert calls == expected * 3
