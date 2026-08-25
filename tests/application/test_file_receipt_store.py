"""Durable local outcome-receipt adapter tests."""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib

import pytest

from _application.receipts import (
    CommittedEffect,
    OutcomeReceipt,
    OutcomeReference,
    ReceiptPolicy,
    ReceiptState,
)
from _command_interface.receipts import (
    FileReceiptStore,
    RECEIPT_DIRECTORY,
    RECEIPT_INDEX_NAME,
)


NOW = datetime.fromisoformat("2026-08-10T08:00:00+10:00")


class _Clock:
    def __init__(self, now=NOW):
        self.value = now

    def now(self):
        return self.value


def _vault(tmp_path):
    root = (tmp_path / "Brain").resolve()
    (root / ".brain-core").mkdir(parents=True)
    (root / ".brain-core" / "VERSION").write_text("0.54.48\n")
    return root


def _receipt(invocation_id, *, minutes=0, state=ReceiptState.COMMITTED):
    effects = ()
    if state is ReceiptState.COMMITTED:
        effects = (CommittedEffect("artefact", "design/example"),)
    return OutcomeReceipt(
        OutcomeReference(invocation_id),
        "artefact.create",
        1,
        state,
        NOW + timedelta(minutes=minutes),
        effects,
    )


def test_file_receipts_round_trip_without_using_caller_ids_as_paths(tmp_path):
    root = _vault(tmp_path)
    store = FileReceiptStore(root, _Clock())
    receipt = _receipt("caller/path/../secret")

    store.write(receipt)

    digest = hashlib.sha256(receipt.reference.invocation_id.encode()).hexdigest()
    files = list((root / RECEIPT_DIRECTORY).glob("*.json"))
    assert files == [root / RECEIPT_DIRECTORY / f"{digest}.json"]
    assert store.read(receipt.reference) == receipt
    raw = files[0].read_text()
    assert "request" not in raw
    assert "credential" not in raw


def test_none_receipts_do_not_create_durable_state(tmp_path):
    root = _vault(tmp_path)
    store = FileReceiptStore(root, _Clock())

    store.write(_receipt("read-only", state=ReceiptState.NONE))

    assert not (root / RECEIPT_DIRECTORY).exists()


def test_missing_receipt_read_does_not_create_durable_state(tmp_path):
    root = _vault(tmp_path)
    store = FileReceiptStore(root, _Clock())

    assert store.read(OutcomeReference("missing")) is None
    assert not (root / RECEIPT_DIRECTORY).exists()


def test_expired_receipt_read_is_logically_absent_but_does_not_delete(tmp_path):
    root = _vault(tmp_path)
    clock = _Clock()
    store = FileReceiptStore(
        root,
        clock,
        ReceiptPolicy(retention=timedelta(minutes=5), max_records=2),
    )
    receipt = _receipt("expired")
    store.write(receipt)
    path = next((root / RECEIPT_DIRECTORY).glob("*.json"))
    before = path.read_bytes()
    clock.value = NOW + timedelta(minutes=6)

    assert store.read(receipt.reference) is None
    assert path.read_bytes() == before
    assert store.cleanup() == 1


def test_file_receipts_are_immutable_bounded_and_expire(tmp_path):
    root = _vault(tmp_path)
    clock = _Clock()
    store = FileReceiptStore(
        root,
        clock,
        ReceiptPolicy(retention=timedelta(minutes=5), max_records=2),
    )
    first = _receipt("first", minutes=0)
    second = _receipt("second", minutes=1)
    third = _receipt("third", minutes=2)
    for receipt in (first, second, third):
        store.write(receipt)

    assert store.read(first.reference) is None
    assert store.read(second.reference) == second
    assert store.read(third.reference) == third
    with pytest.raises(ValueError, match="immutable"):
        store.write(_receipt("third", minutes=3))

    clock.value = NOW + timedelta(minutes=7)
    assert store.cleanup() == 1
    assert store.read(second.reference) is None
    assert store.read(third.reference) == third


