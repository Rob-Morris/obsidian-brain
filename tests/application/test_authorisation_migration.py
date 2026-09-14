"""Migration retains authored layer meaning rather than inheriting new defaults."""

from copy import deepcopy
import json

import pytest

from _common._yaml import dump_mapping_text, load_mapping_file
from config import ConfigError
from _command_interface.authorisation_migration import capture_legacy_authorisation, plan_authorisation_migration
from migrations.migrate_to_0_68_0 import patch_pre_compile, migrate, prospective_effects


OLD = {'vault': {'access': {'elevation_policy': 'automatic', 'default_lease_seconds': 900},
                 'profiles': {'reader': {'allow': ['access.request', 'vault.read-file']},
                              'operator': {'allow': ['access.request', 'vault.read-file', 'artefact.create']}}},
       'defaults': {'access': {'initial_profile': 'reader'}}}
NEW = {'vault': {'access': {'request_policy': 'allowed'},
                 'profiles': {'reader': {'allow': ['access.prepare', 'access.request', 'vault.read-file']},
                              'operator': {'allow': ['access.prepare', 'access.request', 'vault.read-file', 'artefact.create']}}},
       'defaults': {'access': {'initial': {'mode': 'normal'}, 'overrides': {}}}}


@pytest.fixture
def root(tmp_path):
    for path in ('.brain-core/defaults', '.brain/local'):
        (tmp_path / path).mkdir(parents=True)
    write(tmp_path, '.brain-core/defaults/config.yaml', OLD)
    return tmp_path


def write(root, path, value):
    (root / path).write_text(dump_mapping_text(value))


def cutover(root):
    before = capture_legacy_authorisation(root, '0.67.3')
    write(root, '.brain-core/defaults/config.yaml', NEW)
    return before


def test_absence_of_stored_initial_keeps_new_normal_and_does_not_materialise_local_settings(root):
    write(root, '.brain/config.yaml', {'vault': {'profiles': {'custom': {'allow': ['vault.read-file']}}}})
    plan = plan_authorisation_migration(root, cutover(root))
    assert plan.writes == ()
    assert not (root / '.brain/local/config.yaml').exists()


def test_explicit_reader_is_preserved_from_old_template_in_each_authored_layer(root):
    for path in ('.brain/config.yaml', '.brain/local/config.yaml'):
        write(root, path, {'defaults': {'access': {'initial_profile': 'reader'}}})
    before = cutover(root)
    result = patch_pre_compile(root, context={'authorisation_before_upgrade': before})
    assert result['status'] == 'ok'
    for path in ('.brain/config.yaml', '.brain/local/config.yaml'):
        access = load_mapping_file(root / path)['defaults']['access']
        assert access == {'initial': {'mode': 'explicit', 'commands': ['access.request', 'vault.read-file']}}
    assert migrate(root) == {'status': 'skipped'}
    assert patch_pre_compile(root, context={'authorisation_before_upgrade': before})['status'] == 'skipped'


def test_shipped_profile_gains_new_controls_but_custom_profile_is_not_widened(root):
    profiles = deepcopy(OLD['vault']['profiles'])
    profiles['custom'] = {'allow': ['vault.read-file']}
    write(root, '.brain/config.yaml', {'vault': {'profiles': profiles}})
    plan = plan_authorisation_migration(root, cutover(root))
    from _common._yaml import load_mapping_text
    migrated = load_mapping_text(dict(plan.writes)['.brain/config.yaml'])
    assert migrated['vault']['profiles']['reader'] == NEW['vault']['profiles']['reader']
    assert migrated['vault']['profiles']['custom'] == {'allow': ['vault.read-file']}


