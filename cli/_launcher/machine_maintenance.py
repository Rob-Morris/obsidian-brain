"""Typed ``machine-maintenance.*`` owners: the machine pass over Doctor's feed (DD-082, D19).

Same model as the Brain pass, over the same shared vocabulary: detection is
the source of truth, only families flagged automatic run, and the only
persistent state is human decisions in the machine decisions file beside the
launcher receipts. Because ``LocalAuthority.allows`` always returns true, the
automatic flag is the real gate, and no machine family passes the admission
test for it (DD-083): every kind is judgement, so a pass detects, lists and
writes its summary with no groups.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import ClassVar, Mapping
import uuid

from _bootstrap.file_lock import MutationLockError, exclusive_file_lock
from _bootstrap.maintenance_decisions import (
    DecisionState,
    Decisions,
    DecisionsUnreadable,
    ItemState,
    Refusal,
    apply_decisions,
    decide_claim,
    decide_dismiss,
    decide_release,
    locked_decisions,
    prune,
    read_decisions,
    validate_actor,
    validate_key,
    validate_reason,
    write_decisions,
)
from _bootstrap.maintenance_findings import (
    Disposition,
    FindingGroup,
    MaintenanceFinding,
    Owner,
    finding_key,
    group_by_family,
)
from _bootstrap.maintenance_summary import (
    SETTLED,
    SHOWN_AUTOMATIC,
    UNSUCCESSFUL,
    WITHHELD,
    Counts,
    GroupOutcome,
    LastPassSummary,
    MaintenancePaths,
    build_summary,
    classify_outcome,
    count_groups,
    empty_counts,
    host_name,
    read_last_pass,
    write_last_pass,
)
from _repair_common import REPAIR_SCOPES, RepairFamily, family_for_finding

from .context import LauncherContext, launcher_state_home, report_failure_safely
from .contracts import (
    CommandError,
    CommandWarning,
    CommittedEffect,
    Error,
    ErrorCode,
    Ok,
    OutcomeReference,
    OutcomeUnknownDetails,
    Partial,
    RequestErrorDetails,
    WarningCode,
    no_effect_error,
)


NAMESPACE = "machine"
SUMMARY_EFFECT_KIND = "machine-maintenance.summary"
BRAIN_REPAIR = "brain_repair"


def _machine(scope: str, command_id: str, description: str, disposition=Disposition.JUDGEMENT) -> RepairFamily:
    return RepairFamily(scope, command_id, {}, disposition, Owner.MACHINE, False, description)


# The machine repair table: one row per finding kind that a command repairs.
# ``brain_repair`` findings resolve through the Brain table's machine-owned
# scopes instead, so the two tables never disagree about them; kinds with no
# row (orphaned processes) are report-and-decide.
MACHINE_FAMILIES: Mapping[str, RepairFamily] = {
    "stale_vault_registry": _machine(
        "stale_vault_registry", "registry.remove-stale", "Remove stale entries from the vault registry."),
    "brain_unregistered": _machine(
        "brain_unregistered", "brain.register", "Register a discovered Brain in the vault registry."),
    "orphan_runtime": _machine(
        "orphan_runtime", "runtime.remove-orphans", "Remove orphaned shared managed runtimes."),
    "mcp_registration": _machine(
        "mcp_registration", "mcp.repair", "Repair drifted MCP client registrations."),
    "legacy_installation": _machine(
        "legacy_installation", "brain.migrate-legacy-installations", "Migrate a legacy Brain onto the shared runtime."),
}
AUTOMATIC_KINDS = tuple(kind for kind, family in MACHINE_FAMILIES.items()
                        if family.disposition is Disposition.AUTOMATIC)


def _family_for(group: FindingGroup) -> RepairFamily | None:
    if group.check == BRAIN_REPAIR:
        return REPAIR_SCOPES[group.code]
    return MACHINE_FAMILIES.get(group.check)


def machine_paths() -> MaintenancePaths:
    return MaintenancePaths.under(launcher_state_home() / "brain" / "maintenance")


def _request_payload(family: RepairFamily, subject: Mapping[str, object] | None) -> dict:
    """The request ``family``'s command takes for one finding: static, unless the row derives it."""
    payload = dict(family.request)
    if family.scope == "brain_unregistered":
        payload["vault_root"] = subject["path"]
    return payload


