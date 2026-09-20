"""Reuse approval reconciliation around supported registered-target transitions."""

from __future__ import annotations

from dataclasses import replace
from contextlib import ExitStack
import hashlib
import json
import os
import uuid
from pathlib import Path

from _bootstrap import mcp_registration
from _bootstrap.file_transaction import FilePlan, _refuse_symlink_path
from _bootstrap.file_lock import exclusive_file_lock

from . import approval_management as manager
from .contracts import (CommittedEffect, CommandError, Error, ErrorCode, InstructionNextAction,
                        Ok, Partial, RecoveryRequiredDetails, no_effect_error)


def transition_path(home):
    return manager.ledger_path(home).with_name("client-approvals.transitions.json")


def active_transitions(plan, home):
    path = transition_path(home)
    raw = plan.read_text(path)
    if raw is None:
        return {}
    value = manager._object(raw, path)
    if value.get("schema") != "brain.approval-transitions/1" or not isinstance(value.get("active"), dict):
        raise ValueError(f"Invalid approval transition evidence: {path}")
    if any(not isinstance(pid, int) or isinstance(pid, bool) or pid < 1 for pid in value["active"].values()):
        raise ValueError(f"Invalid approval transition process evidence: {path}")
    return value["active"]


def _save_active(plan, home, active):
    path = transition_path(home)
    if active:
        plan.write_text(path, json.dumps({"schema": "brain.approval-transitions/1", "active": active}) + "\n")
    elif plan.read_bytes(path) is not None:
        plan.delete(path)


def operation_lock(home, identity):
    name = hashlib.sha256(identity.encode()).hexdigest() + ".lock"
    return manager.ledger_path(home).parent / "approval-transition-locks" / name


def _future(context, request, kind, roots):
    roots, overrides = set(roots), {}
    if kind == "add":
        roots.add(request.vault_root)
    elif kind == "remove":
        roots.discard(getattr(request, "vault_root", context.current_vault))
    elif kind in {"install", "version"}:
        target = request.vault_root if kind == "install" else context.current_vault
        if context.distribution_root is None or target is None:
            raise ValueError("approval transition requires the checked install/upgrade distribution")
        roots.add(target)
        overrides[target] = context.distribution_root / "src/brain-core"
    elif kind == "prune":
        import vault_registry
        roots = {root for root in roots if vault_registry.is_vault_root(root)}
    return tuple(sorted(roots, key=str)), overrides


def reconcile_records(plan, context, records, roots, *, overrides=None, tighten=False, removed_roots=(), transport_target=None):
    results = []
    for identity, record in list(records.items()):
        selection = manager.record_selection(record, context.home_dir)
        if selection.executable != context.cli_binary:
            raise ValueError("Managed approval executable was retargeted; explicit adoption is required")
        target = Path(record["target"]) if record["target"] is not None else None
        removed = (selection.surface == "mcp" and record.get("owner") is not None
                   and (record["owner"] in {str(root) for root in removed_roots}
                        or Path(record["owner"]) not in roots))
        relevant = () if removed else manager._target_roots(plan, selection, target, roots, context.home_dir)
        if (not removed and transport_target is not None and selection.surface == "mcp"
                and selection.scope != "user" and target == transport_target[0]):
            relevant = tuple(sorted(set(relevant) | {transport_target[1]}, key=str))
        updated, result = manager._stage_target(plan, selection, target, relevant, record, action="remove" if removed else "repair",
                                                overrides=overrides, tighten=tighten, launcher_root=context.distribution_root)
        if removed and tighten and updated is None:
            # Tightening may precede a failed owner operation. Retire intent only
            # after actual inventory proves removal; otherwise post-phase repairs it.
            updated = {**record, "owned": {}, "exclusions": [], "contracts": [], "legacy": {}}
        if updated is None:
            records.pop(identity, None)
        else:
            records[identity] = updated
        results.append(result)
    manager._save_records(plan, context.home_dir, records)
    return results


def invoke(context, request, kind, execute):
    """Tighten before exposure; defer expansion until all active transitions finish."""
    if context.dry_run:
        return _invoke(context, request, kind, execute)
    lock = operation_lock(context.home_dir, context.invocation_id)
    _refuse_symlink_path(lock)
    with exclusive_file_lock(lock, timeout=0, follow_symlinks=False):
        result = _invoke(context, request, kind, execute)
    if context.invocation_id not in active_transitions(FilePlan(), context.home_dir):
        lock.unlink(missing_ok=True)
    return configure_install(context, request, result) if kind == "install" else result


