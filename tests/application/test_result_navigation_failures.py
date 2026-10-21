"""Navigation storage failures preserve recovery and the retained source outcome."""
from dataclasses import replace

import pytest

from _application.adapter import ApplicationAdapter
from _application.registry import current_application_catalogue, current_request_resolver
from _application.result_navigation import build_view
from _command_interface.result_delivery import ResultDeliverySession
from _command_interface.result_store import FileResultStore
from command_application import context_for


@pytest.fixture
def retained(command_vault_clone):
    context = context_for(command_vault_clone.vault_root, profile='operator')
    store = FileResultStore(command_vault_clone.vault_root,
        context.selected_brain.brain_id, context.authorisation.service.identity.principal)
    session = ResultDeliverySession(store, store.issue())
    source = {'schema': 'brain.command-result/1', 'command': 'artefact.read',
              'command_version': 1, 'status': 'ok', 'warnings': [],
              'result': {'content': 'hit one\nhit two\nhit three\n'}}
    view = build_view(source)
    key = session._view_key(view.unit, view.page_size)
    ticket = store.admit(session.owner, 'artefact.read', 'navigation-failure-test')
    result_id = store.publish(session.owner, ticket, source,
        {'command_id': 'artefact.read', 'stamp': None, 'default_view': key})
    session._save(result_id, key, view, None)
    adapter = ApplicationAdapter(current_application_catalogue(), current_request_resolver())
    return store, session, replace(context, result_delivery=session), adapter, result_id, source


@pytest.mark.parametrize('command,method,extra', [
    ('result.read', 'read_view_selection', {}),
    ('result.search', 'read_view_page', {'literal': 'hit'}),
])
@pytest.mark.parametrize('reason', ['capacity_exceeded', 'representation_too_large', 'storage_error'])
def test_navigation_failure_is_typed_bounded_and_keeps_source(
        retained, monkeypatch, command, method, extra, reason):
    from _command_interface.result_store import ResultStoreError
    from _command_interface.script import _guard_cli_result, _cli_result_fits
    from brain_mcp._command_adapter import _tool_result
    from brain_mcp._result_content import guard_tool_result, tool_result_fits

    store, session, context, adapter, result_id, source = retained
    secret = '/private/backend/path/credential-must-not-escape'
    def fail(*args, **kwargs):
        raise ResultStoreError(reason, secret)
    with monkeypatch.context() as fault:
        fault.setattr(store, method, fail)
        projection = adapter.invoke(context, command, {'result_id': result_id, **extra})
    envelope = projection.structured_content
    expected = reason if reason != 'storage_error' else 'result_unavailable'
    assert envelope['command'] == command and envelope['status'] == 'error'
    assert envelope['error']['code'] == 'capability_unavailable'
    assert envelope['error']['effects'] == 'none'
    assert envelope['delivery']['reason'] == expected
    assert envelope['delivery']['result_id'] == result_id
    assert secret not in str(envelope)
    assert 'Source outcome is unchanged' not in str(envelope)
    if reason == 'capacity_exceeded':
        assert envelope['delivery']['capacity_limits']['brain_bytes'] == store.limits.max_brain_bytes
        assert 'result.clear' in envelope['delivery']['message']
        assert 'detail=@outcome' in envelope['delivery']['message']
    elif reason == 'representation_too_large':
        assert 'detail=@outcome' in envelope['delivery']['message']
        assert 'Clearing retained output does not fix' in envelope['delivery']['message']
    cli, concise = _guard_cli_result(envelope, projection.concise_text)
    assert _cli_result_fits(cli, concise)
    wire = guard_tool_result(_tool_result(projection).model_dump(by_alias=True, exclude_none=True))
    assert tool_result_fits(wire)
    assert wire['structuredContent']['delivery']['result_id'] == result_id
    assert store.load(session.owner, result_id)['envelope'] == source


def test_real_resize_capacity_refusal_keeps_existing_view_and_recovery(retained):
    store, session, context, adapter, result_id, source = retained
    used = store._size(store._results_directory)
    store.limits = replace(store.limits, max_brain_bytes=used)
    store._accounting.brain_bytes = used
    reply = adapter.invoke(context, 'result.read',
        {'result_id': result_id, 'page_size': 1, 'anchor': 0}).structured_content
    assert reply['delivery']['reason'] == 'capacity_exceeded'
    assert reply['delivery']['result_id'] == result_id
    assert reply['delivery']['capacity_limits']['brain_bytes'] == used
    assert reply['error']['effects'] == 'none'
    assert store.load(session.owner, result_id)['envelope'] == source
    cached = adapter.invoke(context, 'result.read', {'result_id': result_id}).structured_content
    assert cached['status'] == 'ok'


def test_malformed_continuation_remains_a_conflict(retained):
    _, _, context, adapter, result_id, _ = retained
    reply = adapter.invoke(context, 'result.read',
        {'result_id': result_id, 'continuation': {'unexpected': True}}).structured_content
    assert reply['error']['code'] == 'conflict'
    assert reply['error']['effects'] == 'none'
    assert reply['delivery']['reason'] != 'capacity_exceeded'
