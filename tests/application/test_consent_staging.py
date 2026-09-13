"""Prepared content belongs to its owner and is independent of staging expiry."""

import os
import time

import pytest

from _bootstrap.consent_state import MemoryStateStore
from _command_interface.consent_staging import ConsentContentPins
import _command_interface.consent_staging as pins_module
from _staging import (
    STAGING_TTL_SECONDS, _handle_path, inspect_staged_body,
    inspect_staged_body_for_discard, stage_body, sweep_staged_bodies,
)


def test_prepare_inspection_never_deletes_expired_content(tmp_path):
    handle = stage_body(str(tmp_path), "retained")["handle"]
    path = _handle_path(str(tmp_path), handle)
    old = time.time() - STAGING_TTL_SECONDS - 10
    os.utime(path, (old, old))
    with pytest.raises(ValueError, match="Expired"):
        inspect_staged_body(str(tmp_path), handle)
    assert inspect_staged_body_for_discard(str(tmp_path), handle) == b"retained"
    assert os.path.isfile(path)


def test_discard_inspection_accepts_absence_without_creating_files(tmp_path):
    before = tuple(tmp_path.iterdir())
    assert inspect_staged_body_for_discard(str(tmp_path), "body:" + "a" * 32) is None
    assert tuple(tmp_path.iterdir()) == before


def test_pin_survives_original_stage_sweep(tmp_path):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    store = MemoryStateStore()
    pins = ConsentContentPins(directory, store, namespace="operation-one", coordination_path=tmp_path.parent / (tmp_path.name + ".pins.lock"))
    handle = stage_body(str(tmp_path), "immutable")["handle"]
    source = "stage:" + handle
    pins.retain_content(source, inspect_staged_body(str(tmp_path), handle).encode())
    sweep_staged_bodies(str(tmp_path), now=time.time() + STAGING_TTL_SECONDS + 10)
    assert not os.path.exists(_handle_path(str(tmp_path), handle))
    assert ConsentContentPins(directory, store, namespace="operation-one", coordination_path=tmp_path.parent / (tmp_path.name + ".pins.lock")).read_pinned(source) == b"immutable"


def test_other_descriptor_cannot_read_or_discard_pin(tmp_path):
    store = MemoryStateStore()
    first = ConsentContentPins(tmp_path, store, namespace="first", coordination_path=tmp_path.parent / (tmp_path.name + ".pins.lock"))
    second = ConsentContentPins(tmp_path, store, namespace="second", coordination_path=tmp_path.parent / (tmp_path.name + ".pins.lock"))
    first.retain_content("stage:source", b"private")
    assert second.read_pinned("stage:source") is None
    second.discard()
    assert first.read_pinned("stage:source") == b"private"


def test_deduplicated_content_is_deleted_only_after_final_pin(tmp_path):
    store = MemoryStateStore()
    first = ConsentContentPins(tmp_path, store, namespace="first", coordination_path=tmp_path.parent / (tmp_path.name + ".pins.lock"))
    second = ConsentContentPins(tmp_path, store, namespace="second", coordination_path=tmp_path.parent / (tmp_path.name + ".pins.lock"))
    record = first.retain_content("body", b"same")
    second.retain_content("body", b"same")
    first.discard()
    assert second.read_pinned("body") == b"same"
    second.discard()
    assert not (tmp_path / record["pin"]).exists()


def test_pin_bytes_cannot_change_behind_same_reference(tmp_path):
    pins = ConsentContentPins(tmp_path, MemoryStateStore(), namespace="first", coordination_path=tmp_path.parent / (tmp_path.name + ".pins.lock"))
    record = pins.retain_content("body", b"original")
    with pytest.raises(ValueError, match="changed"):
        pins.retain_content("body", b"different")
    (tmp_path / record["pin"]).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="changed"):
        pins.read_pinned("body")


def test_pin_storage_refuses_capacity_without_removing_live_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(pins_module, "MAX_STAGING_BYTES", 8)
    pins = ConsentContentPins(tmp_path, MemoryStateStore(), namespace="first", coordination_path=tmp_path.parent / (tmp_path.name + ".pins.lock"))
    pins.retain_content("one", b"12345")
    with pytest.raises(ValueError, match="capacity"):
        pins.retain_content("two", b"67890")
    assert pins.read_pinned("one") == b"12345"
    assert pins.read_pinned("two") is None


def test_symlink_staging_cannot_be_preparation_input(tmp_path):
    handle = stage_body(str(tmp_path), "inside")["handle"]
    path = _handle_path(str(tmp_path), handle)
    os.unlink(path)
    os.symlink(__file__, path)
    with pytest.raises(ValueError, match="regular"):
        inspect_staged_body(str(tmp_path), handle)


def test_metadata_capacity_failure_reclaims_unreferenced_blob(tmp_path):
    store = MemoryStateStore(max_bytes=1)
    pins = ConsentContentPins(tmp_path, store, namespace="tiny", coordination_path=tmp_path.parent / (tmp_path.name + ".pins.lock"))
    with pytest.raises(ValueError):
        pins.retain_content("body", b"uncommitted")
    assert pins._content_files() == ()


def test_failed_unlink_is_retried_after_pin_records_are_removed(tmp_path, monkeypatch):
    from pathlib import Path
    pins = ConsentContentPins(tmp_path, MemoryStateStore(), namespace="first", coordination_path=tmp_path.parent / (tmp_path.name + ".pins.lock"))
    record = pins.retain_content("body", b"content")
    unlink = Path.unlink

    def fail_once(path, **kwargs):
        if path.name == record["pin"]:
            raise OSError("temporary failure")
        return unlink(path, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_once)
    with pytest.raises(OSError):
        pins.discard()
    assert pins.read_pinned("body") is None
    monkeypatch.setattr(Path, "unlink", unlink)
    pins.discard()
    assert pins._content_files() == ()


def test_unique_blob_count_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(pins_module, "MAX_STAGING_FILES", 1)
    pins = ConsentContentPins(tmp_path, MemoryStateStore(), namespace="first", coordination_path=tmp_path.parent / (tmp_path.name + ".pins.lock"))
    pins.retain_content("one", b"content")
    pins.retain_content("same", b"content")
    with pytest.raises(ValueError, match="capacity"):
        pins.retain_content("two", b"different")


def test_pin_collection_handles_multiple_metadata_pages(tmp_path):
    pins = ConsentContentPins(tmp_path, MemoryStateStore(), namespace="many", coordination_path=tmp_path.parent / (tmp_path.name + ".pins.lock"))
    for index in range(130):
        pins.retain_content(str(index), b"shared")
    assert pins.read_pinned("129") == b"shared"
    pins.discard()
    assert pins._content_files() == ()


def test_coordination_cannot_live_inside_private_content(tmp_path):
    with pytest.raises(ValueError, match="outside"):
        ConsentContentPins(tmp_path, MemoryStateStore(), namespace="operation",
                           coordination_path=tmp_path / ".pins.lock")
