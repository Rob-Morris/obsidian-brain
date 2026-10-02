"""Typed ``maintenance.run`` owner: the selected Brain's maintenance pass (D3, D5, D7, D8)."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import ClassVar, Mapping
import uuid

from _bootstrap.file_lock import MutationLockError, exclusive_file_lock
from _bootstrap.maintenance_decisions import ItemState
from _bootstrap.maintenance_findings import Disposition, FindingGroup, Owner, family_key
from _bootstrap.maintenance_summary import (
    SETTLED,
    UNSUCCESSFUL,
    WITHHELD,
    Counts,
    GroupOutcome,
    MaintenancePaths,
    brain_paths,
    build_summary,
    classify_outcome,
    count_groups,
    empty_counts,
    host_name,
    read_last_pass,
    write_last_pass,
)

from .._decoding import decode_empty
from .._mutation_support import MAINTENANCE_MCP_EXCLUSION
from ..context import InvocationContext, record_safely, report_failure_safely
from ..preparation import OperationPreparation, admit_owner, bind_operation
from ..receipts import CommittedEffect, OutcomeReference
from ..results import (
    CapabilityUnavailableDetails,
    CommandError,
    CommandWarning,
    Error,
    ErrorCode,
    InstructionNextAction,
    InternalErrorDetails,
    Ok,
    OutcomeUnknownDetails,
    Partial,
    RequestErrorDetails,
    WarningCode,
    request_error,
)
from ..types import (
    Authority,
    DependencyTier,
    EffectClass,
    InitialAuthorisationClass,
    Locality,
    Projection,
    RetryClass,
)
from ._detection import NAMESPACE, semantic_retrieval_configured
from ._items import (
    DetectedState,
    MaintenanceItem,
    describe,
    detect_or_error,
    join_state,
    read_decisions_or_error,
)


PASS_CLEARED_EMBEDDINGS = "pass_cleared_embeddings"
SUMMARY_EFFECT_KIND = "maintenance.summary"


@dataclass(frozen=True, slots=True)
class GroupResult:
    scope: str
    outcome: GroupOutcome
    invocation_id: str | None
    error_code: str | None


@dataclass(frozen=True, slots=True)
class MaintenanceRunPayload:
    pass_id: str
    host: str
    dry_run: bool
    outcome: str
    groups: tuple[GroupResult, ...]
    planned: tuple[str, ...]
    counts: Counts
    attention: tuple[MaintenanceItem, ...]
    semantic_degraded: bool


@dataclass(frozen=True, slots=True)
class MaintenanceRunRequest:
    COMMAND_ID: ClassVar[str] = "maintenance.run"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = MaintenanceRunPayload


def pass_binding(context, request, *, frozen_inputs=None):
    """Static over the request: a prepared pass never fails on detection drift."""
    from _repair_common import AUTOMATIC_SCOPES

    del context
    return bind_operation(request, frozen_inputs=frozen_inputs, review={
        "operation": "Run the selected Brain's maintenance pass: detect, repair automatic families, list the rest.",
        "automatic_families": list(AUTOMATIC_SCOPES),
    })


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _no_invoker(request, context: InvocationContext):
    root = context.selected_brain.vault_root
    return Error(
        request.COMMAND_ID, request.COMMAND_VERSION,
        CommandError(
            ErrorCode.CAPABILITY_UNAVAILABLE,
            "The maintenance pass runs only from a standalone, keyless script context.",
            CapabilityUnavailableDetails(
                DependencyTier.PORTABLE, context.dependency_tier, Locality.SELECTED_BRAIN_LOCAL,
                ("provider:maintenance_invoker",), context.capabilities.freshness, True,
            ),
            InstructionNextAction(
                f"Run `brain --vault {root} maintenance run` or "
                f"`command.py maintenance run --vault {root}` without an operator key, "
                "outside MCP and outside a brain session run job."
            ),
        ),
    )


def classify_result(result) -> tuple[GroupOutcome, str | None]:
    """One closed outcome per sibling result (D3)."""
    error = getattr(result, "error", None)
    code = error.code.value if error is not None else None
    outcome = classify_outcome(
        result.status,
        committed=bool(getattr(result, "committed_effects", ())),
        code=code,
        retryable=getattr(result, "retryable", False),
    )
    return outcome, code


def execute(context: InvocationContext, request: MaintenanceRunRequest):
    if context.maintenance is None:
        return _no_invoker(request, context)
    paths = brain_paths(context.selected_brain.vault_root)
    try:
        with exclusive_file_lock(paths.pass_lock, timeout=0, follow_symlinks=False):
            return _Pass(context, request, paths).run()
    except MutationLockError:
        return request_error(type(request), ErrorCode.CONFLICT,
                             "A maintenance pass is already running for this Brain.", retryable=True)


class _Pass:
    """One pass: prepare, invoke, summarise. Steps 1 to 6 of D3 in order."""

    def __init__(self, context: InvocationContext, request: MaintenanceRunRequest, paths: MaintenancePaths) -> None:
        self.context = context
        self.request = request
        self.paths = paths
        self.started = time.monotonic()
        self.pass_id = uuid.uuid4().hex[:12]
        self.host = host_name()
        self.root = context.selected_brain.vault_root
        self.warnings: list[CommandWarning] = []

    # -- step 1 and 2: admit, read decisions, detect ------------------------

    def run(self):
        admit_owner(self.context, self.request, pass_binding)
        record_safely(self.context, "maintenance.pass_started", family="maintenance",
                      pass_id=self.pass_id, dry_run=self.context.dry_run)
        state, error = self._prepare()
        if error is not None:
            return error
        results, effects = self._invoke(state)
        return self._finish(state, results, effects)

    def _prepare(self) -> tuple[DetectedState | None, Error | None]:
        decisions, error = read_decisions_or_error(MaintenanceRunRequest, self.paths, self.context.correlation_id)
        if error is not None:
            return None, self._blocked("decisions_unreadable", error)
        try:
            findings, error = detect_or_error(MaintenanceRunRequest, self.context)
        except Exception as exc:  # detection has no effects: a crash is a block, never an unknown outcome
            report_failure_safely(self.context, phase="execute", command_id=self.request.COMMAND_ID, error=exc)
            findings, error = None, Error(
                self.request.COMMAND_ID, self.request.COMMAND_VERSION,
                CommandError(ErrorCode.INTERNAL_ERROR, "Maintenance detection failed unexpectedly.",
                             InternalErrorDetails(self.context.correlation_id)))
        if error is not None:
            return None, self._blocked("detection_failed", error)
        previous = read_last_pass(self.paths.last_pass)
        return join_state(findings, decisions, self.context.clock.now(), previous), None

    def _blocked(self, reason: str, error: Error) -> Error:
        """Leave a blocked summary so the advisory never shows a stale count as current."""
        if not self.context.dry_run:
            summary = build_summary(pass_id=self.pass_id, host=self.host, finished_at=self.context.clock.now(),
                                    outcome="error", groups={}, counts=empty_counts(), blocked=reason)
            try:
                write_last_pass(self.paths.last_pass, summary)
            except OSError as exc:
                self.warnings.append(CommandWarning(
                    WarningCode.FOLLOW_UP_REQUIRED, f"could not write the blocked last-pass summary: {exc}"))
        record_safely(self.context, "maintenance.pass_finished", family="maintenance", pass_id=self.pass_id,
                      outcome="error", **empty_counts(), duration_ms=_elapsed_ms(self.started))
        return Error(error.command_id, error.command_version, error.error,
                     retryable=error.retryable, warnings=tuple(self.warnings))

    # -- step 3 and 4: plan and invoke the automatic groups ------------------

    def _ordered(self, state: DetectedState) -> list[FindingGroup]:
        from _repair_common import AUTOMATIC_SCOPES

        automatic = {group.scope: group for group in state.brain_groups if group.disposition is Disposition.AUTOMATIC}
        return [automatic[scope] for scope in AUTOMATIC_SCOPES if scope in automatic]

    def _planned(self, state: DetectedState) -> list[FindingGroup]:
        return [group for group in self._ordered(state) if state.states[group.key].state not in WITHHELD]

    def _invoke(self, state: DetectedState) -> tuple[list[GroupResult], list[CommittedEffect]]:
        from _repair_common import REPAIR_SCOPES

        results: list[GroupResult] = []
        effects: list[CommittedEffect] = []
        if self.context.dry_run:
            return results, effects
        defer_remaining = False
        for group in self._planned(state):
            if defer_remaining:
                results.append(GroupResult(group.scope, GroupOutcome.DEFERRED, None, None))
                continue
            invocation_id = f"maint-{self.pass_id}-{group.scope}"
            repair_started = time.monotonic()
            result = self.context.maintenance.repair(REPAIR_SCOPES[group.scope], invocation_id=invocation_id)
            outcome, error_code = classify_result(result)
            effects.extend(getattr(result, "committed_effects", ()))
            record_safely(self.context, "maintenance.repair_invoked", family="maintenance", pass_id=self.pass_id,
                          command_id=result.command_id, invocation_id=invocation_id, outcome=outcome.value,
                          error_code=error_code, duration_ms=_elapsed_ms(repair_started))
            results.append(GroupResult(group.scope, outcome, invocation_id, error_code))
            if outcome is GroupOutcome.DEFERRED:
                defer_remaining = True
        return results, effects

    # -- step 5 and 6: list the rest, write the summary, log ----------------

    def _finish(self, state: DetectedState, results: list[GroupResult], effects: list[CommittedEffect]):
        from _repair_common import REPAIR_SCOPES

        outcomes = {item.scope: item.outcome for item in results}
        semantic_degraded = (
            any(outcomes.get(family.scope) in {GroupOutcome.REPAIRED, GroupOutcome.PARTIAL}
                for family in REPAIR_SCOPES.values() if family.clears_embeddings)
            and semantic_retrieval_configured(self.root)
        )
        attention, extra = self._attention(state, outcomes, semantic_degraded=semantic_degraded)
        counts = count_groups(state.brain_groups, state.states, lambda group: outcomes.get(group.scope),
                              extra_needs_person=extra)
        unknown = next((item for item in results if item.outcome is GroupOutcome.UNKNOWN), None)
        pass_outcome = (
            "error" if unknown is not None
            else "partial" if any(item.outcome in UNSUCCESSFUL for item in results)
            else "ok"
        )
        if not self.context.dry_run:
            pass_outcome = self._write_summary(state, results, counts, pass_outcome, effects)
        record_safely(self.context, "maintenance.pass_finished", family="maintenance", pass_id=self.pass_id,
                      outcome=pass_outcome, **counts.as_mapping(), duration_ms=_elapsed_ms(self.started))
        payload = MaintenanceRunPayload(
            self.pass_id, self.host, self.context.dry_run, pass_outcome, tuple(results),
            tuple(group.scope for group in self._planned(state)), counts, attention, semantic_degraded,
        )
        if unknown is not None:
            return self._unknown(unknown, effects)
        if pass_outcome == "partial":
            failed = [item for item in results if item.outcome in UNSUCCESSFUL]
            message = (
                "Repairs did not all complete: " + ", ".join(f"{item.scope} {item.outcome.value}" for item in failed)
                if failed else self.warnings[-1].message
            )
            return Partial(self.request.COMMAND_ID, self.request.COMMAND_VERSION,
                           CommandError(ErrorCode.CONFLICT, message, RequestErrorDetails(None, message)),
                           tuple(effects), tuple(self.warnings))
        return Ok(self.request.COMMAND_ID, self.request.COMMAND_VERSION, payload, tuple(effects), tuple(self.warnings))

    def _write_summary(self, state: DetectedState, results, counts: Counts, pass_outcome: str,
                       effects: list[CommittedEffect]) -> str:
        """Write ``last-pass``; a write failure after commits is a warning, never an error."""
        previous = state.last_pass
        if previous is not None and previous["host"] != self.host:
            self.warnings.append(CommandWarning(
                WarningCode.FOLLOW_UP_REQUIRED,
                f"The previous pass ran on host {previous['host']!r}; this one on {self.host!r}. "
                "Passes for one Brain should run from one host: .brain/local may be synced and its locks are host-local.",
            ))
        summary = build_summary(
            pass_id=self.pass_id, host=self.host, finished_at=self.context.clock.now(), outcome=pass_outcome,
            groups={item.scope: item.outcome.value for item in results}, counts=counts.as_mapping(),
        )
        try:
            write_last_pass(self.paths.last_pass, summary)
            effects.append(CommittedEffect(SUMMARY_EFFECT_KIND, self.pass_id))
        except OSError as exc:
            self.warnings.append(CommandWarning(
                WarningCode.FOLLOW_UP_REQUIRED, f"could not write the last-pass summary: {exc}"))
            # Unknown outranks everything; otherwise a lost summary is a partial pass
            # because the siblings' effects stand, even when they are none.
            if pass_outcome == "ok":
                return "partial"
        return pass_outcome

    def _unknown(self, unknown: GroupResult, effects: list[CommittedEffect]) -> Error:
        reference = OutcomeReference(unknown.invocation_id)
        committed = ", ".join(f"{effect.kind}:{effect.subject}" for effect in effects) or "none"
        return Error(
            self.request.COMMAND_ID, self.request.COMMAND_VERSION,
            CommandError(
                ErrorCode.COMMAND_OUTCOME_UNKNOWN,
                f"Repair {unknown.scope} ({unknown.invocation_id}) ended with unknown effects; "
                f"effects known to have committed: {committed}. The next pass re-detects.",
                OutcomeUnknownDetails(reference),
            ),
            effects="unknown", outcome_reference=reference, warnings=tuple(self.warnings),
        )

    def _attention(self, state: DetectedState, outcomes: Mapping[str, GroupOutcome], *, semantic_degraded: bool):
        """Everything the pass leaves for a person, plus the semantic degradation it caused."""
        from _repair_common import REPAIR_SCOPES, build_catalogue_command

        items = []
        semantic_listed = False
        for group in state.groups:
            decision = state.states[group.key]
            if group.owner is Owner.MACHINE:
                listed = True
            elif group.disposition is Disposition.AUTOMATIC:
                listed = decision.state in WITHHELD or outcomes.get(group.scope) not in SETTLED | {None}
            else:
                listed = decision.state is not ItemState.QUIET
            if not listed:
                continue
            semantic_listed = semantic_listed or group.scope == "semantic"
            items.append(describe(group, decision, vault_root=self.root, last_outcome=outcomes.get(group.scope)))
        extra = 0
        if semantic_degraded and not semantic_listed:
            family = REPAIR_SCOPES["semantic"]
            key = family_key(NAMESPACE, family.scope)
            items.append(MaintenanceItem(
                key=key, fingerprint=key, kind="family", disposition=family.disposition, owner=family.owner,
                scope=family.scope, check="semantic", code=PASS_CLEARED_EMBEDDINGS, file=None,
                message="A cache repair cleared the semantic embeddings; run the semantic repair to restore them.",
                members=0, state=ItemState.OPEN, claimant=None, expires_at=None,
                last_outcome=GroupOutcome.NEEDS_PERSON, command=build_catalogue_command(self.root, family),
            ))
            extra = 1
        return tuple(items), extra


def decode(payload: Mapping[str, object]) -> MaintenanceRunRequest:
    return decode_empty(payload, MaintenanceRunRequest)


def catalogue_entry():
    from ..catalogue import ALL_APPLICATION_PROJECTIONS, ApplicationEntry, exclude_projection

    entry = ApplicationEntry(
        initial_class=InitialAuthorisationClass.OBSERVATION,
        preparation=OperationPreparation(pass_binding),
        request_type=MaintenanceRunRequest,
        executor=execute,
        dependency_tier=DependencyTier.PORTABLE,
        locality=Locality.SELECTED_BRAIN_LOCAL,
        required_providers=(),
        optional_providers=(),
        authority=Authority.MAINTAINER,
        effect_class=EffectClass.DERIVED_CACHE_WRITE,
        retry_class=RetryClass.SAFE,
        projections=ALL_APPLICATION_PROJECTIONS,
        summary="Run one bounded maintenance pass: detect, repair automatic families, list the rest.",
    )
    return exclude_projection(entry, Projection.MCP, MAINTENANCE_MCP_EXCLUSION)
