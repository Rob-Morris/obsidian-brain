"""Convert access settings before the new authorisation configuration is compiled."""

from pathlib import Path

from _common import safe_write, safe_write_json
from _bootstrap import branch_guard
from _bootstrap.local_config import patch_local_config, read_local_config
from _bootstrap.yaml_engine import load_yaml_text
from _command_interface.authorisation_migration import REPORT_PATH, plan_authorisation_migration

VERSION = '0.68.0'
TARGET_HANDLERS = {'pre_compile_patch': 'patch_pre_compile'}


def prospective_effects(vault_root):
    """Declare both authored layers and conversion evidence for existing rollback."""
    return [str(Path(vault_root) / path) for path in ('.brain/config.yaml', '.brain/local/config.yaml', REPORT_PATH)]


def patch_pre_compile(vault_root, *, context=None):
    """Consume the pre-replacement snapshot without importing any lease authority."""
    before = (context or {}).get('authorisation_before_upgrade')
    plan = plan_authorisation_migration(vault_root, before)
    for relative, content in plan.writes:
        branch_guard.checkpoint(vault_root)
        if relative == '.brain/local/config.yaml':
            before = read_local_config(vault_root)
            updated = load_yaml_text(content)
            def update(document):
                guard = {key: document[key] for key in ('branch_guard',) if key in document}
                document.clear()
                document.update(updated)
                document.pop('branch_guard', None)
                document.update(guard)
            patch_local_config(vault_root, update, expected_revision=before.revision, lock_held=True)
        else:
            safe_write(Path(vault_root) / relative, content, bounds=vault_root, follow_symlinks=False)
    if plan.writes or plan.report['conflicts']:
        safe_write_json(Path(vault_root) / REPORT_PATH, plan.report, bounds=vault_root, follow_symlinks=False)
    return {'status': 'ok' if plan.writes or plan.report['conflicts'] else 'skipped',
            'configuration_status': plan.report['status'],
            'settings': list(plan.report['changes']), 'conflicts': plan.report['conflicts']}


def migrate(vault_root):
    """Verify conversion already ran through the pre-compile migration owner."""
    plan_authorisation_migration(vault_root, None)
    return {'status': 'skipped'}
