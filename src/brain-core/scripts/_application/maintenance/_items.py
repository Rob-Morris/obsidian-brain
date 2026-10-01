"""The Brain pass's view of detection joined with decisions and the last pass."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from _bootstrap.maintenance_decisions import (
    DecisionState,
    Decisions,
    DecisionsUnreadable,
    ItemState,
    apply_decisions,
    read_decisions,
)
from _bootstrap.maintenance_findings import Disposition, FindingGroup, Owner, group_by_family
from _bootstrap.maintenance_summary import GroupOutcome, MaintenancePaths, brain_paths, read_last_pass

from ..results import ErrorCode, request_error
from ._detection import DetectionFailed, detect


MACHINE_GUIDANCE = "see the machine pass: brain machine-maintenance list"


@dataclass(frozen=True, slots=True)
class MaintenanceItem:
    """One claimable unit: a family group or a per-file judgement finding."""

    key: str
    fingerprint: str
    kind: str
    disposition: Disposition
    owner: Owner
    scope: str | None
    check: str
    code: str | None
    file: str | None
    message: str
    members: int
    state: ItemState
    claimant: str | None
    expires_at: datetime | None
    last_outcome: GroupOutcome | None
    command: str | None


@dataclass(frozen=True, slots=True)
class DetectedState:
    """Live detection joined with the recorded decisions and the last pass."""

    groups: tuple[FindingGroup, ...]
    states: dict[str, DecisionState]
    decisions: Decisions
    last_pass: dict | None

    @property
    def brain_groups(self) -> tuple[FindingGroup, ...]:
        return tuple(group for group in self.groups if group.owner is Owner.BRAIN)

    def group(self, key: str) -> FindingGroup | None:
        return next((group for group in self.brain_groups if group.key == key), None)


def never_held() -> frozenset[str]:
    from _repair_common import NEVER_HELD

    return NEVER_HELD


def read_decisions_or_error(request_type, paths: MaintenancePaths, correlation_id: str):
    """Return ``(decisions, None)`` or ``(None, error)`` naming the unreadable path."""
    from ..results import CommandError, Error, InternalErrorDetails

    try:
        return read_decisions(paths.decisions), None
    except DecisionsUnreadable as exc:
        error = Error(
            request_type.COMMAND_ID,
            request_type.COMMAND_VERSION,
            CommandError(ErrorCode.INTERNAL_ERROR, str(exc), InternalErrorDetails(correlation_id)),
        )
        return None, error


def detect_or_error(request_type, context):
    """Detect in process, mapping ``run_checks`` failures as ``vault.check`` does."""
    try:
        return detect(context), None
    except DetectionFailed as exc:
        return None, request_error(request_type, ErrorCode.CONFLICT, str(exc))


def join_state(findings, decisions: Decisions, now: datetime, last_pass: dict | None) -> DetectedState:
    groups = group_by_family(findings)
    return DetectedState(groups, apply_decisions(groups, decisions, now, never_held=never_held()), decisions, last_pass)


def load_state(request_type, context):
    """Shared read path for the commands that do not take the pass lock."""
    paths = brain_paths(context.selected_brain.vault_root)
    decisions, error = read_decisions_or_error(request_type, paths, context.correlation_id)
    if error is not None:
        return None, error
    findings, error = detect_or_error(request_type, context)
    if error is not None:
        return None, error
    return join_state(findings, decisions, context.clock.now(), read_last_pass(paths.last_pass)), None


def last_outcome_for(group: FindingGroup, last_pass: dict | None) -> GroupOutcome | None:
    if last_pass is None or group.scope is None or group.scope not in last_pass["groups"]:
        return None
    return GroupOutcome(last_pass["groups"][group.scope])


def guidance_for(group: FindingGroup, vault_root: Path) -> str | None:
    from _repair_common import REPAIR_SCOPES, build_catalogue_command

    if group.owner is Owner.MACHINE:
        return MACHINE_GUIDANCE
    if group.scope is not None:
        return build_catalogue_command(vault_root, REPAIR_SCOPES[group.scope])
    return None


def describe(group: FindingGroup, state: DecisionState, *, vault_root: Path,
             last_outcome: GroupOutcome | None) -> MaintenanceItem:
    return MaintenanceItem(
        key=group.key,
        fingerprint=group.fingerprint,
        kind="family" if group.is_family else "finding",
        disposition=group.disposition,
        owner=group.owner,
        scope=group.scope,
        check=group.check,
        code=group.code,
        file=group.file,
        message=group.message,
        members=len(group.members),
        state=state.state,
        claimant=state.claimant,
        expires_at=state.expires_at,
        last_outcome=last_outcome,
        command=guidance_for(group, vault_root),
    )
