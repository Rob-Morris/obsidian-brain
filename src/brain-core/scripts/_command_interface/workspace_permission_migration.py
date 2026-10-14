"""Pure, source-version-bound projection of persisted workspace permissions."""

from copy import deepcopy
import re

from config import ConfigError
from .profile_migration import RETIRED_COMMANDS


_REPLACEMENTS = {'workspace.ensure-registration': 'workspace.create',
                 'workspace.setup': 'workspace.register'}


def _version(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d+\.\d+\.\d+', value):
        raise ConfigError('workspace permission cutover requires an exact source and cutover version')
    return tuple(map(int, value.split('.')))


def _commands(values, setting):
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise ConfigError(f'{setting} must be a command list')
    retained = [value for value in values if value not in RETIRED_COMMANDS]
    return list(dict.fromkeys(_REPLACEMENTS.get(value, value) for value in retained))


def _mapping(value, setting):
    if not isinstance(value, dict):
        raise ConfigError(f'{setting} must be a mapping')
    return value


def _project(layer, *, shared):
    updated = deepcopy(layer)
    if shared and 'vault' in updated:
        vault = _mapping(updated['vault'], 'vault')
        if 'profiles' in vault:
            for name, profile in _mapping(vault['profiles'], 'vault.profiles').items():
                profile = _mapping(profile, f'vault.profiles.{name}')
                if 'allow' in profile:
                    profile['allow'] = _commands(profile['allow'], f'vault.profiles.{name}.allow')
    if 'defaults' not in updated:
        return updated
    defaults = _mapping(updated['defaults'], 'defaults')
    if 'access' not in defaults:
        return updated
    access = _mapping(defaults['access'], 'defaults.access')
    if 'initial' in access:
        initial = _mapping(access['initial'], 'defaults.access.initial')
        if 'commands' in initial:
            initial['commands'] = _commands(initial['commands'], 'defaults.access.initial.commands')
    if 'overrides' in access:
        overrides = _mapping(access['overrides'], 'defaults.access.overrides')
        if any(not isinstance(command, str) or type(value) is not bool
               for command, value in overrides.items()):
            raise ConfigError('defaults.access.overrides must map command IDs to Boolean values')
        overrides = {key: value for key, value in overrides.items() if key not in RETIRED_COMMANDS}
        for source, target in _REPLACEMENTS.items():
            if source in overrides and target in overrides and overrides[source] != overrides[target]:
                raise ConfigError(f'defaults.access.overrides has conflicting {source} and {target}; resolve explicitly')
        access['overrides'] = {_REPLACEMENTS.get(key, key): value for key, value in overrides.items()}
    return updated


def project_workspace_permissions(shared, local, *, source_version, cutover_version):
    """Plan both authored layers before writes; current-version loads retain new grants.

    The coordinated migration supplies its release version and the captured
    installed version. It owns admission, observed revisions, writes and ledger;
    this projection neither reads nor transfers live consent.
    """
    source, cutover = _version(source_version), _version(cutover_version)
    shared = _mapping(shared, 'shared configuration')
    local = _mapping(local, 'local configuration')
    if source >= cutover:
        return deepcopy(shared), deepcopy(local)
    return _project(shared, shared=True), _project(local, shared=False)
