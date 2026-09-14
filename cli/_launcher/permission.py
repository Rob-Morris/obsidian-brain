"""Thin CLI route to the selected Brain's authenticated permission service."""

from dataclasses import dataclass
import json
from typing import ClassVar, Literal
import subprocess

from .context import LauncherContext
from .contracts import (CommandError, CommittedEffect, Error, ErrorCode, Ok,
                         OutcomeReference, OutcomeUnknownDetails, InstructionNextAction)


@dataclass(frozen=True, slots=True)
class PermissionProfilePayload:
    status: Literal['planned', 'applied', 'unchanged']
    operator_id: str
    before_profile: str
    after_profile: str
    added_permissions: tuple[str, ...]
    removed_permissions: tuple[str, ...]
    revision: str
    invocation_id: str

    def __post_init__(self):
        if self.status not in {'planned', 'applied', 'unchanged'}:
            raise ValueError('permission result status is invalid')
        for name in ('operator_id', 'before_profile', 'after_profile', 'revision', 'invocation_id'):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError('permission result identity is invalid')
        for values in (self.added_permissions, self.removed_permissions):
            if not isinstance(values, tuple) or any(not isinstance(value, str) or not value for value in values) or values != tuple(sorted(set(values))):
                raise ValueError('permission result command differences are invalid')
        if set(self.added_permissions) & set(self.removed_permissions):
            raise ValueError('permission result command differences overlap')


@dataclass(frozen=True, slots=True)
class PermissionSetProfileRequest:
    COMMAND_ID: ClassVar[str] = 'permission.set-profile'
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = PermissionProfilePayload

    operator_id: str
    profile: str
    expected_revision: str | None = None

    def __post_init__(self):
        if any(not isinstance(value, str) or not value.strip() for value in (self.operator_id, self.profile)):
            raise ValueError('permission.set-profile requires operator_id and profile')
        if self.expected_revision is not None and (not isinstance(self.expected_revision, str) or not self.expected_revision.strip()):
            raise ValueError('expected_revision must be a non-empty revision when supplied')


def execute(context: LauncherContext, request: PermissionSetProfileRequest):
    if context.current_vault is None or not context.operator_key:
        return _error(request, ErrorCode.AUTHORITY_DENIED, 'Select a Brain and supply a registered administrator key.')
    if not context.dry_run and request.expected_revision is None:
        return _error(request, ErrorCode.INVALID_REQUEST, 'Apply requires expected_revision from a dry-run preview.')
    helper = context.current_vault / '.brain-core/scripts/permission_admin.py'
    if helper.is_symlink() or not helper.is_file():
        return _error(request, ErrorCode.CAPABILITY_UNAVAILABLE, 'Upgrade the selected Brain to provide permission administration.')
    python = context.launcher_python
    if python is None or python.is_symlink() or not python.is_file():
        raise RuntimeError('launcher Python is unavailable for permission administration')
    from _bootstrap.owner_attachment import without_owner_environment
    environment = without_owner_environment()
    environment.pop('BRAIN_OPERATOR_KEY', None)
    environment['BRAIN_PERMISSION_ADMIN_KEY'] = context.operator_key
    argv = [str(python), str(helper), '--vault', str(context.current_vault), '--invocation-id', context.invocation_id]
    if context.dry_run:
        argv.append('--dry-run')
    completed = subprocess.run(argv, input=json.dumps({'operator_id': request.operator_id, 'profile': request.profile,
        'expected_revision': request.expected_revision}), capture_output=True, text=True, check=False, env=environment)
    try:
        value = json.loads(completed.stdout)
        if not isinstance(value, dict):
            raise ValueError('invalid permission helper result')
        if completed.returncode == 2 and set(value) == {'status', 'code', 'message'} and value['status'] == 'error':
            code = ErrorCode(value['code'])
            if code not in {ErrorCode.AUTHORITY_DENIED, ErrorCode.INVALID_REQUEST} or not isinstance(value['message'], str):
                raise ValueError('invalid permission helper rejection')
            return _error(request, code, value['message'])
        if completed.returncode == 3 and set(value) == {'status', 'invocation_id', 'message'} and value['status'] == 'unknown':
            if value['invocation_id'] != context.invocation_id or not isinstance(value['message'], str):
                raise ValueError('invalid permission helper recovery reference')
            reference = OutcomeReference(context.invocation_id)
            return Error(request.COMMAND_ID, request.COMMAND_VERSION,
                CommandError(ErrorCode.COMMAND_OUTCOME_UNKNOWN, value['message'], OutcomeUnknownDetails(reference),
                             InstructionNextAction('Inspect invocation.read in the selected Brain using this invocation ID and the same principal.')),
                effects='unknown', outcome_reference=reference)
        if completed.returncode != 0 or completed.stderr or set(value) != {'status', 'result'} or value['status'] != 'ok':
            raise ValueError('permission helper failed its process contract')
        payload = value['result']
        expected = {'status', 'operator_id', 'before_profile', 'after_profile', 'added_permissions',
                    'removed_permissions', 'revision', 'invocation_id'}
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ValueError('permission helper result has an invalid shape')
        if any(not isinstance(payload[field], list) for field in ('added_permissions', 'removed_permissions')):
            raise ValueError('permission helper command differences must be lists')
        result = PermissionProfilePayload(**{**payload, 'added_permissions': tuple(payload['added_permissions']),
                                           'removed_permissions': tuple(payload['removed_permissions'])})
        if (result.operator_id, result.after_profile, result.invocation_id) != (request.operator_id, request.profile, context.invocation_id):
            raise ValueError('permission helper result identity changed')
    except (ValueError, TypeError, KeyError) as exc:
        raise RuntimeError('permission helper returned invalid output') from exc
    effects = (CommittedEffect('permission.profile', result.operator_id),) if result.status == 'applied' else ()
    return Ok(request.COMMAND_ID, request.COMMAND_VERSION, result, effects)


def _error(request, code, message):
    return Error(request.COMMAND_ID, request.COMMAND_VERSION, CommandError(code, message))


def set_profile_owner():
    from .owners import LauncherOwner
    return LauncherOwner(PermissionSetProfileRequest, PermissionProfilePayload, '_launcher.permission:set-profile', execute)
