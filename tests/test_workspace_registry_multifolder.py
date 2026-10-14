"""Path-selected inventory and canonical workspace reads with multiple local folders."""

import json
from pathlib import Path

import pytest

import workspace_registry as registry
from _bootstrap.file_transaction import FilePlan, apply_file_changes
from _bootstrap import mcp_inventory
from _bootstrap.diagnostics import inspect_registry, RegistryCondition


def hub(vault, slug):
    path = vault / 'Workspaces' / f'{slug}.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'---\nkey: {slug}\nstatus: active\ntags: []\n---\n')


def test_shared_slug_has_zero_then_many_locations_without_arbitrary_resolution(tmp_path):
    vault = tmp_path / 'Brain'
    hub(vault, 'shared')
    assert registry.read_workspace_strict(vault, 'shared')['locations'] == []
    paths = [tmp_path / 'first', tmp_path / 'second']
    for path in paths:
        path.mkdir()
        registry.register_workspace(vault, 'shared', path)
    rows, original = registry.read_registry_strict(vault)
    assert set(rows) == {str(path) for path in paths}
    assert json.loads(original)['schema_version'] == 1
    record = registry.read_workspace_strict(vault, 'shared')
    assert [row['path'] for row in record['locations']] == [str(path) for path in paths]
    assert 'path' not in record
    with pytest.raises(ValueError, match='explicit folder selection'):
        registry.resolve_workspace_strict(vault, 'shared')
    assert registry.resolve_workspace_strict(vault, 'shared', folder=paths[1])['path'] == str(paths[1])
    registry.unregister_workspace(vault, paths[0], slug='shared')
    assert set(registry.load_registry_strict(vault)) == {str(paths[1])}


def test_embedded_location_coexists_and_requires_a_canonical_hub(tmp_path):
    vault = tmp_path / 'Brain'
    embedded = vault / '_Workspaces/shared'
    embedded.mkdir(parents=True)
    assert registry.list_workspaces_strict(vault) == []
    hub(vault, 'shared')
    outside = tmp_path / 'external'
    outside.mkdir()
    registry.register_workspace(vault, 'shared', outside)
    locations = registry.read_workspace_strict(vault, 'shared')['locations']
    assert {row['mode'] for row in locations} == {'embedded', 'linked'}
    registry.unregister_workspace(vault, outside)
    assert registry.read_workspace_strict(vault, 'shared')['locations'] == [
        {'slug': 'shared', 'path': str(embedded), 'mode': 'embedded'}]


def test_selected_read_checks_only_its_embedded_location(tmp_path, monkeypatch):
    vault = tmp_path / 'Brain'
    for slug in ('selected', 'unselected', 'empty'):
        hub(vault, slug)
    embedded = vault / '_Workspaces/selected'
    embedded.mkdir(parents=True)
    outside = tmp_path / 'outside'
    registry.register_workspace(vault, 'selected', embedded)
    registry.register_workspace(vault, 'selected', outside)
    registry.register_workspace(vault, 'unselected', tmp_path / 'elsewhere')
    checks = []
    isdir = registry.os.path.isdir

    def observed_isdir(path):
        if Path(path).parent == vault / '_Workspaces':
            checks.append(Path(path))
        return isdir(path)

    monkeypatch.setattr(registry.os.path, 'isdir', observed_isdir)
    selected = registry.read_workspace_strict(vault, 'selected')
    assert checks == [embedded]
    assert selected['locations'] == sorted([
        {'slug': 'selected', 'path': str(embedded), 'mode': 'embedded'},
        {'slug': 'selected', 'path': str(outside), 'mode': 'linked'},
    ], key=lambda row: row['path'])
    checks.clear()
    with pytest.raises(registry.UnknownWorkspaceError):
        registry.read_workspace_strict(vault, 'absent')
    assert checks == []


def test_list_traverses_registrations_once_and_preserves_zero_many_locations(tmp_path):
    vault = tmp_path / 'Brain'
    for slug in ('empty', 'shared', 'other'):
        hub(vault, slug)
    paths = [tmp_path / 'first', tmp_path / 'second']

    class CountedRows(dict):
        traversals = 0

        def values(self):
            self.traversals += 1
            return super().values()

    rows = CountedRows({str(path): {'slug': 'shared', 'path': str(path)} for path in paths})
    result = registry.list_workspaces(vault, registry=rows)
    assert rows.traversals == 1
    assert [row['slug'] for row in result] == ['empty', 'other', 'shared']
    assert result[0]['locations'] == result[1]['locations'] == []
    assert [row['path'] for row in result[2]['locations']] == [str(path) for path in paths]
    assert all(row['mode'] == 'linked' for row in result[2]['locations'])