def launcher_request(family: RepairFamily, subject: Mapping[str, object] | None = None):
    """Resolve a family's request for ``subject`` through the launcher's own catalogue."""
    from .owners import LAUNCHER_OWNERS
    from .projection import resolve_request

    owner = next(item for item in LAUNCHER_OWNERS.entries if item.command_id == family.command_id)
    return resolve_request(owner.request_type, _request_payload(family, subject))


def launcher_guidance(family: RepairFamily, *, vault_root=None, subject: Mapping[str, object] | None = None) -> str:
    """The shell-ready launcher command that repairs ``family`` for ``subject``."""
    from launcher_catalogue import LAUNCHER_CATALOGUE

    from _common import join_argv

    entry = next(item for item in LAUNCHER_CATALOGUE.entries if item.command_id == family.command_id)
    binary, *rest = entry.entry_point
    argv = [binary]
    if vault_root is not None:
        argv.extend(["--vault", str(vault_root)])
    argv.extend(rest)
    payload = _request_payload(family, subject)
    if payload:
        argv.extend(["--request-json", json.dumps(payload, separators=(",", ":"), sort_keys=True)])
    return join_argv(argv)


# ---------------------------------------------------------------------------
# Detection over Doctor's feed
# ---------------------------------------------------------------------------

def _finding(kind: str, subject: Mapping[str, object], message: str, *, disposition: Disposition,
             evidence: Mapping[str, object] | None = None, severity: str = "warning",
             scope: str | None = None, code: str | None = None, file: str | None = None) -> MaintenanceFinding:
    return MaintenanceFinding(
        kind, severity, file, message, disposition, code=code, scope=scope, owner=Owner.MACHINE,
        key=finding_key(NAMESPACE, kind, subject), subject=subject, evidence=evidence,
    )


def detect_machine(context: LauncherContext) -> tuple[tuple[MaintenanceFinding, ...], bool]:
    """Classify Doctor's machine feed; returns the findings and whether the process scan ran.

    One process scan serves both live-runtime matching and orphan detection.
    """
    from _machine.maintenance import collect_machine_summary
    from _machine.topology import find_orphaned_brain_processes, scan_processes

    scan = scan_processes()
    summary = collect_machine_summary(
        current_vault=str(context.current_vault) if context.current_vault is not None else None,
        launcher_python=str(context.launcher_python) if context.launcher_python is not None else None,
        cli_binary=str(context.cli_binary),
        process_scan=scan,
    )
    findings: list[MaintenanceFinding] = []
    for entry in summary["stale_registry_entries"]:
        findings.append(_finding("stale_vault_registry", {"path": entry["path"]},
                                 f"Vault registry entry {entry['alias']!r} points at a path that is not a Brain.",
                                 disposition=Disposition.JUDGEMENT))
    for path in summary["unregistered_brains"]:
        findings.append(_finding("brain_unregistered", {"path": path},
                                 "Brain is not registered on this machine: no workspace can bind to it, --brain cannot "
                                 "select it, and it cannot verify its linked workspace registry.",
                                 disposition=Disposition.JUDGEMENT))
    for runtime in summary["runtimes"]:
        if runtime["orphan_candidate"]:
            findings.append(_finding("orphan_runtime", {"python": runtime["python"]},
                                     "Shared managed runtime is selected by no Brain and referenced by no registration.",
                                     disposition=Disposition.JUDGEMENT))
    for brain in summary["brains"]:
        if brain["runtime"]["status"] == "legacy_vault_venv":
            findings.append(_finding("legacy_installation", {"brain": brain["path"]},
                                     "Brain still falls back to its legacy vault-local .venv.", disposition=Disposition.JUDGEMENT))
        for item in brain["repair_findings"]:
            family = family_for_finding(item)
            if family is None or family.owner is not Owner.MACHINE:
                continue  # Brain-owned and family-less findings belong to that Brain's own pass or a person
            findings.append(_finding(BRAIN_REPAIR, {"brain": brain["path"], "scope": family.scope}, item["message"],
                                     disposition=Disposition.JUDGEMENT, scope=BRAIN_REPAIR, code=family.scope,
                                     file=item["check"], evidence={"brain": brain["path"]}))
    from _bootstrap.mcp_inventory import REPORTED_STATES

    for item in summary["mcp_registrations"]["registrations"]:
        # An unreachable folder is the owning Brain's finding (workspace_folder_unreachable), not a machine item.
        if item["state"] in REPORTED_STATES:
            continue
        # A Brain-level item (incomplete or migration_required) names a vault, not a client slot.
        subject = ({"path": item["path"], "client": item["client"], "scope": item["scope"]}
                   if "client" in item else {"path": item["path"]})
        findings.append(_finding("mcp_registration", subject, item.get("message") or f"MCP registration is {item['state']}.",
                                 disposition=Disposition.JUDGEMENT, evidence={"state": item["state"]}))
    processes = find_orphaned_brain_processes(scan=scan)
    for process in processes["processes"]:
        findings.append(_finding("orphaned_process", {"pid": process["pid"], "command": process["command"]},
                                 f"{process['role']} interpreter has lost its parent (pid {process['pid']}).",
                                 disposition=Disposition.JUDGEMENT))
    return tuple(findings), processes["available"]


