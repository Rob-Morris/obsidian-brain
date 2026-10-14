"""The real migration runner orders retirement before the workspace replacement."""

import json
from pathlib import Path
import shutil

import pytest

from _common._yaml import dump_mapping_text, load_mapping_file
from _command_interface.authorisation_migration import capture_legacy_authorisation
from migrations.migrate_to_0_72_0 import patch_pre_compile, prospective_effects
from config import ConfigError
import upgrade


def write(root, relative, value):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_mapping_text(value), encoding='utf-8')


@pytest.mark.parametrize('source', ['0.62.5', '0.70.10', '0.71.4'])
def test_runner_cutover_and_current_rerun(tmp_path, source):
    scripts = Path(__file__).resolve().parents[1] / 'src/brain-core/scripts'
    shutil.copytree(scripts.parent, tmp_path / '.brain-core')
    shared = {'vault': {'profiles': {'restricted': {'allow':
              ['workspace.register', 'workspace.bind', 'workspace.setup', 'workspace.ensure-registration', 'workspace.read']}}},
              'defaults': {'access': {'overrides': {'workspace.setup': False, 'workspace.ensure-registration': True}}}}
    local = {'defaults': {'access': {'initial': {'mode': 'explicit', 'commands': ['workspace.setup']},
                                    'overrides': {'workspace.bind': True}}}}
    write(tmp_path, '.brain/config.yaml', shared)
    write(tmp_path, '.brain/local/config.yaml', local)
    before = capture_legacy_authorisation(tmp_path, source, {})
    prepared = []
    results, ledger = upgrade._run_migrations(str(tmp_path), source, '0.72.0',
        target='pre_compile_patch', context={'authorisation_before_upgrade': before},
        prepare=lambda effects: prepared.extend(effects or ()))
    migrated = load_mapping_file(tmp_path / '.brain/config.yaml')
    assert migrated['vault']['profiles']['restricted']['allow'] == ['workspace.register', 'workspace.create', 'workspace.read']
    assert migrated['defaults']['access']['overrides'] == {'workspace.register': False, 'workspace.create': True}
    assert load_mapping_file(tmp_path / '.brain/local/config.yaml')['defaults']['access'] == {
        'initial': {'mode': 'explicit', 'commands': ['workspace.register']}, 'overrides': {}}
    if source == '0.70.10':
        assert [entry['version'] for entry in results] == ['0.71.0', '0.72.0']
        assert '0.71.0' in ledger['migrations']
    assert set(prospective_effects(tmp_path)) <= set(prepared)
    original = (tmp_path / '.brain/config.yaml').read_bytes()
    # The later default phase cannot replay retirement; neither can a force rerun.
    remaining, ledger = upgrade._run_migrations(str(tmp_path), source, '0.72.0')
    assert not {'0.62.9', '0.68.0', '0.71.0', '0.72.0'} & {entry['version'] for entry in remaining}
    assert upgrade._run_migrations(str(tmp_path), '0.72.0', '0.72.0', target='pre_compile_patch')[0] == []
    assert (tmp_path / '.brain/config.yaml').read_bytes() == original
    assert json.loads((tmp_path / '.brain/local/migrations.json').read_text()) == ledger


def test_missing_evidence_and_conflict_refuse_before_writes(tmp_path):
    shared = {'defaults': {'access': {'overrides': {'workspace.setup': True}}}}
    local = {'defaults': {'access': {'overrides': {'workspace.ensure-registration': True, 'workspace.create': False}}}}
    write(tmp_path, '.brain/config.yaml', shared)
    write(tmp_path, '.brain/local/config.yaml', local)
    with pytest.raises(ConfigError, match='snapshot'):
        patch_pre_compile(tmp_path)
    before = capture_legacy_authorisation(tmp_path, '0.71.4', {})
    with pytest.raises(ConfigError, match='conflicting'):
        patch_pre_compile(tmp_path, context={'authorisation_before_upgrade': before})
    assert load_mapping_file(tmp_path / '.brain/config.yaml') == shared
    assert load_mapping_file(tmp_path / '.brain/local/config.yaml') == local


def test_recognised_builtin_uses_canonical_owner(tmp_path):
    from _application.registry import current_application_catalogue
    from _command_interface.profiles import builtin_profile_allow_lists
    template = {'vault': {'profiles': {'operator': {'allow': ['workspace.setup', 'workspace.read']}}}}
    write(tmp_path, '.brain/config.yaml', template)
    before = capture_legacy_authorisation(tmp_path, '0.71.4', template)
    patch_pre_compile(tmp_path, context={'authorisation_before_upgrade': before})
    allowed = load_mapping_file(tmp_path / '.brain/config.yaml')['vault']['profiles']['operator']['allow']
    assert allowed == list(builtin_profile_allow_lists(current_application_catalogue())['operator'])
    assert {'workspace.create', 'workspace.register', 'workspace.rebind'} <= set(allowed)