def test_receipt_write_uses_compact_index_instead_of_inventory(tmp_path, monkeypatch):
    root = _vault(tmp_path)
    store = FileReceiptStore(root, _Clock())
    for index in range(4):
        store.write(_receipt(f"existing-{index}"))

    reads = []
    original = store._read_path

    def count_read(path):
        reads.append(path.name)
        return original(path)

    monkeypatch.setattr(store, "_read_path", count_read)
    store.write(_receipt("new"))

    assert reads == [store._path(OutcomeReference("new")).name]


def test_cleanup_repairs_missing_receipt_index_from_durable_records(tmp_path):
    root = _vault(tmp_path)
    store = FileReceiptStore(root, _Clock())
    receipt = _receipt("repair-index")
    store.write(receipt)
    index_path = root / RECEIPT_DIRECTORY / RECEIPT_INDEX_NAME
    index_path.unlink()

    assert store.cleanup() == 0
    assert index_path.is_file()
    assert store.read(receipt.reference) == receipt


def test_corrupt_receipt_index_is_rebuilt_before_append(tmp_path):
    root = _vault(tmp_path)
    store = FileReceiptStore(root, _Clock())
    first = _receipt("first")
    second = _receipt("second")
    store.write(first)
    index_path = root / RECEIPT_DIRECTORY / RECEIPT_INDEX_NAME
    index_path.write_text("not json", encoding="utf-8")

    store.write(second)

    assert store.read(first.reference) == first
    assert store.read(second.reference) == second


def test_file_receipts_reject_symlinked_storage_and_corrupt_records(tmp_path):
    root = _vault(tmp_path)
    local = root / ".brain" / "local"
    local.mkdir(parents=True)
    external = tmp_path / "external"
    external.mkdir()
    (local / "command-outcomes").symlink_to(external, target_is_directory=True)
    store = FileReceiptStore(root, _Clock())
    with pytest.raises(ValueError, match="symlinked"):
        store.read(OutcomeReference("blocked"))
    with pytest.raises(ValueError, match="symlinked"):
        store.write(_receipt("blocked"))

    (local / "command-outcomes").unlink()
    store.write(_receipt("corrupt"))
    receipt_file = next((local / "command-outcomes").glob("*.json"))
    receipt_file.write_text("{}")
    with pytest.raises(ValueError, match="invalid object shape"):
        store.read(OutcomeReference("corrupt"))


def test_file_receipts_reject_mismatched_hashed_filenames(tmp_path):
    root = _vault(tmp_path)
    store = FileReceiptStore(root, _Clock())
    receipt = _receipt("original")
    store.write(receipt)
    path = next((root / RECEIPT_DIRECTORY).glob("*.json"))
    path.rename(path.with_name("0" * 64 + ".json"))

    with pytest.raises(ValueError, match="filename does not match"):
        store.cleanup()


def test_file_receipts_reject_symlinked_records_and_lock(tmp_path):
    root = _vault(tmp_path)
    directory = root / RECEIPT_DIRECTORY
    directory.mkdir(parents=True)
    missing = tmp_path / "missing"
    (directory / ("0" * 64 + ".json")).symlink_to(missing)
    store = FileReceiptStore(root, _Clock())
    with pytest.raises(ValueError, match="regular file"):
        store.cleanup()

    next(directory.glob("*.json")).unlink()
    (directory / ".receipts.lock").unlink()
    (directory / ".receipts.lock").symlink_to(tmp_path / "external-lock")
    with pytest.raises(ValueError, match="lock must be a regular file"):
        store.cleanup()


def test_file_receipts_reject_symlinked_compact_index(tmp_path):
    root = _vault(tmp_path)
    store = FileReceiptStore(root, _Clock())
    store.write(_receipt("first"))
    index_path = root / RECEIPT_DIRECTORY / RECEIPT_INDEX_NAME
    index_path.unlink()
    index_path.symlink_to(tmp_path / "external-index")

    with pytest.raises(ValueError, match="index must be a regular file"):
        store.write(_receipt("second"))