# ---------------------------------------------------------------------------
# Typed payloads
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class MachineMaintenanceItem:
    key: str
    fingerprint: str
    kind: str
    disposition: Disposition
    subject: Mapping[str, object]
    message: str
    members: int
    state: ItemState
    claimant: str | None
    expires_at: str | None
    last_outcome: GroupOutcome | None
    command: str | None


@dataclass(frozen=True, slots=True)
class MachineGroupResult:
    kind: str
    outcome: GroupOutcome
    invocation_id: str | None
    error_code: str | None


@dataclass(frozen=True, slots=True)
class MachineMaintenanceRunPayload:
    pass_id: str
    host: str
    dry_run: bool
    outcome: str
    groups: tuple[MachineGroupResult, ...]
    planned: tuple[str, ...]
    counts: Counts
    attention: tuple[MachineMaintenanceItem, ...]
    process_scan_available: bool


@dataclass(frozen=True, slots=True)
class MachineMaintenanceListPayload:
    items: tuple[MachineMaintenanceItem, ...]
    hidden_quiet: int
    process_scan_available: bool
    last_pass: LastPassSummary | None


@dataclass(frozen=True, slots=True)
class MachineDecisionPayload:
    kind: str
    item: MachineMaintenanceItem


@dataclass(frozen=True, slots=True)
class MachineMaintenanceRunRequest:
    COMMAND_ID: ClassVar[str] = "machine-maintenance.run"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = MachineMaintenanceRunPayload


@dataclass(frozen=True, slots=True)
class MachineMaintenanceListRequest:
    COMMAND_ID: ClassVar[str] = "machine-maintenance.list"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = MachineMaintenanceListPayload

    all: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.all, bool):
            raise ValueError("machine-maintenance.list all must be a boolean")


@dataclass(frozen=True, slots=True)
class MachineMaintenanceClaimRequest:
    COMMAND_ID: ClassVar[str] = "machine-maintenance.claim"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = MachineDecisionPayload

    key: str
    claimant: str

    def __post_init__(self) -> None:
        validate_key(self.key)
        validate_actor(self.claimant, "claimant")


@dataclass(frozen=True, slots=True)
class MachineMaintenanceDismissRequest:
    COMMAND_ID: ClassVar[str] = "machine-maintenance.dismiss"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = MachineDecisionPayload

    key: str
    expected_fingerprint: str
    reason: str
    actor: str

    def __post_init__(self) -> None:
        validate_key(self.key)
        validate_key(self.expected_fingerprint, "expected_fingerprint")
        validate_reason(self.reason)
        validate_actor(self.actor, "actor")


@dataclass(frozen=True, slots=True)
class MachineMaintenanceReleaseRequest:
    COMMAND_ID: ClassVar[str] = "machine-maintenance.release"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = MachineDecisionPayload

    key: str
    actor: str

    def __post_init__(self) -> None:
        validate_key(self.key)
        validate_actor(self.actor, "actor")


# ---------------------------------------------------------------------------
# Shared mechanics
# ---------------------------------------------------------------------------

def _refused(request, refusal: Refusal) -> Error:
    return no_effect_error(type(request), ErrorCode(refusal.code), refusal.message, refusal.field)


