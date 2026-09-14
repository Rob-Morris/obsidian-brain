"""Selected-Brain permission administration, reached only through an explicit CLI key."""

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from _application.receipts import (AdmissionIntent, CommittedEffect, ExecutionState, InvocationOutcome,
    OutcomeReceipt, OutcomeReference, PermissionChange, ReceiptOwnership, ReceiptState, ReceiptIntentConflict)
from _common import is_brain_vault, safe_write, vault_mutation_lock
from _common._yaml import dump_mapping_text
from .authorisation_config import read_authorisation_sources, resolve_authorisation_sources, profile_permissions
from .receipts import OwnedReceiptStore


class PermissionOutcomeUnknown(RuntimeError):
    """A durable intent exists but completion must be inspected, never replayed."""

    def __init__(self, invocation_id):
        super().__init__('Permission update outcome is unknown; inspect its owned invocation receipt before further changes.')
        self.invocation_id = invocation_id


@dataclass(frozen=True, slots=True)
class PermissionProfileResult:
    status: str
    operator_id: str
    before_profile: str
    after_profile: str
    added_permissions: tuple[str, ...]
    removed_permissions: tuple[str, ...]
    revision: str
    invocation_id: str


def set_operator_profile(*, vault_root: Path, catalogue, operator_key: str | None,
                         operator_id: str, profile: str, expected_revision: str | None,
                         dry_run: bool, invocation_id: str, clock, receipt_factory=None) -> PermissionProfileResult:
    """Preview or atomically apply one existing registration's profile with durable attribution."""
    from .direct import resolve_direct_brain_id

    if not operator_key:
        raise PermissionError('permission.set-profile requires an explicit registered administrator key')
    if any(not isinstance(value, str) or not value.strip() for value in (operator_id, profile, invocation_id)):
        raise ValueError('permission.set-profile requires operator_id, profile and invocation identity')
    if type(dry_run) is not bool or (not dry_run and not expected_revision):
        raise ValueError('apply requires expected_revision from a dry-run preview')
    if vault_root.is_symlink() or not is_brain_vault(vault_root):
        raise ValueError('permission administration requires an installed selected Brain')
    root = vault_root.resolve()
    reference = OutcomeReference(invocation_id)
    request_digest = 'sha256:' + hashlib.sha256(json.dumps({'operator_id': operator_id, 'profile': profile,
        'expected_revision': expected_revision, 'dry_run': dry_run}, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    with vault_mutation_lock(root):
        sources = read_authorisation_sources(root, catalogue=catalogue)
        actor = resolve_authorisation_sources(sources, catalogue=catalogue, operator_key=operator_key)
        if actor.operator_id is None or actor.profile != 'administrator':
            raise PermissionError('permission.set-profile requires a registered administrator-profile credential')
        ownership = ReceiptOwnership(resolve_direct_brain_id(root), actor.principal, 'standalone')
        receipts = (receipt_factory(ownership) if receipt_factory is not None else OwnedReceiptStore(root, clock, ownership))
        prior = receipts.read(reference)
        if prior.intent is not None:
            if prior.intent.command_id != 'permission.set-profile' or prior.intent.operation_digest != request_digest:
                raise ValueError('invocation identity already belongs to a different permission request')
            if prior.outcome is None or prior.outcome.execution is not ExecutionState.SUCCEEDED:
                raise PermissionOutcomeUnknown(invocation_id)
            change = prior.intent.permission_change
            return _result(change, prior.outcome.config_revision or change.before_revision, invocation_id,
                           'planned' if dry_run else ('applied' if prior.outcome.receipt.committed_effects else 'unchanged'))
        if expected_revision is not None and expected_revision != sources.revision:
            raise ValueError('configuration revision changed; obtain a fresh permission preview')
        shared = deepcopy(sources.layers[1])
        registrations = shared.get('vault', {}).get('operators', [])
        targets = [item for item in registrations if isinstance(item, dict) and item.get('id') == operator_id]
        if len(targets) != 1:
            raise ValueError('target must name one existing, unambiguous shared operator registration')
        target = targets[0]
        before_profile = target.get('profile', sources.config.get('defaults', {}).get('default_profile', 'operator'))
        commands = frozenset(entry.command_id for entry in catalogue.entries)
        before_permissions = profile_permissions(sources.config, before_profile, commands=commands)
        after_permissions = profile_permissions(sources.config, profile, commands=commands)
        controls = frozenset(entry.command_id for entry in catalogue.entries if entry.initial_class.value == 'control')
        if missing := controls - after_permissions:
            raise ValueError('target profile omits required controls: ' + ', '.join(sorted(missing)))
        change = PermissionChange(operator_id, before_profile, profile,
                                  tuple(sorted(after_permissions - before_permissions)),
                                  tuple(sorted(before_permissions - after_permissions)), sources.revision)
        target['profile'] = profile
        rendered = dump_mapping_text(shared)
        changed = shared != sources.layers[1] and not dry_run
        intent = AdmissionIntent(reference, 'permission.set-profile', 1, clock.now(), 'initial',
                                 actor.permission_generation, 'cli-request', operation_digest=request_digest,
                                 permission_change=change)
        try:
            claimed = receipts.begin(intent)
        except ReceiptIntentConflict as exc:
            raise PermissionOutcomeUnknown(invocation_id) from exc
        if claimed is not True:
            raise PermissionOutcomeUnknown(invocation_id)
        try:
            if changed:
                safe_write(sources.paths[1], rendered, bounds=root, follow_symlinks=False)
                revision = read_authorisation_sources(root, catalogue=catalogue).revision
            else:
                revision = sources.revision
            effects = (CommittedEffect('permission.profile', operator_id),) if changed else ()
            outcome = InvocationOutcome(OutcomeReceipt(reference, 'permission.set-profile', 1,
                ReceiptState.COMMITTED if changed else ReceiptState.NONE, clock.now(), effects),
                ExecutionState.SUCCEEDED, revision)
            receipts.finalise(outcome)
        except Exception as exc:
            # Publication and audit are separate durable writes. Neither an
            # absent final record nor an exception proves replacement did not occur.
            raise PermissionOutcomeUnknown(invocation_id) from exc
        return _result(change, revision, invocation_id, 'planned' if dry_run else ('applied' if changed else 'unchanged'))


def _result(change, revision, invocation_id, status):
    return PermissionProfileResult(status, change.operator_id, change.before_profile, change.after_profile,
                                   change.added_permissions, change.removed_permissions, revision, invocation_id)
