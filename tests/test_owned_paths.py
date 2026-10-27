"""Owned namespaces reject redirection before inspecting or changing records."""

import os
import pytest

from _bootstrap.owned_paths import (
    UnsafeFilesystemLayoutError, owned_directory, owned_file, owned_output_paths,
)


def test_observing_missing_directory_creates_nothing(tmp_path):
    with pytest.raises(FileNotFoundError):
        owned_directory(tmp_path, ".brain/local/staging")
    assert not (tmp_path / ".brain").exists()


def test_owned_directory_creation_and_record_lookup(tmp_path):
    directory = owned_directory(tmp_path, ".brain/local/staging", create=True)
    record = directory / "record"
    record.write_text("valid")
    assert owned_file(tmp_path, ".brain/local/staging/record") == record
    assert owned_file(tmp_path, ".brain/local/staging/new", missing_ok=True) == directory / "new"


@pytest.mark.parametrize("component", [".brain", ".brain/local", ".brain/local/staging"])
def test_owned_directory_refuses_linked_components_without_changes(tmp_path, component):
    root = tmp_path / "vault"
    root.mkdir()
    other = tmp_path / "ordinary"
    other.mkdir()
    sentinel = other / "sentinel"
    sentinel.write_text("unchanged")
    link = root / component
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(other, target_is_directory=True)
    with pytest.raises(UnsafeFilesystemLayoutError):
        owned_directory(root, ".brain/local/staging", create=True)
    assert sentinel.read_text() == "unchanged"
    assert set(other.iterdir()) == {sentinel}


def test_selected_root_alias_is_supported(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    alias = tmp_path / "selected"
    alias.symlink_to(root, target_is_directory=True)
    assert owned_directory(alias, ".brain/local", create=True) == root / ".brain/local"


def test_owned_record_rejects_final_link_and_directory(tmp_path):
    directory = owned_directory(tmp_path, ".brain/local", create=True)
    original = tmp_path / "ordinary"
    original.write_text("unchanged")
    (directory / "linked").symlink_to(original)
    (directory / "folder").mkdir()
    for name in ("linked", "folder"):
        with pytest.raises(UnsafeFilesystemLayoutError):
            owned_file(tmp_path, f".brain/local/{name}")
    assert original.read_text() == "unchanged"


def test_single_link_record_policy(tmp_path):
    directory = owned_directory(tmp_path, ".brain/local", create=True)
    original = tmp_path / "ordinary"
    original.write_text("unchanged")
    os.link(original, directory / "record")
    with pytest.raises(UnsafeFilesystemLayoutError):
        owned_file(tmp_path, ".brain/local/record", single_link=True)
    assert original.read_text() == "unchanged"


def test_installation_version_metadata_refuses_redirected_source(tmp_path, monkeypatch):
    import builtins
    from _common import read_version

    core = tmp_path / ".brain-core"
    core.mkdir()
    ordinary = tmp_path / "ordinary"
    ordinary.write_text("Synthetic only")
    (core / "VERSION").symlink_to(ordinary)
    original = builtins.open

    def observed(path, *args, **kwargs):
        if os.path.realpath(path) == str(ordinary):
            pytest.fail("version capability opened redirected content")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", observed)
    with pytest.raises(UnsafeFilesystemLayoutError):
        read_version(tmp_path)


def test_output_workset_observation_does_not_create_missing_parents(tmp_path):
    before = set(tmp_path.iterdir())
    paths = owned_output_paths(tmp_path, (".brain/local/one.json", ".obsidian/graph.json"))
    assert paths == (tmp_path / ".brain/local/one.json", tmp_path / ".obsidian/graph.json")
    assert set(tmp_path.iterdir()) == before


def test_complete_output_workset_is_refused_without_earlier_effects(tmp_path):
    parent = owned_directory(tmp_path, ".brain/local", create=True)
    ready = parent / "ready.json"
    ready.write_text("Existing readiness")
    other = tmp_path / "ordinary"
    other.write_text("Unrelated")
    (parent / "array.npy").symlink_to(other)
    with pytest.raises(UnsafeFilesystemLayoutError):
        owned_output_paths(tmp_path, (".brain/local/ready.json", ".brain/local/array.npy"))
    assert ready.read_text() == "Existing readiness"
    assert other.read_text() == "Unrelated"


@pytest.mark.parametrize("relative", ["../other", "/outside", "."])
def test_internal_capability_cannot_select_an_arbitrary_path(tmp_path, relative):
    with pytest.raises(ValueError):
        owned_directory(tmp_path, relative, create=True)