def _guidance(group: FindingGroup) -> str | None:
    family = _family_for(group)
    if family is None:
        return None
    vault_root = group.subject["brain"] if group.check == BRAIN_REPAIR else None
    return launcher_guidance(family, vault_root=vault_root, subject=group.subject)


def _describe(group: FindingGroup, state: DecisionState, *, last_outcome: GroupOutcome | None) -> MachineMaintenanceItem:
    return MachineMaintenanceItem(
        key=group.key, fingerprint=group.fingerprint, kind=group.check, disposition=group.disposition,
        subject=dict(group.subject), message=group.message, members=len(group.members),
        state=state.state, claimant=state.claimant,
        expires_at=state.expires_at.isoformat() if state.expires_at is not None else None,
        last_outcome=last_outcome, command=_guidance(group),
    )


def _last_outcome(group: FindingGroup, last_pass: dict | None) -> GroupOutcome | None:
    if last_pass is None or group.check not in last_pass["groups"]:
        return None
    return GroupOutcome(last_pass["groups"][group.check])


def _classify(result) -> tuple[GroupOutcome, str | None]:
    error = getattr(result, "error", None)
    code = error.code.value if error is not None else None
    outcome = classify_outcome(
        result.status,
        committed=bool(getattr(result, "committed_effects", ())),
        code=code,
        retryable=getattr(result, "retryable", False),
        needs_person_codes=frozenset({ErrorCode.AUTHORITY_DENIED.value}),
    )
    return outcome, code


@dataclass(frozen=True, slots=True)
class _State:
    groups: tuple[FindingGroup, ...]
    states: dict
    decisions: Decisions
    last_pass: dict | None
    scan_available: bool


def _load_state(request, context: LauncherContext, paths: MaintenancePaths):
    """Read decisions and detect; a detection failure names its block reason."""
    import vault_registry

    try:
        decisions = read_decisions(paths.decisions)
    except DecisionsUnreadable as exc:
        return None, ("decisions_unreadable", no_effect_error(type(request), ErrorCode.INTERNAL_ERROR, str(exc)))
    try:
        findings, scan_available = detect_machine(context)
    except (vault_registry.RegistryReadError, OSError, ValueError) as exc:
        return None, ("detection_failed",
                      no_effect_error(type(request), ErrorCode.CONFLICT, f"machine detection failed: {exc}"))
    except Exception as exc:  # detection has no effects: a crash is a block, never an unknown outcome
        report_failure_safely(context, phase="execute", command_id=request.COMMAND_ID, error=exc)
        return None, ("detection_failed",
                      no_effect_error(type(request), ErrorCode.INTERNAL_ERROR, "Machine detection failed unexpectedly."))
    groups = group_by_family(findings)
    states = apply_decisions(groups, decisions, context.clock.now())
    return _State(groups, states, decisions, read_last_pass(paths.last_pass), scan_available), None


# ---------------------------------------------------------------------------
# The pass
# ---------------------------------------------------------------------------

def execute_run(context: LauncherContext, request: MachineMaintenanceRunRequest):
    paths = machine_paths()
    try:
        with exclusive_file_lock(paths.pass_lock, timeout=0, follow_symlinks=False):
            return _run(context, request, paths)
    except MutationLockError:
        return no_effect_error(type(request), ErrorCode.CONFLICT, "A machine maintenance pass is already running.",
                               retryable=True)


