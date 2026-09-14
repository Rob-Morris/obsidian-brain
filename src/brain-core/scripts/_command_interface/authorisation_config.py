"""Resolve current authorisation from one stable, provenance-bearing config snapshot."""

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path

import config as brain_config
from _common._yaml import load_mapping_text
from _application.consent import ConsentPolicy


@dataclass(frozen=True, slots=True)
class AuthorisationDiagnostic:
    code: str
    setting: str
    source: str
    message: str


@dataclass(frozen=True, slots=True)
class AuthorisationSources:
    config: dict = field(repr=False)
    layers: tuple[dict, dict, dict] = field(repr=False)
    paths: tuple[Path, ...]
    revision: str
    signature: tuple


@dataclass(frozen=True, slots=True)
class ResolvedAuthorisation:
    config: dict = field(repr=False)
    profile: str
    operator_id: str | None
    principal: str
    policy: ConsentPolicy
    revision: str
    permission_generation: str
    diagnostics: tuple[AuthorisationDiagnostic, ...]
    authentication_binding: brain_config.OperatorAuthenticationBinding = field(repr=False)
    initial_source: str
    overrides_source: str
    request_policy_source: str


def _digest(value) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return 'sha256:' + hashlib.sha256(encoded).hexdigest()


def _source_paths(vault_root: Path) -> tuple[Path, ...]:
    paths = tuple(Path(path) for path in brain_config.config_input_paths(str(vault_root)))
    return (*paths, paths[0].with_name('command-authority.json'))