def configure_install(context, request, result):
    """The opt-in follows target commit; it never determines scaffold success."""
    if request.approval_client is None or context.dry_run or not isinstance(result, (Ok, Partial)):
        return result
    from .approvals import ApprovalsConfigureRequest, ApprovalScope, execute_configure
    selected = replace(context, current_vault=request.vault_root,
                       workspace_dir=None if request.approval_scope is ApprovalScope.USER else request.vault_root)
    outcome = execute_configure(selected, ApprovalsConfigureRequest(
        request.approval_client, request.approval_scope, request.approval_surfaces))
    effects = tuple(CommittedEffect(request.COMMAND_ID, e.subject) for e in getattr(outcome, "committed_effects", ()))
    if isinstance(outcome, Ok) and outcome.result.complete:
        return replace(result, committed_effects=(*result.committed_effects, *effects))
    message = "Brain scaffold outcome retained; client approvals need follow-up: " + (
        "; ".join(f"{t.client}/{t.surface}: {t.state} {t.activation}" for t in outcome.result.targets)
        if isinstance(outcome, Ok) else outcome.error.message)
    return Partial(request.COMMAND_ID, request.COMMAND_VERSION,
                   CommandError(ErrorCode.CONFLICT, message), (*result.committed_effects, *effects))


def _invoke(context, request, kind, execute):
    effects = []
    home = context.home_dir
    executing = False
    try:
        with mcp_registration.registration_lock(home):
            plan = FilePlan()
            records = manager.read_records(plan, home)
            if FilePlan().read_bytes(manager.journal_path(home)) is not None:
                raise ValueError("Recover the interrupted approval transaction before changing registered targets")
            roots = ((manager.mcp_inventory.local_brains(plan, context.current_vault, allow_missing=True)
                      if kind == "prune" else manager.inventory(plan, context)) if records else ())
            future, overrides = _future(context, request, kind, roots) if records else ((), {})
            if records and kind == "inventory":
                from .machine import prepare_legacy_migration, execute_prepared_legacy_migration, _selector_value
                from _machine.maintenance import _select_legacy_targets
                summary = prepare_legacy_migration(context)
                selected = _select_legacy_targets(summary, _selector_value(request.target), dry_run=True)
                future = tuple(sorted(set(future) | {Path(item["path"]).resolve() for item in selected.targets}, key=str))
                execute = lambda ctx, req: execute_prepared_legacy_migration(ctx, req, summary)
            active = active_transitions(plan, home)
            # Do not hold the registration lock across child CLI processes used by upgrade.
            # The durable marker prevents other writers from widening policy in that gap.
            results = reconcile_records(plan, context, records, future, overrides=overrides, tighten=True,
                                        removed_roots=tuple(root for root in roots if root not in future),
                                        transport_target=(context.workspace_dir or context.caller_dir, context.current_vault)
                                        if kind == "transport" and context.current_vault is not None else None)
            if any(result.state == "conflicted" for result in results):
                raise ValueError("User policy conflicts prevent required pre-transition tightening; preserve and resolve the overrides")
            active[context.invocation_id] = os.getpid()
            _save_active(plan, home, active)
            if not context.dry_run:
                effects.extend(manager.commit(plan, home))
        executing = True
        result = execute(context, request)
        executing = False
        if context.dry_run:
            return result
        if isinstance(result, Error) and result.effects == "unknown":
            # Keep the transition marker: actual target state needs explicit recovery.
            return result
        with mcp_registration.registration_lock(home):
            plan = FilePlan()
            records = manager.read_records(plan, home)
            active = active_transitions(plan, home)
            active.pop(context.invocation_id, None)
            _save_active(plan, home, active)
            # Drop a removed selected root; inventory reflects committed registry state.
            actual_context = replace(context, current_vault=None)
            roots = manager.inventory(plan, actual_context) if records else ()
            results = reconcile_records(plan, actual_context, records, roots, tighten=bool(active))
            if any(item.state == "conflicted" for item in results):
                raise ValueError("Target changed but client policy has preserved user conflicts; run approvals inspect")
            effects.extend(manager.commit(plan, home))
        mapped = tuple(CommittedEffect(request.COMMAND_ID, path) for path in sorted(set(effects))
                       if path != str(transition_path(home)))
        if isinstance(result, Ok):
            return replace(result, committed_effects=(*result.committed_effects, *mapped))
        if isinstance(result, Partial):
            return replace(result, committed_effects=(*result.committed_effects, *mapped))
        if mapped:
            return Partial(request.COMMAND_ID, request.COMMAND_VERSION, result.error, mapped)
        return result
    except (OSError, ValueError, RuntimeError) as exc:
        if executing:
            # The owner did not return an effect receipt. Preserve its unknown outcome.
            raise
        committed = tuple(getattr(locals().get("result"), "committed_effects", ()))
        paths = tuple(sorted({*effects, *(str(p) for p in getattr(exc, "surviving_paths", ())) }))
        if paths or committed:
            reason = str(exc)
            recovery = tuple(sorted({str(transition_path(home)), str(manager.journal_path(home)), *paths}))
            error = CommandError(ErrorCode.CONFLICT, reason, RecoveryRequiredDetails(recovery, reason),
                                 InstructionNextAction("Inspect managed approvals; recover incomplete transactions/transitions, then repair."))
            return Partial(request.COMMAND_ID, request.COMMAND_VERSION, error,
                           (*committed, *(CommittedEffect(request.COMMAND_ID, path) for path in paths)))
        return no_effect_error(type(request), ErrorCode.CONFLICT, str(exc))


