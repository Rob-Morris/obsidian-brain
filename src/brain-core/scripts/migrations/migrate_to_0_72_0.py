"""Cut over workspace permissions before admitting the reintroduced register."""

from pathlib import Path

from config import ConfigError
from _common import safe_write
from _common._yaml import load_mapping_file, dump_mapping_text
from _command_interface.workspace_permission_migration import project_workspace_permissions

VERSION = '0.72.0'
TARGET_HANDLERS = {'pre_compile_patch': 'patch_pre_compile'}
PRECOMPILE_DEFAULT_PREDECESSORS = ('0.55.0', '0.56.0', '0.57.0', '0.59.0', '0.62.9', '0.68.0', '0.71.0')
_FILES = ('.brain/config.yaml', '.brain/local/config.yaml')


def prospective_effects(vault_root):
    """Use the upgrade journal's existing authored-configuration scope."""
    return [str(Path(vault_root) / name) for name in _FILES]


def patch_pre_compile(vault_root, *, context=None):
    """Project only captured source semantics; never transfer active consent."""
    before = (context or {}).get('authorisation_before_upgrade')
    if not isinstance(before, dict) or before.get('schema') != 'brain.authorisation-before-upgrade/1':
        raise ConfigError('workspace permission cutover requires the coordinated pre-upgrade snapshot')
    paths = [Path(vault_root) / name for name in _FILES]
    originals = [load_mapping_file(path) if path.exists() else {} for path in paths]
    projected = project_workspace_permissions(*originals, source_version=before.get('version'), cutover_version=VERSION)
    if tuple(map(int, before['version'].split('.'))) >= (0, 72, 0):
        return {'status': 'skipped', 'settings': []}
    # Only an exact old shipped allow-list establishes authority to regenerate
    # a built-in; labels do not change its permission meaning.
    old_template, old_shared, _old_local = before['layers']
    from _application.registry import current_application_catalogue
    from _command_interface.profiles import builtin_profile_allow_lists
    builtins = builtin_profile_allow_lists(current_application_catalogue())
    for name, definition in old_shared.get('vault', {}).get('profiles', {}).items():
        old = old_template.get('vault', {}).get('profiles', {}).get(name)
        if (name in builtins and isinstance(definition, dict) and isinstance(old, dict)
                and isinstance(definition.get('allow'), list)
                and definition['allow'] == old.get('allow')):
            projected[0]['vault']['profiles'][name]['allow'] = list(builtins[name])
    settings = []
    for name, path, original, updated in zip(_FILES, paths, originals, projected):
        if original != updated:
            safe_write(path, dump_mapping_text(updated), bounds=vault_root, follow_symlinks=False)
            settings.append(name)
    return {'status': 'ok' if settings else 'skipped', 'settings': settings}