def test_mcp_conversion_and_reverse_rows_preserve_every_shared_folder(tmp_path):
    vault = tmp_path / 'Brain'
    old = tmp_path / 'old'
    new = tmp_path / 'new'
    old.mkdir()
    new.mkdir()
    path = vault / registry.REGISTRY_REL
    path.parent.mkdir(parents=True)
    before = json.dumps({'workspaces': {'shared': {'path': str(old)}}}).encode()
    path.write_bytes(before)
    plan = FilePlan()
    registry.stage_canonical_rows(plan, vault)
    registry.stage_link_row(plan, vault, 'shared', new)
    assert path.read_bytes() == before
    plan.validate()
    apply_file_changes(plan.changes())
    assert mcp_inventory.workspace_paths(FilePlan(), vault) == {vault, old, new}
    rerun = FilePlan()
    registry.stage_canonical_rows(rerun, vault)
    registry.stage_link_row(rerun, vault, 'shared', new)
    assert not rerun.changes()
    with pytest.raises(ValueError, match='Conflicting MCP'):
        registry.stage_link_row(FilePlan(), vault, 'other', old)


def test_aliases_deduplicate_and_contradictory_aliases_refuse(tmp_path):
    folder = tmp_path / 'folder'
    folder.mkdir()
    alias = tmp_path / 'alias'
    alias.symlink_to(folder, target_is_directory=True)
    same = {'workspaces': {'shared': str(folder)}}
    assert set(registry.registry_rows(same)) == {str(folder)}
    with pytest.raises(ValueError, match='Conflicting workspace'):
        registry.registry_rows({'workspaces': {'shared': str(folder), 'other': str(alias)}})


@pytest.mark.parametrize('bad', [
    {'schema_version': 2, 'registrations': {}},
    {'schema_version': True, 'registrations': {}},
    {'schema_version': 1, 'registrations': {}, 'workspaces': {}},
    {'workspaces': {'shared': 'relative'}},
])
def test_invalid_or_future_inventory_is_never_silently_empty_or_migrated(tmp_path, bad):
    vault = tmp_path / 'Brain'
    path = vault / registry.REGISTRY_REL
    path.parent.mkdir(parents=True)
    before = json.dumps(bad).encode()
    path.write_bytes(before)
    with pytest.raises(ValueError):
        registry.load_registry(vault)
    plan = FilePlan()
    with pytest.raises(ValueError):
        registry.stage_canonical_rows(plan, vault)
    assert not plan.changes() and path.read_bytes() == before
    if 'schema_version' in bad and bad['schema_version'] != 1:
        assert inspect_registry(vault).condition is RegistryCondition.UNREADABLE


def test_cas_failure_preserves_other_writer_and_missing_folders_are_retained(tmp_path):
    vault = tmp_path / 'Brain'
    missing = tmp_path / 'missing'
    registry.register_workspace(vault, 'shared', missing)
    rows, before = registry.read_registry_strict(vault)
    registry.register_workspace(vault, 'shared', tmp_path / 'other')
    with pytest.raises(registry.RegistryChangedError):
        registry.replace_registry(vault, {}, expected=before)
    assert str(missing) in registry.load_registry_strict(vault)
    absent = []
    assert mcp_inventory.workspace_paths(FilePlan(), vault, unreachable=absent) == {vault}
    assert {row.path for row in absent} == {missing, tmp_path / 'other'}


def test_vault_self_is_not_a_registration(tmp_path):
    with pytest.raises(ValueError, match='Vault-self'):
        registry.register_workspace(tmp_path, 'shared', tmp_path)


def test_duplicate_json_claim_is_not_silently_overwritten(tmp_path):
    content = b'{"schema_version":1,"registrations":{},"registrations":{}}'
    with pytest.raises(ValueError, match='Duplicate workspace registry field'):
        registry.decode_registry(content)


