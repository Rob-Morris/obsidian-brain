"""Current policy, exact replacement and private delegated authentication regressions."""

from copy import deepcopy
from enum import Enum
import os
from types import SimpleNamespace

import pytest

import config
from _common import hash_key, safe_write
from _common._yaml import dump_mapping_text
from _command_interface.authorisation_config import resolve_authorisation_config


class Class(str, Enum):
    CONTROL = 'control'
    OBSERVATION = 'observation'
    CONTENT = 'content'
    EXCEPTIONAL = 'exceptional'


CLASSES = {'access.request': Class.CONTROL, 'vault.read-file': Class.OBSERVATION,
           'artefact.create': Class.CONTENT, 'artefact.delete': Class.EXCEPTIONAL}
CATALOGUE = SimpleNamespace(entries=tuple(SimpleNamespace(command_id=key, initial_class=value)
                                         for key, value in CLASSES.items()))


@pytest.fixture
def brain(tmp_path):
    (tmp_path / '.brain/local').mkdir(parents=True)
    write(tmp_path, {'vault': {'profiles': {'operator': {'allow': list(CLASSES)},
                                         'narrow': {'allow': ['access.request', 'vault.read-file']}}}})
    return tmp_path


def write(root, value, local=False):
    (root / ('.brain/local/config.yaml' if local else '.brain/config.yaml')).write_text(dump_mapping_text(value))


def resolve(root, key=None, binding=None):
    return resolve_authorisation_config(vault_root=root, catalogue=CATALOGUE, operator_key=key, delegated_binding=binding)


def test_normal_and_readonly_have_different_initial_authorisation_not_permissions(brain):
    normal = resolve(brain)
    assert normal.principal == 'default'
    assert normal.policy.initial == {'vault.read-file', 'artefact.create'}
    assert 'artefact.delete' in normal.policy.permissions
    write(brain, {'defaults': {'access': {'initial': {'mode': 'read-only'}}}}, local=True)
    readonly = resolve(brain)
    assert readonly.policy.initial == {'vault.read-file'}
    assert readonly.policy.permissions == normal.policy.permissions


def test_replacement_clears_explicit_commands_empty_override_and_false(brain):
    shared = config._read_yaml(str(brain / '.brain/config.yaml'))
    shared['defaults'] = {'access': {'initial': {'mode': 'explicit', 'commands': ['artefact.delete']},
                                   'overrides': {'artefact.create': True}}}
    write(brain, shared)
    write(brain, {'defaults': {'access': {'initial': {'mode': 'read-only'}, 'overrides': {}}}}, local=True)
    assert resolve(brain).policy.initial == {'vault.read-file'}
    write(brain, {'defaults': {'access': {'initial': {'mode': 'explicit', 'commands': []},
                                        'overrides': {'artefact.create': False}}}}, local=True)
    assert not resolve(brain).policy.initial


@pytest.mark.parametrize('initial,overrides', [
    ({'mode': 'read-only', 'commands': []}, {}), ({'mode': 'explicit'}, {}),
    ({'mode': 'explicit', 'commands': ['invented.command']}, {}),
    ({'mode': 'normal'}, {'artefact.create': 1}),
])
def test_invalid_initial_settings_do_not_guess_access(brain, initial, overrides):
    write(brain, {'defaults': {'access': {'initial': initial, 'overrides': overrides}}}, local=True)
    with pytest.raises(config.ConfigError):
        resolve(brain)


def test_restored_bytes_and_mtime_never_resurrect_generation(brain):
    path = brain / '.brain/config.yaml'
    before = path.read_bytes()
    stat = path.stat()
    first = resolve(brain)
    reduced = config._read_yaml(str(path))
    reduced['vault']['profiles']['operator']['allow'] = ['access.request', 'vault.read-file']
    write(brain, reduced)
    path.write_bytes(before)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    restored = resolve(brain)
    assert restored.policy == first.policy
    assert restored.permission_generation != first.permission_generation


def test_routine_cache_replacement_with_absent_local_config_preserves_generation(brain):
    first = resolve(brain)
    for i in range(3):
        safe_write(brain / '.brain/local/compiled-router.json', str(i), bounds=brain)
        assert resolve(brain).permission_generation == first.permission_generation