def test_active_loading_does_not_replay_historical_projection(tmp_path, monkeypatch):
    from _application.registry import current_application_catalogue
    from _command_interface.authorisation_config import resolve_authorisation_config
    from _command_interface.profiles import builtin_profile_allow_lists
    from _command_interface import profile_migration
    def historical_call(*args, **kwargs):
        raise AssertionError('active loading must not invoke historical migration')
    monkeypatch.setattr(profile_migration, 'migrate_profile_allow_lists', historical_call)
    monkeypatch.setattr(profile_migration, 'migrate_current_document_command_names', historical_call)
    catalogue = current_application_catalogue()
    allowed = list(builtin_profile_allow_lists(catalogue)['operator'])
    write(tmp_path, '.brain/config.yaml', {'vault': {'profiles': {'operator': {'allow': allowed}}}})
    before = (tmp_path / '.brain/config.yaml').read_bytes()
    resolved = resolve_authorisation_config(vault_root=tmp_path, catalogue=catalogue, operator_key=None)
    assert resolved.profile == 'operator'
    assert 'workspace.register' in allowed
    assert (tmp_path / '.brain/config.yaml').read_bytes() == before


@pytest.mark.parametrize('source_version', ['0.70.10', '0.71.4'])
def test_coordinated_upgrade_and_force_rerun(tmp_path, fake_home, monkeypatch, source_version):
    from test_upgrade_migrations import _make_source, _make_vault, _REAL_SCRIPTS
    from _bootstrap import mcp_transport
    monkeypatch.setattr(mcp_transport, 'delegate_machine_command', lambda *a, **k: {'status': 'ok', 'committed_effects': []})
    monkeypatch.setattr(upgrade, '_complete_runtime_readiness', lambda root: {'outcome': 'ok', 'message': 'ready'})
    monkeypatch.setattr(upgrade, '_inspect_runtime_orphans', lambda root: {'outcome': 'ok', 'orphan_candidates': 0, 'message': 'tidy'})
    migrations = {name: (_REAL_SCRIPTS / 'migrations' / name).read_text()
                  for name in ('migrate_to_0_71_0.py', 'migrate_to_0_72_0.py')}
    source = _make_source(tmp_path, '0.72.0', migrations=migrations)
    shutil.copytree(_REAL_SCRIPTS.parent / 'defaults', source / 'defaults')
    vault = _make_vault(tmp_path, source_version)
    raw = {'defaults': {'access': {'overrides': {'workspace.register': True, 'workspace.setup': False,
                                                'workspace.ensure-registration': True}}}}
    conflicting = {'defaults': {'access': {'overrides': {**raw['defaults']['access']['overrides'], 'workspace.create': False}}}}
    write(vault, '.brain/config.yaml', conflicting)
    unchanged = (vault / '.brain/config.yaml').read_bytes()
    refused = upgrade.upgrade(str(vault), str(source), sync=False, sync_deps=False)
    assert refused['status'] == 'error', refused
    assert 'conflicting' in refused['message']
    assert (vault / '.brain/config.yaml').read_bytes() == unchanged
    assert (vault / '.brain-core/VERSION').read_text().strip() == source_version
    write(vault, '.brain/config.yaml', raw)
    result = upgrade.upgrade(str(vault), str(source), sync=False, sync_deps=False)
    assert result['status'] == 'ok', result
    assert result['workspace_upgrade']['outcome'] == 'no_work'
    assert result['router_compile']['outcome'] != 'error'
    assert load_mapping_file(vault / '.brain/config.yaml')['defaults']['access']['overrides'] == {
        'workspace.register': False, 'workspace.create': True}
    initial = (vault / '.brain/config.yaml').read_bytes()
    ledger = (vault / '.brain/local/migrations.json').read_bytes()
    again = upgrade.upgrade(str(vault), str(source), force=True, sync=False, sync_deps=False)
    assert again['status'] == 'ok', again
    assert again['workspace_upgrade']['outcome'] == 'no_work'
    assert (vault / '.brain/config.yaml').read_bytes() == initial
    assert (vault / '.brain/local/migrations.json').read_bytes() == ledger