def _run(context: LauncherContext, request: MachineMaintenanceRunRequest, paths: MaintenancePaths):
    from .nested_invocation import invoke_sibling

    pass_id = uuid.uuid4().hex[:12]
    host = host_name()
    warnings: list[CommandWarning] = []

    state, blocked = _load_state(request, context, paths)
    if blocked is not None:
        reason, error = blocked
        if not context.dry_run:
            summary = build_summary(pass_id=pass_id, host=host, finished_at=context.clock.now(), outcome="error",
                                    groups={}, counts=empty_counts(), blocked=reason)
            try:
                write_last_pass(paths.last_pass, summary)
            except OSError as exc:
                warnings.append(CommandWarning(WarningCode.FOLLOW_UP_REQUIRED,
                                               f"could not write the blocked machine last-pass summary: {exc}"))
        return Error(error.command_id, error.command_version, error.error, warnings=tuple(warnings))

    automatic = {group.check: group for group in state.groups if group.disposition is Disposition.AUTOMATIC}
    ordered = [automatic[kind] for kind in AUTOMATIC_KINDS if kind in automatic]
    planned = [group for group in ordered if state.states[group.key].state not in WITHHELD]
    results: list[MachineGroupResult] = []
    effects: list[CommittedEffect] = []
    if not context.dry_run:
        defer_remaining = False
        for group in planned:
            if defer_remaining:
                results.append(MachineGroupResult(group.check, GroupOutcome.DEFERRED, None, None))
                continue
            invocation_id = f"{context.invocation_id}-{pass_id}-{group.check}"
            result = invoke_sibling(context, launcher_request(_family_for(group), group.subject), invocation_id=invocation_id)
            outcome, error_code = _classify(result)
            effects.extend(getattr(result, "committed_effects", ()))
            results.append(MachineGroupResult(group.check, outcome, invocation_id, error_code))
            if outcome is GroupOutcome.DEFERRED:
                defer_remaining = True
    outcomes = {item.kind: item.outcome for item in results}
    attention = tuple(
        _describe(group, state.states[group.key], last_outcome=outcomes.get(group.check))
        for group in state.groups
        if (group.disposition is Disposition.AUTOMATIC
            and (state.states[group.key].state in WITHHELD or outcomes.get(group.check) not in SETTLED | {None}))
        or (group.disposition is Disposition.JUDGEMENT and state.states[group.key].state is not ItemState.QUIET)
    )
    counts = count_groups(state.groups, state.states, lambda group: outcomes.get(group.check))
    unknown = next((item for item in results if item.outcome is GroupOutcome.UNKNOWN), None)
    pass_outcome = (
        "error" if unknown is not None
        else "partial" if any(item.outcome in UNSUCCESSFUL for item in results)
        else "ok"
    )
    if not context.dry_run:
        if state.last_pass is not None and state.last_pass["host"] != host:
            warnings.append(CommandWarning(WarningCode.FOLLOW_UP_REQUIRED,
                                           f"The previous machine pass ran on host {state.last_pass['host']!r}; this one on {host!r}."))
        summary = build_summary(
            pass_id=pass_id, host=host, finished_at=context.clock.now(), outcome=pass_outcome,
            groups={item.kind: item.outcome.value for item in results}, counts=counts.as_mapping(),
        )
        try:
            write_last_pass(paths.last_pass, summary)
            effects.append(CommittedEffect(SUMMARY_EFFECT_KIND, pass_id))
        except OSError as exc:
            warnings.append(CommandWarning(WarningCode.FOLLOW_UP_REQUIRED,
                                           f"could not write the machine last-pass summary: {exc}"))
            if pass_outcome == "ok":
                pass_outcome = "partial"
    payload = MachineMaintenanceRunPayload(pass_id, host, context.dry_run, pass_outcome, tuple(results),
                                           tuple(group.check for group in planned), counts, attention, state.scan_available)
    if unknown is not None:
        reference = OutcomeReference(unknown.invocation_id)
        committed = ", ".join(f"{effect.kind}:{effect.subject}" for effect in effects) or "none"
        return Error(
            request.COMMAND_ID, request.COMMAND_VERSION,
            CommandError(
                ErrorCode.COMMAND_OUTCOME_UNKNOWN,
                f"Machine repair {unknown.kind} ({unknown.invocation_id}) ended with unknown effects; "
                f"effects known to have committed: {committed}. The next pass re-detects.",
                OutcomeUnknownDetails(reference),
            ),
            effects="unknown", outcome_reference=reference, warnings=tuple(warnings),
        )
    if pass_outcome == "partial":
        failed = [item for item in results if item.outcome in UNSUCCESSFUL]
        message = ("Machine repairs did not all complete: " + ", ".join(f"{item.kind} {item.outcome.value}" for item in failed)
                   if failed else warnings[-1].message)
        return Partial(request.COMMAND_ID, request.COMMAND_VERSION,
                       CommandError(ErrorCode.CONFLICT, message, RequestErrorDetails(None, message)),
                       tuple(effects), tuple(warnings))
    return Ok(request.COMMAND_ID, request.COMMAND_VERSION, payload, tuple(effects), tuple(warnings))


# ---------------------------------------------------------------------------
# list and decisions
# ---------------------------------------------------------------------------