def test_mcp_refuses_legacy_with_exact_upgrade_then_explicit_normalisation_allows_it(tmp_path, monkeypatch):
    import vault_registry
    from _bootstrap import mcp_registration, workspace_schema
    from _common._yaml import dump_mapping_text

    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(home / '.config'))
    vault = tmp_path / 'Brain with spaces'
    (vault / '.brain-core').mkdir(parents=True)
    (vault / '.brain-core/VERSION').write_text('0.71.4')
    vault_registry.register(vault, 'brain')
    folder = tmp_path / 'folder with spaces'
    manifest = folder / '.brain/local/workspace.yaml'
    manifest.parent.mkdir(parents=True)
    legacy = {'brain': 'brain', 'slug': 'local-old', 'links': {'workspace': 'shared'}}
    manifest.write_text(dump_mapping_text(legacy))
    before = manifest.read_bytes()
    plan = FilePlan()
    with pytest.raises(ValueError, match='workspace_upgrade_required') as error:
        mcp_registration.plan_reverse_registration(plan, vault, folder)
    assert "--vault '" in str(error.value) and "--workspace '" in str(error.value)
    assert not plan.changes() and manifest.read_bytes() == before
    # Explicit compatibility planner models the separate admitted upgrade boundary.
    manifest.write_text(dump_mapping_text(workspace_schema.plan_workspace_migration(legacy).payload))
    plan = FilePlan()
    mcp_registration.plan_reverse_registration(plan, vault, folder)
    plan.validate()
    apply_file_changes(plan.changes())
    assert registry.load_registry_strict(vault)[str(folder)]['slug'] == 'shared'


def test_mcp_reverse_registration_refuses_cross_brain_path_claims(tmp_path, monkeypatch):
    import vault_registry
    from _bootstrap import mcp_registration

    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(home / '.config'))
    brains = [tmp_path / 'firstBrain', tmp_path / 'secondBrain']
    for idx, brain in enumerate(brains):
        (brain / '.brain-core').mkdir(parents=True)
        (brain / '.brain-core/VERSION').write_text('0.71.4')
        vault_registry.register(brain, f'brain-{idx}')
    folder = tmp_path / 'folder'
    manifest = folder / '.brain/local/workspace.yaml'
    manifest.parent.mkdir(parents=True)
    manifest.write_text('schema_version: 1\nbrain: brain-0\nslug: shared\n')
    registry.register_workspace(brains[1], 'old', folder)
    plan = FilePlan()
    with pytest.raises(ValueError, match='Conflicting workspace registration'):
        mcp_registration.plan_reverse_registration(plan, brains[0], folder)
    assert not plan.changes()


def test_historical_raw_api_keeps_legacy_shape_and_refuses_current_rewrite(tmp_path):
    path = tmp_path / registry.REGISTRY_REL
    path.parent.mkdir(parents=True)
    legacy = {'workspaces': {'Pre Contract Key': '/missing'}}
    path.write_text(json.dumps(legacy))
    assert registry.load_raw_registry(tmp_path) == {'Pre Contract Key': {'path': '/missing'}}
    before = registry.registry_bytes({})
    path.write_bytes(before)
    with pytest.raises(RuntimeError, match='Historical workspace key migration'):
        registry.load_raw_registry(tmp_path)
    assert path.read_bytes() == before


def test_current_schema_requires_collection_and_mcp_detects_duplicate_claims(tmp_path):
    with pytest.raises(ValueError, match='requires registrations'):
        registry.registry_rows({'schema_version': 1})
    path = tmp_path / registry.REGISTRY_REL
    path.parent.mkdir(parents=True)
    path.write_bytes(b'{"workspaces":{},"workspaces":{}}')
    with pytest.raises(ValueError, match='Duplicate workspace registry field'):
        mcp_inventory.workspace_paths(FilePlan(), tmp_path)


def test_vault_self_stale_row_repair_keeps_other_rows_and_preserves_backup(tmp_path):
    from _portable.registry_maintenance import repair_registry

    vault = tmp_path / 'Brain'
    other = tmp_path / 'other'
    path = vault / registry.REGISTRY_REL
    path.parent.mkdir(parents=True)
    before = registry.registry_bytes({
        str(vault): {'slug': 'self', 'path': str(vault)},
        str(other): {'slug': 'shared', 'path': str(other)},
    })
    path.write_bytes(before)
    assert inspect_registry(vault).condition is RegistryCondition.INVALID_ROWS
    result = repair_registry(vault, dry_run=False)
    assert result.status == 'changed'
    assert registry.load_registry_strict(vault) == {str(other): {'slug': 'shared', 'path': str(other)}}
    assert path.with_name(path.name + '.bak').read_bytes() == before
