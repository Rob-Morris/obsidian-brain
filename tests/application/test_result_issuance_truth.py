"""Credential publication failure must preserve typed source execution truth."""

from dataclasses import replace
from hashlib import sha256
import io
import json
from types import SimpleNamespace

import pytest

from _application.adapter import ApplicationAdapter, project_adapter_result
from _application.projection import canonical_result_envelope
from _application.receipts import CommittedEffect, OutcomeReference
from _application.registry import current_application_catalogue
from _application.results import (
    CommandError, CommandWarning, Error, ErrorCode, Ok, OutcomeUnknownDetails,
    Partial, WarningCode,
)
from _command_interface import script


@pytest.mark.parametrize('branch', ['ok', 'partial', 'no_effect', 'unknown'])
@pytest.mark.parametrize('oversized', [False, True], ids=['fits', 'final_guard'])
@pytest.mark.parametrize('failure_type', [OSError, RuntimeError, AttributeError])
def test_issuance_failure_preserves_typed_outcome(
    tmp_path, monkeypatch, branch, oversized, failure_type,
):
    entry = next(item for item in current_application_catalogue().entries
                 if item.command_id == 'session.start')
    identity = (entry.command_id, entry.command_version)
    effect = CommittedEffect('saved', 'subject' * (3000 if oversized else 1))
    warnings = (CommandWarning(WarningCode.FOLLOW_UP_REQUIRED,
                              'source warning ' * (800 if oversized else 1)),)
    conflict = CommandError(ErrorCode.CONFLICT, 'Source conflict.')
    reference = OutcomeReference('issuance-truth-invocation')
    if branch == 'ok':
        source = Ok(*identity, {}, (effect,), warnings)
    elif branch == 'partial':
        source = Partial(*identity, conflict, (effect,), warnings)
    elif branch == 'no_effect':
        source = Error(*identity, conflict, warnings=warnings)
    else:
        source = Error(*identity, CommandError(
            ErrorCode.COMMAND_OUTCOME_UNKNOWN, 'Source outcome is unknown.',
            OutcomeUnknownDetails(reference)), effects='unknown',
            outcome_reference=reference, warnings=warnings)

    original = canonical_result_envelope(source)
    envelope = {**original, 'delivery': {
        'status': 'truncated', 'availability': 'available', 'result_id': 'retained-id',
        'continuation': 'opaque-next-page', 'outside_continuation': 'opaque-outside-page',
    }}
    secret = 'private-issuance-truth-credential'
    projection = replace(project_adapter_result(source), delivery_envelope=envelope,
                         result_session_credential=secret)
    assert script._cli_result_fits(envelope, projection.concise_text) is not oversized
    calls = []

    def invoke(self, context, command_id, payload):
        calls.append(command_id)
        return projection

    def fail_publication(*args):
        raise failure_type(secret)

    monkeypatch.setattr(ApplicationAdapter, 'invoke', invoke)
    monkeypatch.setattr(script, 'resolve_direct_vault', lambda *a, **kw: tmp_path)
    monkeypatch.setattr(script, '_write_result_session', fail_publication)
    destination = tmp_path / 'session'
    stdout, stderr = io.StringIO(), io.StringIO()
    exit_code = script.run(
        ['session', 'start', '--json', '--result-session-file', str(destination)],
        context_factory=lambda **kw: SimpleNamespace(), stdout=stdout, stderr=stderr,
    )
    output = json.loads(stdout.getvalue())

    assert calls == ['session.start']
    assert exit_code == {'ok': 0, 'partial': 1, 'no_effect': 2, 'unknown': 4}[branch]
    assert output['status'] == source.status
    assert script._cli_result_fits(output, '')
    assert output['delivery']['status'] == output['delivery']['availability'] == 'unavailable'
    assert output['delivery']['reason'] == 'result_session_unavailable'
    assert output['delivery']['continuation'] is None
    assert output['delivery'].get('outside_continuation') is None
    assert 'result_session_file' not in output and 'result_session' not in output
    assert not destination.exists()
    assert secret not in stdout.getvalue() + stderr.getvalue()
    assert 'do not rerun a mutation' in stderr.getvalue()

    if branch in ('no_effect', 'unknown'):
        assert output['error']['code'] == original['error']['code']
        assert output['error']['effects'] == source.effects
        assert output['error']['retryable'] is False
        if branch == 'unknown':
            assert output['error']['outcome_reference'] == {'invocation_id': reference.invocation_id}
        else:
            assert 'outcome_reference' not in output['error']
    else:
        effects = (original['result']['committed_effects'] if branch == 'partial'
                   else original['committed_effects'])
        if oversized:
            assert 'committed_effects' not in output
            summary = output['committed_effect_summary']
            assert summary['count'] == len(effects)
            assert summary['sha256'] == sha256(json.dumps(
                effects, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
        elif branch == 'partial':
            assert output['result']['committed_effects'] == effects
        else:
            assert output['committed_effects'] == effects
        if branch == 'partial':
            assert output['error']['code'] == original['error']['code']
            assert output['error']['effects'] == 'known'

    if oversized:
        assert output['result'] is None
        assert output['warnings'][0]['code'] == 'source_warnings_retained'
    else:
        assert output['warnings'] == original['warnings']
