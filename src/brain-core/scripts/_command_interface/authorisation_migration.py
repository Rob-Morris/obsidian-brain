"""One-time authorisation conversion from captured pre-upgrade source semantics."""

from copy import deepcopy
from dataclasses import dataclass
import hashlib
from pathlib import Path

from _common._yaml import load_mapping_file, load_mapping_text, dump_mapping_text
from config import ConfigError, _merge_config


REPORT_PATH = '.brain/local/authorisation-migration.json'
_LEGACY_VAULT = {'elevation_policy', 'default_lease_seconds', 'max_lease_seconds', 'pending_seconds', 'max_use_count'}
_LEGACY_INITIAL = {'initial_profile', 'initial_commands'}


def _read(path):
    try:
        return load_mapping_file(path)
    except FileNotFoundError:
        return {}


def _revision(path):
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def capture_legacy_authorisation(vault_root, old_version):
    """Capture only security settings and old shipped profiles before replacing Core."""
    root = Path(vault_root)
    paths = (root / '.brain-core/defaults/config.yaml', root / '.brain/config.yaml', root / '.brain/local/config.yaml')
    layers, revisions = [], []
    for path in paths:
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            data = None
        raw = load_mapping_text(data.decode('utf-8')) if data is not None else {}
        revisions.append(hashlib.sha256(data).hexdigest() if data is not None else None)
        vault, defaults = raw.get('vault', {}), raw.get('defaults', {})
        if not isinstance(vault, dict) or not isinstance(defaults, dict):
            raise ConfigError('existing authorisation configuration zones must be mappings')
        layers.append({'vault': {key: deepcopy(vault[key]) for key in ('profiles', 'access') if key in vault},
                       'defaults': {key: deepcopy(defaults[key]) for key in ('access', 'default_profile') if key in defaults}})
    return {'schema': 'brain.authorisation-before-upgrade/1', 'version': old_version,
            'layers': layers, 'authored_revisions': revisions[1:]}


@dataclass(frozen=True, slots=True)
class AuthorisationMigrationPlan:
    writes: tuple[tuple[str, str], ...]
    report: dict


def _control_conflicts(template, shared, local):
    # Classification remains catalogue-owned; migration does not maintain a
    # second list of protocol facilities or widen custom permission profiles.
    from _application.registry import current_application_catalogue
    controls = {entry.command_id for entry in current_application_catalogue().entries
                if entry.initial_class.value == 'control'}
    conflicts = []
    effective = _merge_config(template, shared, local)
    for name, authored in shared.get('vault', {}).get('profiles', {}).items():
        definition = effective.get('vault', {}).get('profiles', {}).get(name)
        allowed = definition.get('allow') if isinstance(definition, dict) else None
        if not isinstance(allowed, list) or any(not isinstance(value, str) for value in allowed):
            raise ConfigError(f'shared permission profile {name!r} has an invalid command set')
        if missing := controls - set(allowed):
            source = 'shared' if isinstance(authored, dict) and 'allow' in authored else 'template'
            conflicts.append({'code': 'control_conflict', 'source': source,
                'setting': 'vault.profiles.' + name, 'commands': sorted(missing),
                'message': 'Custom permission maximum omits required controls; choose an explicit configuration change.'})
    overrides = effective.get('defaults', {}).get('access', {}).get('overrides', {})
    if isinstance(overrides, dict) and (misplaced := controls & set(overrides)):
        source = next((label for label, layer in (('local', local), ('shared', shared), ('template', template))
                       if 'overrides' in layer.get('defaults', {}).get('access', {})), 'template')
        conflicts.append({'code': 'control_conflict', 'source': source,
            'setting': 'defaults.access.overrides', 'commands': sorted(misplaced),
            'message': 'Controls cannot be ordinary initial overrides; remove these entries explicitly.'})
    if effective.get('vault', {}).get('access', {}).get('request_policy') == 'migration_required':
        conflicts.append({'code': 'migration_required', 'source': 'shared',
            'setting': 'vault.access.request_policy', 'commands': [],
            'message': 'Former external approval requires an explicit allowed or denied request policy.'})
    return conflicts


def _current_plan(template, current):
    conflicts = _control_conflicts(template, *current)
    return AuthorisationMigrationPlan((), {'schema': 'brain.authorisation-migration/1',
        'status': 'review-required' if conflicts else 'already-current',
        'changes': [], 'conflicts': conflicts})