def test_migration_marker_denies_exceptional_but_preserves_initial_and_is_explicitly_resolved(brain):
    raw = config._read_yaml(str(brain / '.brain/config.yaml'))
    raw['vault']['access'] = {'request_policy': 'migration_required'}
    write(brain, raw)
    found = resolve(brain)
    assert found.policy.request_policy == 'migration_required'
    assert found.policy.initial == {'vault.read-file', 'artefact.create'}
    for policy in ('allowed', 'denied'):
        raw['vault']['access']['request_policy'] = policy
        write(brain, raw)
        found = resolve(brain)
        assert found.policy.request_policy == policy
        assert not found.diagnostics


def test_delegation_rechecks_profile_without_key_and_rejects_changed_registration(brain):
    raw = config._read_yaml(str(brain / '.brain/config.yaml'))
    raw['vault']['operators'] = [
        {'id': 'rob', 'profile': 'operator', 'auth': {'type': 'key', 'hash': hash_key('one')}},
        {'id': 'other', 'profile': 'operator', 'auth': {'type': 'key', 'hash': hash_key('two')}},
    ]
    write(brain, raw)
    initial = resolve(brain, 'one')
    binding = initial.authentication_binding
    assert resolve(brain, binding=binding).principal == 'operator:rob'
    assert resolve(brain, 'one', binding).principal == 'operator:rob'
    for key in ('invalid', 'two'):
        with pytest.raises(ValueError) as caught:
            resolve(brain, key, binding)
        assert not isinstance(caught.value, config.OperatorBindingRevoked)
    raw['vault']['operators'][0]['profile'] = 'narrow'
    write(brain, raw)
    reduced = resolve(brain, binding=binding)
    assert reduced.principal == initial.principal
    assert reduced.profile == 'narrow'
    raw['vault']['operators'][0]['auth']['hash'] = hash_key('rotated')
    write(brain, raw)
    with pytest.raises(config.OperatorBindingRevoked):
        resolve(brain, binding=binding)


def test_default_binding_survives_default_profile_change(brain):
    binding = resolve(brain).authentication_binding
    write(brain, {'defaults': {'default_profile': 'narrow'}}, local=True)
    current = resolve(brain, binding=binding)
    assert current.principal == 'default'
    assert current.profile == 'narrow'


def test_duplicate_registered_identity_is_not_authenticated_by_list_order(brain):
    raw = {'vault': {'operators': [{'id': 'rob', 'auth': {'type': 'key', 'hash': hash_key('one')}}]}}
    profile, binding = config.authenticate_operator_binding('one', raw)
    duplicated = deepcopy(raw)
    duplicated['vault']['operators'].append(deepcopy(duplicated['vault']['operators'][0]))
    with pytest.raises(ValueError):
        config.authenticate_operator_binding('one', duplicated)
    with pytest.raises(config.OperatorBindingRevoked):
        config.resolve_operator_binding(binding, duplicated)


def test_malformed_registration_is_transient_not_terminal_revocation():
    raw = {'vault': {'operators': [{'id': 'rob', 'auth': {'type': 'key', 'hash': hash_key('one')}}]}}
    _, binding = config.authenticate_operator_binding('one', raw)
    raw['vault']['operators'][0]['auth'] = None
    with pytest.raises(config.ConfigError) as caught:
        config.resolve_operator_binding(binding, raw)
    assert not isinstance(caught.value, config.OperatorBindingRevoked)
    raw['vault']['operators'] = []
    with pytest.raises(config.OperatorBindingRevoked):
        config.resolve_operator_binding(binding, raw)


def test_inspection_provenance_tracks_whole_setting_replacement(brain):
    raw = config._read_yaml(str(brain / '.brain/config.yaml'))
    raw['vault']['access'] = {'request_policy': 'denied'}
    raw['defaults'] = {'access': {'initial': {'mode': 'read-only'}, 'overrides': {'artefact.create': True}}}
    write(brain, raw)
    write(brain, {'defaults': {'access': {'overrides': {}}}}, local=True)
    resolved = resolve(brain)
    assert (resolved.initial_source, resolved.overrides_source, resolved.request_policy_source) == ('shared', 'local', 'shared')
    assert resolved.config['defaults']['access']['overrides'] == {}
