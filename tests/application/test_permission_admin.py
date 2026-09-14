"""Permission changes require current explicit administrator auth and immutable audit."""

from dataclasses import replace
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest

from _common import hash_key
from _common._yaml import dump_mapping_text, load_mapping_file
from _command_interface.permission_admin import PermissionOutcomeUnknown, set_operator_profile
from _command_interface.receipts import OwnedReceiptStore
from _application.receipts import OutcomeReference, ReceiptState, ReceiptIntentConflict


CLASSES = {'access.request': 'control', 'vault.read-file': 'observation',
           'artefact.create': 'content', 'artefact.delete': 'exceptional'}
CATALOGUE = SimpleNamespace(entries=tuple(SimpleNamespace(command_id=key, initial_class=SimpleNamespace(value=value))
                                         for key, value in CLASSES.items()))


class Clock:
    def now(self):
        return datetime.now(timezone.utc)


@pytest.fixture
def root(tmp_path):
    (tmp_path / '.brain-core').mkdir()
    (tmp_path / '.brain-core/VERSION').write_text('0.68.0\n')
    (tmp_path / '.brain/local').mkdir(parents=True)
    raw = {'vault': {'profiles': {'administrator': {'allow': list(CLASSES)},
                                  'operator': {'allow': list(CLASSES)},
                                  'reader': {'allow': ['access.request', 'vault.read-file']}},
                     'operators': [
                         {'id': 'admin', 'profile': 'administrator', 'auth': {'type': 'key', 'hash': hash_key('admin-key')}},
                         {'id': 'agent', 'profile': 'operator', 'auth': {'type': 'key', 'hash': hash_key('agent-key')}},
                     ]}}
    (tmp_path / '.brain/config.yaml').write_text(dump_mapping_text(raw))
    return tmp_path


def change(root, **options):
    return set_operator_profile(vault_root=root, catalogue=CATALOGUE, operator_key=options.pop('key', 'admin-key'),
        operator_id='agent', profile=options.pop('profile', 'reader'), expected_revision=options.pop('revision', None),
        dry_run=options.pop('dry_run', True), invocation_id=options.pop('invocation_id', 'preview'), clock=Clock(), **options)


def test_preview_then_apply_preserves_registration_and_records_distinct_intent_final(root):
    initial = (root / '.brain/config.yaml').read_bytes()
    planned = change(root)
    assert planned.status == 'planned'
    assert planned.removed_permissions == ('artefact.create', 'artefact.delete')
    assert (root / '.brain/config.yaml').read_bytes() == initial
    result = change(root, revision=planned.revision, dry_run=False, invocation_id='apply')
    assert result.status == 'applied'
    assert result.revision != planned.revision
    raw = load_mapping_file(root / '.brain/config.yaml')
    assert raw['vault']['operators'][1]['profile'] == 'reader'
    assert raw['vault']['operators'][1]['auth']['hash'] == hash_key('agent-key')
    files = list((root / '.brain/local/command-outcomes/owned').glob('*.json'))
    audit = next(json.loads(path.read_text()) for path in files if json.loads(path.read_text())['intent']['reference']['invocation_id'] == 'apply')
    assert audit['ownership']['principal'] == 'operator:admin'
    assert audit['ownership']['kind'] == 'standalone'
    assert audit['intent']['permission_change']['before_profile'] == 'operator'
    assert audit['intent']['permission_change']['after_profile'] == 'reader'
    assert 'admin-key' not in json.dumps(audit)
    assert hash_key('agent-key') not in json.dumps(audit)
    repeated = change(root, revision=planned.revision, dry_run=False, invocation_id='apply')
    assert repeated == result


@pytest.mark.parametrize('key', [None, 'agent-key', 'invalid-key'])
def test_keyless_nonadmin_and_unknown_keys_cannot_assign_permissions(root, key):
    before = (root / '.brain/config.yaml').read_bytes()
    with pytest.raises((PermissionError, ValueError)):
        change(root, key=key, profile='administrator')
    assert (root / '.brain/config.yaml').read_bytes() == before
    assert not (root / '.brain/local/command-outcomes').exists()


def test_changed_revision_and_admin_revocation_are_checked_again_before_apply(root):
    planned = change(root)
    raw = load_mapping_file(root / '.brain/config.yaml')
    raw['vault']['brain_name'] = 'Concurrent change'
    (root / '.brain/config.yaml').write_text(dump_mapping_text(raw))
    with pytest.raises(ValueError, match='revision changed'):
        change(root, revision=planned.revision, dry_run=False, invocation_id='stale')
    raw['vault']['operators'][0]['profile'] = 'operator'
    (root / '.brain/config.yaml').write_text(dump_mapping_text(raw))
    with pytest.raises(PermissionError):
        change(root, revision=planned.revision, dry_run=False, invocation_id='revoked')


def test_failed_intent_blocks_write_and_failed_final_leaves_attributed_unknown(root):
    planned = change(root)
    original = (root / '.brain/config.yaml').read_bytes()
    captured = []
    class Failure:
        def __init__(self, owner, at):
            self.saved = OwnedReceiptStore(root, Clock(), owner)
            self.at = at
            captured.append(self.saved)
        def read(self, ref): return self.saved.read(ref)
        def begin(self, value):
            if self.at == 'intent': raise OSError('intent unavailable')
            return self.saved.begin(value)
        def finalise(self, value):
            if self.at == 'final': raise OSError('final unavailable')
            self.saved.finalise(value)
    with pytest.raises(OSError, match='intent unavailable'):
        change(root, revision=planned.revision, dry_run=False, invocation_id='failed-intent',
               receipt_factory=lambda owner: Failure(owner, 'intent'))
    assert (root / '.brain/config.yaml').read_bytes() == original
    with pytest.raises(PermissionOutcomeUnknown):
        change(root, revision=planned.revision, dry_run=False, invocation_id='failed-final',
               receipt_factory=lambda owner: Failure(owner, 'final'))
    lookup = captured[-1].read(OutcomeReference('failed-final'))
    assert lookup.intent.permission_change.after_profile == 'reader'
    assert lookup.outcome is None
    assert load_mapping_file(root / '.brain/config.yaml')['vault']['operators'][1]['profile'] == 'reader'
    with pytest.raises(PermissionOutcomeUnknown):
        change(root, revision=planned.revision, dry_run=False, invocation_id='failed-final')


@pytest.mark.parametrize('claim_result', [False, None, ReceiptIntentConflict])
def test_unclaimed_intent_blocks_permission_write_and_finalisation(root, claim_result):
    planned = change(root)
    before = (root / '.brain/config.yaml').read_bytes()

    class Unclaimed:
        def __init__(self, owner):
            self.saved = OwnedReceiptStore(root, Clock(), owner)
        def read(self, ref):
            return self.saved.read(ref)
        def begin(self, value):
            self.saved.begin(value)
            if claim_result is ReceiptIntentConflict:
                raise ReceiptIntentConflict('admission intent is immutable once recorded')
            return claim_result
        def finalise(self, value):
            pytest.fail('an unclaimed invocation cannot publish a final outcome')

    with pytest.raises(PermissionOutcomeUnknown):
        change(root, revision=planned.revision, dry_run=False, invocation_id='unclaimed',
               receipt_factory=Unclaimed)
    assert (root / '.brain/config.yaml').read_bytes() == before
