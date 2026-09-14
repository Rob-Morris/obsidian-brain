"""Configuration inspection reports effective access sources without credential material."""
from dataclasses import asdict, replace
import json
from types import SimpleNamespace

from _application.access_contracts import (
    AccessConfiguration, AccessConfigurationDiagnostic, InitialAuthorisationOverride,
)
from _application._response_budget import encoded_result_size
from _application.projection import canonical_result_envelope
from _application.vault.read_config import execute, VaultReadConfigRequest, VaultConfigPage


def configuration():
    return AccessConfiguration(initial_mode='read-only', initial_commands=None,
        initial_source='local', overrides=(InitialAuthorisationOverride('artefact.create', True),),
        overrides_source='shared', request_policy='allowed', effective_request_policy='migration_required', request_policy_source='template',
        diagnostics=(AccessConfigurationDiagnostic('control_conflict', 'defaults.access.overrides',
            'shared', 'access.request is a control; remove its ordinary initial override'),),
        revision='sha256:' + 'a' * 64)


def context(tmp_path, monkeypatch, value):
    import config
    monkeypatch.setattr(config, 'load_config', lambda root: {
        'vault': {'profiles': {'operator': {'allow': []}},
                  'operators': [{'id': 'private-registration', 'auth': {'hash': 'private-key-hash'}}]},
        'defaults': {'default_profile': 'operator', 'exclude': {'artefact_sync': []}},
    })
    return SimpleNamespace(selected_brain=SimpleNamespace(vault_root=tmp_path),
                           access=SimpleNamespace(configuration=lambda: value))


def test_effective_setting_sources_and_actionable_conflicts_are_public(tmp_path, monkeypatch):
    value = configuration()
    result = execute(context(tmp_path, monkeypatch, value), VaultReadConfigRequest())
    assert result.result.access == value
    envelope = canonical_result_envelope(result)
    assert envelope['result']['access']['initial_source'] == 'local'
    assert envelope['result']['access']['request_policy'] == 'allowed'
    assert envelope['result']['access']['request_policy_source'] == 'template'
    assert envelope['result']['access']['effective_request_policy'] == 'migration_required'
    assert envelope['result']['access']['diagnostics'][0]['source'] == 'shared'
    encoded = json.dumps(envelope)
    assert 'private-registration' not in encoded
    assert 'private-key-hash' not in encoded
    assert encoded_result_size(result) < 16000


def test_large_configuration_inspection_pages_complete_json_without_truncation(tmp_path, monkeypatch):
    value = replace(configuration(), diagnostics=(AccessConfigurationDiagnostic(
        'control_conflict', 'vault.profiles', 'shared', 'Large configuration "é\\' * 2500),))
    ctx = context(tmp_path, monkeypatch, value)
    cursor, content = None, []
    while True:
        result = execute(ctx, VaultReadConfigRequest(cursor=cursor))
        assert isinstance(result.result, VaultConfigPage)
        assert encoded_result_size(result) < 16000
        content.append(result.result.content)
        cursor = result.result.range.next_cursor
        if cursor is None:
            break
    reconstructed = json.loads(''.join(content))
    assert reconstructed['access']['diagnostics'][0]['message'] == value.diagnostics[0].message
    assert len(content) > 1


def test_configuration_change_invalidates_existing_continuation(tmp_path, monkeypatch):
    value = replace(configuration(), diagnostics=(AccessConfigurationDiagnostic(
        'control_conflict', 'vault.profiles', 'shared', 'long' * 10000),))
    first = execute(context(tmp_path, monkeypatch, value), VaultReadConfigRequest())
    changed = replace(value, effective_request_policy='allowed', revision='sha256:' + 'b' * 64)
    result = execute(context(tmp_path, monkeypatch, changed), VaultReadConfigRequest(cursor=first.result.range.next_cursor))
    assert result.status == 'error'
    assert result.error.code.value == 'conflict'
