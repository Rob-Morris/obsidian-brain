"""Public receipt recovery never discloses another caller's persisted outcome."""
from dataclasses import replace

import pytest

from command_application import context_for
from _application.adapter import ApplicationAdapter
from _application.receipts import (
    AdmissionIntent, CommittedEffect, ExecutionState, InvocationOutcome,
    OutcomeReceipt, OutcomeReference, ReceiptOwnership, ReceiptState,
)
from _application.registry import current_application_catalogue, current_request_resolver
from _command_interface.receipts import OwnedReceiptStore


@pytest.mark.parametrize('foreign', [
    {}, {'brain_id': 'other-brain'}, {'principal': 'other-principal'},
    {'kind': 'cli-job'}, {'context_id': 'other-context'},
])
def test_public_owned_receipt_lookup_maps_foreign_context_without_content(tmp_path, foreign):
    (tmp_path / '.brain-core').mkdir()
    (tmp_path / '.brain-core' / 'VERSION').write_text('0.68.0\n')
    context = context_for(tmp_path)
    identity = context.authorisation.service.identity
    owner = ReceiptOwnership(identity.brain_id, identity.principal, identity.kind, identity.context_id)
    saved = OwnedReceiptStore(tmp_path, context.clock, replace(owner, **foreign))
    reference = OutcomeReference('private-invocation')
    intent = AdmissionIntent(reference, 'document.write-body', 1, context.clock.now(),
        'initial', 'private-generation', 'host-request')
    outcome = InvocationOutcome(OutcomeReceipt(reference, 'document.write-body', 1,
        ReceiptState.COMMITTED, context.clock.now(),
        (CommittedEffect('document', 'Private/foreign-subject.md'),)), ExecutionState.SUCCEEDED)
    saved.begin(intent)
    saved.finalise(outcome)
    caller = OwnedReceiptStore(tmp_path, context.clock, owner)
    adapter = ApplicationAdapter(current_application_catalogue(), current_request_resolver())

    projected = adapter.invoke(replace(context, receipt_reader=caller),
                               'invocation.read', {'invocation_id': reference.invocation_id})

    if not foreign:
        assert projected.structured_content['result']['intent']['source'] == 'host-request'
        assert projected.structured_content['result']['outcome']['execution'] == 'succeeded'
        return
    assert projected.is_error
    assert projected.structured_content['error']['code'] == 'authority_denied'
    assert projected.structured_content['error']['details']['boundary'] == 'context'
    assert projected.structured_content['error']['details']['reason'] == 'foreign_context'
    assert projected.structured_content['error']['details']['requestable'] is False
    assert projected.structured_content['error']['effects'] == 'none'
    assert projected.structured_content['result'] is None
    for private in ('private-generation', 'foreign-subject',
                    'document.write-body', *foreign.values()):
        assert private not in projected.json_text
    assert saved.read(reference).intent == intent
    assert saved.read(reference).outcome == outcome