def plan_authorisation_migration(vault_root, before, *, new_template=None) -> AuthorisationMigrationPlan:
    """Preserve stored initial selections and custom maxima without importing old leases."""
    root = Path(vault_root)
    current = [_read(root / path) for path in ('.brain/config.yaml', '.brain/local/config.yaml')]
    template = new_template if new_template is not None else _read(root / '.brain-core/defaults/config.yaml')
    has_legacy = any(_LEGACY_INITIAL & set(raw.get('defaults', {}).get('access', {})) for raw in current)
    # Machine-local vault fields never participate in effective configuration.
    has_legacy = has_legacy or bool(_LEGACY_VAULT & set(current[0].get('vault', {}).get('access', {})))
    if before is None:
        if has_legacy:
            raise ConfigError('legacy access conversion requires a pre-upgrade configuration snapshot; rerun the coordinated upgrade')
        return _current_plan(template, current)
    if before.get('schema') != 'brain.authorisation-before-upgrade/1':
        raise ConfigError('authorisation migration snapshot has an invalid schema')
    if [_revision(root / path) for path in ('.brain/config.yaml', '.brain/local/config.yaml')] != before['authored_revisions']:
        # A previous successful conversion is safe to verify on a forced rerun;
        # an unexplained legacy change cannot borrow the earlier profile meaning.
        if not has_legacy:
            return _current_plan(template, current)
        raise ConfigError('authored access configuration changed after the upgrade snapshot')
    old_template, old_shared, old_local = before['layers']
    old_effective = _merge_config(old_template, old_shared, old_local)
    old_profiles = old_effective.get('vault', {}).get('profiles', {})
    new_profiles = template.get('vault', {}).get('profiles', {})
    changes, writes, updated_layers = [], [], []
    for label, path, original in zip(('shared', 'local'), ('.brain/config.yaml', '.brain/local/config.yaml'), current):
        updated = deepcopy(original)
        access = updated.get('defaults', {}).get('access', {})
        legacy_keys = _LEGACY_INITIAL & set(access)
        if legacy_keys:
            if 'initial_commands' in access:
                raise ConfigError(f'{label} initial_commands had no supported predecessor meaning; remove it or choose the new initial selection explicitly')
            if 'initial' in access or len(legacy_keys) != 1:
                raise ConfigError(f'{label} access has conflicting old/new initial selections; resolve explicitly')
            name = access['initial_profile']
            definition = old_profiles.get(name)
            if not isinstance(definition, dict) or not isinstance(definition.get('allow'), list):
                raise ConfigError(f'{label} initial profile has no proven old command set')
            commands = definition['allow']
            if not isinstance(commands, list) or any(not isinstance(item, str) for item in commands):
                raise ConfigError(f'{label} legacy initial command set is invalid')
            for key in legacy_keys:
                access.pop(key)
            access['initial'] = {'mode': 'explicit', 'commands': sorted(set(commands))}
            changes.append({'source': label, 'setting': 'defaults.access.initial', 'strategy': 'preserved-exact'})
        if label == 'shared':
            policy = updated.get('vault', {}).get('access', {})
            legacy = _LEGACY_VAULT & set(policy)
            if legacy:
                if 'request_policy' in policy:
                    raise ConfigError('shared access has both legacy and new request policies; resolve explicitly')
                old_policy = policy.get('elevation_policy', old_effective.get('vault', {}).get('access', {}).get('elevation_policy', 'automatic'))
                mapped = {'automatic': 'allowed', 'denied': 'denied', 'external': 'migration_required'}.get(old_policy)
                if mapped is None:
                    raise ConfigError('legacy request policy is unknown')
                for key in legacy:
                    policy.pop(key)
                policy['request_policy'] = mapped
                changes.append({'source': label, 'setting': 'vault.access.request_policy', 'value': mapped})
            profiles = updated.get('vault', {}).get('profiles', {})
            for name, definition in list(profiles.items()):
                old_builtin = old_template.get('vault', {}).get('profiles', {}).get(name)
                if old_builtin is not None and definition == old_builtin and name in new_profiles:
                    profiles[name] = deepcopy(new_profiles[name])
                    if definition != profiles[name]:
                        changes.append({'source': label, 'setting': 'vault.profiles.' + name, 'strategy': 'recognised-shipped'})
        if updated != original:
            writes.append((path, dump_mapping_text(updated)))
        updated_layers.append(updated)
    conflicts = _control_conflicts(template, *updated_layers)
    report = {'schema': 'brain.authorisation-migration/1', 'status': 'review-required' if conflicts else 'converted',
              'source_version': before['version'], 'changes': changes,
              'conflicts': conflicts,
              'authored_source_revisions': before['authored_revisions'],
              'historical_grants': 'retained as history only; never imported'}
    return AuthorisationMigrationPlan(tuple(writes), report)
