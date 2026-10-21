"""Consent grant/revocation composition does not require optional result storage."""
import pytest

from _command_interface.direct import DirectContextComposer
from _command_interface import result_store


@pytest.mark.parametrize('command', ['access.request', 'access.reduce'])
def test_nonretained_controls_can_compose_when_cache_is_unavailable(command_vault_clone, monkeypatch, command):
    def unavailable(*args, **kwargs):
        pytest.fail('non-retained consent control attempted to open result storage')
    monkeypatch.setattr(result_store, 'FileResultStore', unavailable)
    composer = DirectContextComposer(vault_root=command_vault_clone.vault_root)
    try:
        context = composer.compose(command_id=command)
        assert context.result_delivery is None
    finally:
        composer.close()
    assert not (command_vault_clone.vault_root / '.brain/local/results').exists()


def test_unrelated_context_error_is_not_reported_as_a_result_session_error():
    import asyncio
    from mcp.server import MCPServer
    from _application.registry import current_application_catalogue, current_request_resolver
    from brain_mcp._command_adapter import register_application_tools
    secret = 'private-context-value'
    def unrelated(**kwargs):
        raise ValueError(secret)
    server = MCPServer('unrelated-context-error-test')
    register_application_tools(
        server, catalogue=current_application_catalogue(),
        resolver=current_request_resolver(), context_factory=unrelated,
        invocation_guard=lambda: None,
    )
    result = asyncio.run(server.call_tool('session_start', {}))
    assert result.structured_content['error']['code'] == 'internal_error'
    assert secret not in str(result)