def execute_list(context: LauncherContext, request: MachineMaintenanceListRequest):
    state, blocked = _load_state(request, context, machine_paths())
    if blocked is not None:
        return blocked[1]
    items, hidden = [], 0
    for group in state.groups:
        decision = state.states[group.key]
        item = _describe(group, decision, last_outcome=_last_outcome(group, state.last_pass))
        if group.disposition is Disposition.AUTOMATIC:
            if request.all or decision.state is ItemState.CLAIM_EXPIRED or item.last_outcome in SHOWN_AUTOMATIC:
                items.append(item)
        elif decision.state is ItemState.QUIET and not request.all:
            hidden += 1
        else:
            items.append(item)
    last_pass = None if state.last_pass is None else LastPassSummary.from_document(state.last_pass)
    return Ok(request.COMMAND_ID, request.COMMAND_VERSION,
              MachineMaintenanceListPayload(tuple(items), hidden, state.scan_available, last_pass))


def _apply_decision(context: LauncherContext, request, *, kind: str, decide):
    paths = machine_paths()
    state, blocked = _load_state(request, context, paths)
    if blocked is not None:
        return blocked[1]
    group = next((item for item in state.groups if item.key == request.key), None)
    if group is None:
        return no_effect_error(type(request), ErrorCode.NOT_FOUND,
                               "No detected machine maintenance finding has that key; run brain machine-maintenance list.", "key")
    try:
        with locked_decisions(paths.decisions_lock):
            try:
                decisions = read_decisions(paths.decisions)
            except DecisionsUnreadable as exc:
                return no_effect_error(type(request), ErrorCode.INTERNAL_ERROR, str(exc))
            now = context.clock.now()
            decisions = prune(decisions, now)
            updated, refusal = decide(group, decisions, now)
            if refusal is not None:
                return _refused(request, refusal)
            if not context.dry_run:
                write_decisions(paths.decisions, updated)
    except MutationLockError as exc:
        return no_effect_error(type(request), ErrorCode.CONFLICT, f"The machine decisions file is busy; retry. {exc}",
                               retryable=True)
    final = updated if not context.dry_run else decisions
    decision = apply_decisions((group,), final, now)[group.key]
    effects = () if context.dry_run else (CommittedEffect(request.COMMAND_ID, f"file:{paths.decisions}"),)
    item = _describe(group, decision, last_outcome=_last_outcome(group, state.last_pass))
    return Ok(request.COMMAND_ID, request.COMMAND_VERSION, MachineDecisionPayload(kind, item), effects)


def execute_claim(context: LauncherContext, request: MachineMaintenanceClaimRequest):
    def decide(group, decisions, now):
        return decide_claim(group, decisions, now, claimant=request.claimant)

    return _apply_decision(context, request, kind="claim", decide=decide)


def execute_dismiss(context: LauncherContext, request: MachineMaintenanceDismissRequest):
    def decide(group, decisions, now):
        return decide_dismiss(group, decisions, now, actor=request.actor, reason=request.reason,
                              expected_fingerprint=request.expected_fingerprint)

    return _apply_decision(context, request, kind="dismiss", decide=decide)


def execute_release(context: LauncherContext, request: MachineMaintenanceReleaseRequest):
    def decide(group, decisions, now):
        return decide_release(group, decisions, now, actor=request.actor)

    return _apply_decision(context, request, kind="release", decide=decide)


def _owner(request_type, result_type, ref: str, executor):
    from .owners import LauncherOwner

    return LauncherOwner(request_type, result_type, ref, executor)


def run_owner():
    return _owner(MachineMaintenanceRunRequest, MachineMaintenanceRunPayload, "_launcher.machine_maintenance:run", execute_run)


def list_owner():
    return _owner(MachineMaintenanceListRequest, MachineMaintenanceListPayload, "_launcher.machine_maintenance:list", execute_list)


def claim_owner():
    return _owner(MachineMaintenanceClaimRequest, MachineDecisionPayload, "_launcher.machine_maintenance:claim", execute_claim)


def dismiss_owner():
    return _owner(MachineMaintenanceDismissRequest, MachineDecisionPayload, "_launcher.machine_maintenance:dismiss", execute_dismiss)


def release_owner():
    return _owner(MachineMaintenanceReleaseRequest, MachineDecisionPayload, "_launcher.machine_maintenance:release", execute_release)