def recover_transitions(context):
    """Reconcile actual inventory after abandoned transitions, never a live owner."""
    plan = FilePlan()
    home = context.home_dir
    active = active_transitions(plan, home)
    if not active:
        return manager.ApprovalsPayload(manager.POLICY, (), ())
    with ExitStack() as locks:
        if not context.dry_run:
            for identity in active:
                path = operation_lock(home, identity)
                _refuse_symlink_path(path)
                locks.enter_context(exclusive_file_lock(path, timeout=0, follow_symlinks=False))
        records = manager.read_records(plan, home)
        actual = replace(context, current_vault=None)
        results = reconcile_records(plan, actual, records, manager.inventory(plan, actual)) if records else []
        if any(item.state == "conflicted" for item in results):
            raise ValueError("Preserved user conflicts still prevent approval transition recovery")
        _save_active(plan, home, {})
        paths = tuple(str(c.path) for c in plan.changes()) if context.dry_run else manager.commit(plan, home)
    if not context.dry_run:
        for identity in active:
            operation_lock(home, identity).unlink(missing_ok=True)
    return manager.ApprovalsPayload(manager.POLICY, tuple(results), paths)


def distribution_cutover(source, binary, execute):
    """Reconcile the machine CLI's own contract under the caller's registry lock."""
    from _local_cli.runtime import compose_launcher_context
    from _distribution import DistributionInstallError

    context = compose_launcher_context(cli_binary=binary, distribution_root=source, selected=None, dry_run=False)
    home = context.home_dir
    plan = FilePlan()
    records = manager.read_records(plan, home)
    if not records:
        return execute()
    identity = "distribution-" + uuid.uuid4().hex
    lock = operation_lock(home, identity)
    _refuse_symlink_path(lock)
    effects = []
    with exclusive_file_lock(lock, timeout=0, follow_symlinks=False):
        try:
            if plan.read_bytes(manager.journal_path(home)) is not None:
                raise ValueError("Recover pending approval writes before replacing the CLI")
            active = active_transitions(plan, home)
            active[identity] = os.getpid()
            _save_active(plan, home, active)
            results = reconcile_records(plan, context, records, manager.inventory(plan, context), tighten=True)
            if any(r.state == "conflicted" for r in results):
                raise ValueError("Resolve preserved approval overrides before replacing the CLI")
            effects.extend(manager.commit(plan, home))
            installed = execute()
            plan = FilePlan()
            active = active_transitions(plan, home)
            active.pop(identity)
            _save_active(plan, home, active)
            context = replace(context, distribution_root=installed.distribution_root)
            results = reconcile_records(plan, context, manager.read_records(plan, home),
                                        manager.inventory(plan, context), tighten=bool(active))
            if any(r.state == "conflicted" for r in results):
                raise ValueError("CLI replaced; approval reconciliation needs explicit recovery")
            effects.extend(manager.commit(plan, home))
        except (OSError, ValueError, RuntimeError) as exc:
            paths = tuple(sorted({Path(p) for p in effects} | set(getattr(exc, "recovery_paths", ()))
                                 | set(getattr(exc, "surviving_paths", ())), key=str))
            if paths:
                raise DistributionInstallError(f"CLI/approval transition incomplete: {exc}", rollback_verified=False,
                                               recovery_paths=(*paths, transition_path(home))) from exc
            raise
    lock.unlink(missing_ok=True)
    return installed
