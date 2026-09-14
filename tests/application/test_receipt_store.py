"""Bounded receipt storage and still-unknown query semantics."""

from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timedelta

import pytest

from _application.receipts import (
    CommittedEffect,
    MemoryReceiptStore,
    OutcomeReceipt,
    OutcomeReference,
    ReceiptLookupState,
    ReceiptPolicy,
    ReceiptState,
)
from _application.requests import InvocationReadPayload


NOW = datetime.fromisoformat("2026-08-09T12:00:00+10:00")


class _Clock:
    def __init__(self, now=NOW):
        self.value = now

    def now(self):
        return self.value


def _receipt(invocation_id: str, recorded_at=NOW, *, state=ReceiptState.NONE):
    effects = (
        (CommittedEffect("artefact_created", "idea/example"),)
        if state is ReceiptState.COMMITTED
        else ()
    )
    return OutcomeReceipt(
        OutcomeReference(invocation_id),
        "artefact.create",
        1,
        state,
        recorded_at,
        effects,
    )


def test_missing_or_expired_receipt_remains_still_unknown():
    clock = _Clock()
    store = MemoryReceiptStore(clock, ReceiptPolicy(timedelta(minutes=5), 10))
    reference = OutcomeReference("missing")

    lookup = store.lookup(reference)

    assert lookup.state is ReceiptLookupState.STILL_UNKNOWN
    payload = InvocationReadPayload(reference, lookup.state, None, None)
    assert payload.intent is None and payload.outcome is None


def test_cleanup_expires_records_without_reclassifying_their_effects():
    clock = _Clock()
    store = MemoryReceiptStore(clock, ReceiptPolicy(timedelta(minutes=5), 10))
    receipt = _receipt("old", NOW - timedelta(minutes=4))
    store.write(receipt)
    assert store.lookup(receipt.reference).state is ReceiptLookupState.FOUND

    clock.value = NOW + timedelta(minutes=2)

    assert store.cleanup() == 1
    assert store.lookup(receipt.reference).state is ReceiptLookupState.STILL_UNKNOWN


def test_store_is_bounded_and_receipts_are_immutable():
    clock = _Clock()
    store = MemoryReceiptStore(clock, ReceiptPolicy(timedelta(days=1), 2))
    first = _receipt("one", NOW - timedelta(seconds=2))
    second = _receipt("two", NOW - timedelta(seconds=1))
    third = _receipt("three", NOW)
    for receipt in (first, second, third):
        store.write(receipt)

    assert store.read(first.reference) is None
    assert store.read(second.reference) == second
    assert store.read(third.reference) == third
    with pytest.raises(ValueError, match="immutable"):
        store.write(_receipt("two", NOW, state=ReceiptState.COMMITTED))


def test_receipt_model_cannot_store_request_results_or_credentials():
    assert {field.name for field in fields(OutcomeReceipt)} == {
        "reference",
        "command_id",
        "command_version",
        "state",
        "recorded_at",
        "committed_effects",
    }


def test_receipt_policy_rejects_unbounded_or_non_positive_values():
    with pytest.raises(ValueError, match="retention"):
        ReceiptPolicy(timedelta(0), 1)
    with pytest.raises(ValueError, match="max_records"):
        ReceiptPolicy(timedelta(days=1), 0)