def _path_signature(path: Path) -> tuple:
    canonical = str(path.resolve())
    try:
        stat = path.stat()
    except FileNotFoundError:
        # Parent timestamps would revoke consent on every atomic cache write.
        return str(path), canonical, 'missing'
    return (str(path), canonical, stat.st_dev, stat.st_ino, stat.st_mode,
            stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def authorisation_config_signature(vault_root: Path) -> tuple:
    """Cheap cache signature; present source restoration cannot restore its ctime."""
    return tuple(_path_signature(path) for path in _source_paths(vault_root))


def read_authorisation_sources(vault_root: Path, *, catalogue) -> AuthorisationSources:
    """Pair parsed policy/authentication with the exact source revision read."""
    paths = _source_paths(vault_root)
    for _attempt in range(3):
        before = tuple(_path_signature(path) for path in paths)
        contents = []
        for index, path in enumerate(paths):
            try:
                contents.append(path.read_bytes())
            except FileNotFoundError:
                if index in {0, 3}:
                    raise brain_config.ConfigError(f'required authorisation input is missing: {path}')
                contents.append(None)
        after = tuple(_path_signature(path) for path in paths)
        if before != after:
            continue
        layers = tuple(load_mapping_text(content.decode('utf-8'), source=str(path)) if content is not None else {}
                       for path, content in zip(paths[:3], contents[:3]))
        config = brain_config.merge_config_sources(*layers, additional_valid_tools=frozenset(
            entry.command_id for entry in catalogue.entries))
        revision = _digest({'sources': after, 'contents': [hashlib.sha256(value).hexdigest()
                                                         if value is not None else None for value in contents]})
        return AuthorisationSources(config, layers, paths, revision, after)
    raise brain_config.ConfigError('authorisation configuration changed while being read; retry after the edit completes')


def _mapping(value, label: str) -> dict:
    if not isinstance(value, dict):
        raise brain_config.ConfigError(f'{label} must be a mapping')
    return value


def _setting_source(sources: AuthorisationSources, zone: str, setting: str) -> str:
    winner = 'template'
    for name, layer in zip(('template', 'shared', 'local'), sources.layers):
        if zone == 'vault' and name == 'local':
            continue
        access = layer.get(zone, {}).get('access', {})
        if isinstance(access, dict) and setting in access:
            winner = name
    return winner


def profile_permissions(config: dict, profile: str, *, commands: frozenset[str]) -> frozenset[str]:
    """Validate one current credential maximum without silently dropping unknown IDs."""
    profiles = _mapping(config.get('vault', {}).get('profiles', {}), 'vault.profiles')
    definition = profiles.get(profile)
    if not isinstance(definition, dict) or not isinstance(definition.get('allow'), list):
        raise brain_config.ConfigError(f'profile {profile!r} has an invalid allow-list')
    values = definition['allow']
    if any(not isinstance(value, str) for value in values):
        raise brain_config.ConfigError(f'profile {profile!r} contains a non-command permission')
    allowed = frozenset(values)
    if unknown := allowed - commands:
        raise brain_config.ConfigError(f'profile {profile!r} contains unknown commands: {", ".join(sorted(unknown))}')
    return allowed


def resolve_authorisation_config(*, vault_root: Path, catalogue, operator_key: str | None,
                                 delegated_binding=None) -> ResolvedAuthorisation:
    """Authenticate or privately delegate, then resolve current initial access and policy."""
    sources = read_authorisation_sources(vault_root, catalogue=catalogue)
    return resolve_authorisation_sources(sources, catalogue=catalogue, operator_key=operator_key,
                                         delegated_binding=delegated_binding)


def resolve_authorisation_sources(sources: AuthorisationSources, *, catalogue, operator_key: str | None,
                                  delegated_binding=None) -> ResolvedAuthorisation:
    """Resolve a captured snapshot, shared by ordinary composition and guarded administration."""
    config = sources.config
    if delegated_binding is None:
        profile, binding = brain_config.authenticate_operator_binding(operator_key, config)
    else:
        # Only this call can positively revoke the pinned registration. A wrong
        # explicit key is a caller rejection, not authority to revoke siblings.
        profile = brain_config.resolve_operator_binding(delegated_binding, config)
        binding = delegated_binding
        if operator_key is not None:
            _profile, explicit = brain_config.authenticate_operator_binding(operator_key, config)
            if explicit != binding:
                raise ValueError('explicit operator key does not match the private session principal')
    principal = f'operator:{binding.operator_id}' if binding.operator_id is not None else 'default'
    classes = {entry.command_id: entry.initial_class.value for entry in catalogue.entries}
    commands = frozenset(classes)
    permissions = profile_permissions(config, profile, commands=commands)
    controls = frozenset(command for command, kind in classes.items() if kind == 'control')
    defaults = _mapping(config.get('defaults', {}).get('access', {}), 'defaults.access')
    vault = _mapping(config.get('vault', {}).get('access', {}), 'vault.access')
    diagnostics = []
    legacy_initial = {'initial_profile', 'initial_commands'} & set(defaults)
    if legacy_initial:
        raise brain_config.ConfigError('stored legacy initial access requires the authorisation migration before use')
    initial = _mapping(defaults.get('initial', {'mode': 'normal'}), 'defaults.access.initial')
    mode = initial.get('mode')
    if mode not in {'normal', 'read-only', 'explicit'} or set(initial) != ({'mode', 'commands'} if mode == 'explicit' else {'mode'}):
        raise brain_config.ConfigError('defaults.access.initial requires mode normal/read-only, or explicit with commands')
    if mode == 'explicit':
        values = initial['commands']
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise brain_config.ConfigError('defaults.access.initial.commands must be a list of exact command IDs')
        selected = set(values)
    else:
        selected = {command for command, kind in classes.items()
                    if kind == 'observation' or (mode == 'normal' and kind == 'content')}
    overrides = _mapping(defaults.get('overrides', {}), 'defaults.access.overrides')
    if any(not isinstance(command, str) or type(enabled) is not bool for command, enabled in overrides.items()):
        raise brain_config.ConfigError('defaults.access.overrides must map exact command IDs to Boolean values')
    if unknown := (selected | set(overrides)) - commands:
        raise brain_config.ConfigError('initial authorisation contains unknown commands: ' + ', '.join(sorted(unknown)))
    for command, enabled in overrides.items():
        if command in controls:
            diagnostics.append(AuthorisationDiagnostic('control_conflict', 'defaults.access.overrides',
                _setting_source(sources, 'defaults', 'overrides'), f'{command} is a control; remove its ordinary initial override'))
        elif enabled:
            selected.add(command)
        else:
            selected.discard(command)
    selected.difference_update(controls)
    missing = controls - permissions
    if missing:
        shared_profile = sources.layers[1].get('vault', {}).get('profiles', {}).get(profile, {})
        permission_source = 'shared' if isinstance(shared_profile, dict) and 'allow' in shared_profile else 'template'
        diagnostics.append(AuthorisationDiagnostic('control_conflict', 'vault.profiles.' + profile, permission_source,
            'credential maximum omits required controls: ' + ', '.join(sorted(missing))))
    request_policy = vault.get('request_policy', 'allowed')
    if request_policy not in {'allowed', 'denied', 'migration_required'}:
        raise brain_config.ConfigError('vault.access.request_policy must be allowed or denied (migration_required is an inert migration marker)')
    if request_policy == 'migration_required':
        diagnostics.append(AuthorisationDiagnostic('migration_required', 'vault.access.request_policy',
            _setting_source(sources, 'vault', 'request_policy'),
            'external approval was retired; an administrator must explicitly choose allowed or denied'))
    if any(key != 'request_policy' for key in vault):
        diagnostics.append(AuthorisationDiagnostic('migration_required', 'vault.access', 'shared',
            'legacy access settings require explicit migration; choose request_policy and remove retired settings'))
    if diagnostics:
        request_policy = 'migration_required'
    policy = ConsentPolicy(profile, permissions, frozenset(selected) & permissions, mode, request_policy, controls)
    generation = _digest({'revision': sources.revision, 'principal': principal, 'profile': profile,
        'permissions': sorted(permissions), 'initial': sorted(policy.initial), 'controls': sorted(controls),
        'mode': mode, 'request_policy': request_policy, 'classes': classes,
        'registration': binding.registration_fingerprint})
    return ResolvedAuthorisation(config, profile, binding.operator_id, principal, policy, sources.revision,
        generation, tuple(diagnostics), binding,
        _setting_source(sources, 'defaults', 'initial'),
        _setting_source(sources, 'defaults', 'overrides'),
        _setting_source(sources, 'vault', 'request_policy'))