@pytest.mark.parametrize('legacy,expected', [('automatic', 'allowed'), ('denied', 'denied'), ('external', 'migration_required')])
def test_policy_conversion_has_no_approver_or_timer_runtime(root, legacy, expected):
    write(root, '.brain/config.yaml', {'vault': {'access': {'elevation_policy': legacy, 'default_lease_seconds': 90}},
                                      'defaults': {'flags': {'custom': True}}})
    before = cutover(root)
    history = root / '.brain/local/access-state.json'
    history.write_text('{"audit":"historic"}')
    patch_pre_compile(root, context={'authorisation_before_upgrade': before})
    raw = load_mapping_file(root / '.brain/config.yaml')
    assert raw['vault']['access'] == {'request_policy': expected}
    assert raw['defaults']['flags']['custom'] is True
    assert json.loads(history.read_text()) == {'audit': 'historic'}
    assert len(prospective_effects(root)) == 3


def test_mixed_initial_fields_fail_before_either_layer_is_written(root):
    raw = {'defaults': {'access': {'initial_profile': 'reader', 'initial': {'mode': 'normal'}}}}
    write(root, '.brain/local/config.yaml', raw)
    before = cutover(root)
    with pytest.raises(ConfigError, match='conflicting'):
        patch_pre_compile(root, context={'authorisation_before_upgrade': before})
    assert load_mapping_file(root / '.brain/local/config.yaml') == raw


def test_legacy_without_old_evidence_does_not_resolve_reader_through_new_defaults(root):
    write(root, '.brain/config.yaml', {'defaults': {'access': {'initial_profile': 'reader'}}})
    cutover(root)
    with pytest.raises(ConfigError, match='snapshot'):
        migrate(root)


def test_changed_authored_config_after_snapshot_refuses_old_meaning(root):
    write(root, '.brain/config.yaml', {'defaults': {'access': {'initial_profile': 'reader'}}})
    before = cutover(root)
    write(root, '.brain/config.yaml', {'defaults': {'access': {'initial_profile': 'operator'}}})
    with pytest.raises(ConfigError, match='changed'):
        patch_pre_compile(root, context={'authorisation_before_upgrade': before})


def test_ignored_predecessor_initial_commands_never_gain_authorisation(root):
    raw = {'defaults': {'access': {'initial_commands': ['artefact.delete']}}}
    write(root, '.brain/config.yaml', raw)
    before = cutover(root)
    with pytest.raises(ConfigError, match='no supported predecessor meaning'):
        patch_pre_compile(root, context={'authorisation_before_upgrade': before})
    assert load_mapping_file(root / '.brain/config.yaml') == raw


def test_custom_control_conflicts_publish_review_report_without_rewriting_configuration(root):
    raw = {'vault': {'profiles': {'custom': {'allow': ['vault.read-file']}}}}
    write(root, '.brain/config.yaml', raw)
    before = cutover(root)
    original = (root / '.brain/config.yaml').read_bytes()
    result = patch_pre_compile(root, context={'authorisation_before_upgrade': before})
    assert result['configuration_status'] == 'review-required'
    assert result['conflicts'][0]['setting'] == 'vault.profiles.custom'
    assert result['conflicts'][0]['source'] == 'shared'
    assert 'access.prepare' in result['conflicts'][0]['commands']
    report = json.loads((root / '.brain/local/authorisation-migration.json').read_text())
    assert report['status'] == 'review-required'
    assert report['changes'] == []
    assert (root / '.brain/config.yaml').read_bytes() == original


def test_metadata_only_profile_override_uses_inherited_current_permissions(root):
    from _application.registry import current_application_catalogue
    controls = sorted(entry.command_id for entry in current_application_catalogue().entries
                      if entry.initial_class.value == 'control')
    new = deepcopy(NEW)
    new['vault']['profiles']['reader']['allow'] = [*controls, 'vault.read-file']
    raw = {'vault': {'profiles': {'reader': {'label': 'Read-only agents'}}}}
    write(root, '.brain/config.yaml', raw)
    before = capture_legacy_authorisation(root, '0.67.3')
    plan = plan_authorisation_migration(root, before, new_template=new)
    assert plan.writes == ()
    assert plan.report['conflicts'] == []
