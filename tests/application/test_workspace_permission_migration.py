"""Workspace cutover preserves authored authority and layer selection."""

from copy import deepcopy

import pytest

from config import ConfigError
from _command_interface.profile_migration import RETIRED_COMMANDS
from _command_interface.workspace_permission_migration import project_workspace_permissions


def project(shared, local=None, source='0.71.4'):
    return project_workspace_permissions(shared, local or {}, source_version=source, cutover_version='0.72.0')


@pytest.mark.parametrize('source', ['0.62.5', '0.70.10', '0.71.4'])
def test_drop_historical_before_replacements_and_preserve_order(source):
    commands = ['workspace.register', 'workspace.read', 'workspace.ensure-registration',
                'workspace.bind', 'workspace.setup', 'workspace.read']
    shared = {'vault': {'profiles': {'restricted': {'allow': commands, 'label': 'Restricted'}}},
              'defaults': {'access': {'initial': {'mode': 'explicit', 'commands': commands},
                                      'overrides': {'workspace.register': False, 'workspace.bind': True,
                                                    'workspace.setup': True, 'workspace.ensure-registration': False}}}}
    before = deepcopy(shared)
    updated, local = project(shared, source=source)
    expected = ['workspace.read', 'workspace.create', 'workspace.register']
    assert updated['vault']['profiles']['restricted'] == {'allow': expected, 'label': 'Restricted'}
    assert updated['defaults']['access'] == {'initial': {'mode': 'explicit', 'commands': expected},
                                           'overrides': {'workspace.register': True, 'workspace.create': False}}
    assert local == {}
    assert shared == before
    assert not set(expected) & {'workspace.rebind', 'workspace.upgrade', 'workspace.discover'}


def test_local_empty_override_blocks_shared_without_promoting_local_profiles():
    shared = {'defaults': {'access': {'overrides': {'workspace.setup': True}}}}
    local = {'vault': {'profiles': {'ignored': {'allow': ['workspace.setup']}}},
             'defaults': {'access': {'initial': {'mode': 'normal'},
                                     'overrides': {'workspace.register': True, 'workspace.bind': False}}}}
    updated, projected_local = project(shared, local)
    assert updated['defaults']['access']['overrides'] == {'workspace.register': True}
    assert projected_local['defaults']['access'] == {'initial': {'mode': 'normal'}, 'overrides': {}}
    assert projected_local['vault'] == local['vault']


@pytest.mark.parametrize('source', ['0.72.0', '0.72.1'])
def test_current_rerun_does_not_strip_new_register(source):
    shared = {'vault': {'profiles': {'custom': {'allow': ['workspace.register']}}},
              'defaults': {'access': {'overrides': {'workspace.register': False}}}}
    assert project(shared, source=source) == (shared, {})
    assert RETIRED_COMMANDS == frozenset({'workspace.bind', 'workspace.register'})


@pytest.mark.parametrize('access', [
    {'overrides': {'workspace.ensure-registration': True, 'workspace.create': False}},
])
def test_conflicting_target_refuses_without_mutating_either_layer(access):
    shared = {'defaults': {'access': {'overrides': {'workspace.setup': True}}}}
    local = {'defaults': {'access': access}}
    originals = deepcopy((shared, local))
    with pytest.raises(ConfigError, match='conflicting'):
        project(shared, local)
    assert (shared, local) == originals


def test_equal_target_override_deduplicates_and_retains_exact_boolean():
    raw = {'defaults': {'access': {'overrides': {'workspace.ensure-registration': False,
                                              'workspace.create': False}}}}
    assert project(raw)[0]['defaults']['access']['overrides'] == {'workspace.create': False}


def test_compatible_target_list_deduplicates_in_first_occurrence_order():
    raw = {'defaults': {'access': {'initial': {'mode': 'explicit', 'commands':
           ['workspace.create', 'workspace.read', 'workspace.ensure-registration']}}}}
    assert project(raw)[0]['defaults']['access']['initial']['commands'] == ['workspace.create', 'workspace.read']


@pytest.mark.parametrize('raw', [
    {'defaults': {'access': {'overrides': {'workspace.setup': 'normal'}}}},
    {'vault': {'profiles': {'custom': {'allow': ['workspace.setup', 1]}}}},
    {'defaults': {'access': []}},
])
def test_malformed_configuration_refuses(raw):
    with pytest.raises(ConfigError):
        project(raw)


@pytest.mark.parametrize('source', [None, 'unknown', '0.71'])
def test_unknown_source_refuses_instead_of_reinterpreting_grants(source):
    with pytest.raises(ConfigError, match='exact source'):
        project({}, source=source)
