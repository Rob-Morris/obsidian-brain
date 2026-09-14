"""The permission launcher routes to one authenticated selected-Brain service."""

from datetime import datetime, timezone
from pathlib import Path
import sys

CLI = Path(__file__).resolve().parents[2] / 'cli'
if str(CLI) not in sys.path:
    sys.path.insert(0, str(CLI))

from _application.registry import current_application_catalogue
from _common import hash_key
from _common._yaml import dump_mapping_text, load_mapping_file
from _launcher.context import LauncherContext, ProviderBindings
from _launcher.contracts import Error, Ok
from _launcher.invocation import LauncherInvocation
from _launcher.owners import LAUNCHER_OWNERS
from _launcher.permission import PermissionSetProfileRequest
from launcher_catalogue import LAUNCHER_CATALOGUE


class Clock:
    def now(self): return datetime.now(timezone.utc)


class Authority:
    def allows(self, **kwargs): return True


class Receipts:
    def write(self, receipt): pass


class Filesystem:
    provider_id = 'caller_filesystem'
    available = True


def invoke(root, *, dry_run, revision=None, key='admin-key', identity='preview'):
    context = LauncherContext(profile='local-operator', authority=Authority(), providers=ProviderBindings((Filesystem(),)),
        correlation_id=identity, invocation_id=identity, receipt_writer=Receipts(), clock=Clock(),
        caller_dir=root, home_dir=root, cli_version='3.3.0', cli_binary=root / 'brain',
        launcher_python=Path(sys.executable).resolve(), current_vault=root, operator_key=key, dry_run=dry_run)
    return LauncherInvocation(context, LAUNCHER_CATALOGUE, LAUNCHER_OWNERS).invoke(
        PermissionSetProfileRequest('agent', 'reader', revision))


def test_real_helper_preview_then_apply_is_cli_only(command_vault_clone):
    root = command_vault_clone.vault_root
    path = root / '.brain/config.yaml'
    raw = {'vault': {'operators': [
        {'id': 'admin', 'profile': 'administrator', 'auth': {'type': 'key', 'hash': hash_key('admin-key')}},
        {'id': 'agent', 'profile': 'operator', 'auth': {'type': 'key', 'hash': hash_key('agent-key')}}]}}
    path.write_text(dump_mapping_text(raw))
    planned = invoke(root, dry_run=True)
    assert isinstance(planned, Ok), planned
    assert planned.result.status == 'planned'
    applied = invoke(root, dry_run=False, revision=planned.result.revision, identity='apply')
    assert isinstance(applied, Ok), applied
    assert applied.result.status == 'applied'
    assert load_mapping_file(path)['vault']['operators'][1]['profile'] == 'reader'
    application_ids = {entry.command_id for entry in current_application_catalogue().entries}
    assert 'permission.set-profile' not in application_ids
    assert 'access.approve' not in {entry.command_id for entry in LAUNCHER_CATALOGUE.entries}


def test_launcher_rejects_apply_without_revision_before_selected_helper(command_vault_clone):
    result = invoke(command_vault_clone.vault_root, dry_run=False)
    assert isinstance(result, Error)
    assert result.error.code.value == 'invalid_request'
