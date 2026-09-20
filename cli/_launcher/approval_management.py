"""Compose approval policy, native projections and existing host file transactions."""

from __future__ import annotations

import base64
from contextlib import nullcontext
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

from _bootstrap.approval_clients import Selection, config_root, desired_items, observed_items, render_items, known_overrides
from _bootstrap.approval_ownership import reconcile_items
from _bootstrap.approval_policy import POLICY, CommandFact, desired_policy, read_snapshot
from _bootstrap.file_transaction import FilePlan, FileChange, FileTransactionError, apply_file_changes
from _bootstrap import mcp_inventory, mcp_registration

from .approvals import ApprovalItemStatus, ApprovalTargetStatus, ApprovalsPayload


LEDGER_SCHEMA = "brain.client-approvals/1"
JOURNAL_SCHEMA = "brain.client-approval-transaction/1"
MIN_CLIENT_VERSION = {"codex": (0, 155, 1), "claude": (2, 1, 278)}


def ledger_path(home: Path) -> Path:
    return mcp_registration.user_ledger_path(home).with_name("client-approvals.json")


def journal_path(home: Path) -> Path:
    return ledger_path(home).with_name("client-approvals.pending.json")


def _object(content: str | None, path: Path) -> dict:
    try:
        value = json.loads(content) if content is not None else {}
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid approval state at {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Approval state must be an object: {path}")
    return value


def read_records(plan: FilePlan, home: Path) -> dict:
    path = ledger_path(home)
    content = plan.read_text(path)
    if content is None:
        return {}
    value = _object(content, path)
    if value.get("schema") != LEDGER_SCHEMA or not isinstance(value.get("records"), dict):
        raise ValueError(f"Unsupported approval ownership state: {path}")
    for identity, record in value["records"].items():
        if (not isinstance(record, dict) or record.get("policy") != POLICY
                or not isinstance(record.get("owned"), dict) or not isinstance(record.get("exclusions"), list)
                or any(not isinstance(item, str) for item in record["exclusions"])
                or not isinstance(record.get("legacy", {}), dict)
                or (record.get("owner") is not None and
                    (not isinstance(record["owner"], str) or not Path(record["owner"]).is_absolute()))):
            raise ValueError(f"Invalid approval policy receipt: {identity}")
        selection = record_selection(record, home)
        if selection.identity != identity:
            raise ValueError("approval receipt identity disagrees with its native destination")
    return value["records"]


def record_selection(record: dict, home: Path) -> Selection:
    try:
        target = Path(record["target"]) if record["target"] is not None else None
        root = config_root(record["client"], record["scope"], home, target)
        if str(root) != record["root"]:
            raise ValueError("client configuration root changed; explicitly migrate/adopt its approvals")
        return Selection(record["client"], record["scope"], record["surface"], root,
                         Path(record["executable"]), record["server"])
    except (KeyError, TypeError) as exc:
        raise ValueError("invalid approval target receipt") from exc


def _new_record(selection: Selection, target: Path | None) -> dict:
    return {"client": selection.client, "scope": selection.scope, "surface": selection.surface,
            "root": str(selection.root), "target": str(target) if target else None,
            "executable": str(selection.executable), "server": selection.server,
            "policy": POLICY, "owned": {}, "exclusions": [], "contracts": []}


def _client_version(client: str) -> str:
    binary = shutil.which(client)
    if binary is None:
        raise ValueError(f"{client} is unavailable; native approval compatibility cannot be verified")
    process = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=10, check=False)
    version = re.search(r"\b(\d+)\.(\d+)\.(\d+)\b", process.stdout)
    if process.returncode or version is None or tuple(map(int, version.groups())) < MIN_CLIENT_VERSION[client]:
        raise ValueError(f"{client} requires approval-compatible version {'.'.join(map(str, MIN_CLIENT_VERSION[client]))} or newer")
    return version.group(0)


def _contracts(plan: FilePlan, roots: tuple[Path, ...], *, overrides: dict[Path, Path] | None = None):
    facts, fingerprints = [], []
    for root in roots:
        core = (overrides or {}).get(root, root / ".brain-core")
        path = core / "approval-contract.json"
        content = plan.read_text(path)
        if content is None:
            raise ValueError(f"Approval contract unavailable: {path}; upgrade/repair that Brain before widening managed approvals")
        if len(content) > 2_000_000:
            raise ValueError(f"Approval contract exceeds its bounded input size: {path}")
        value = _object(content, path)
        facts.append(read_snapshot(value))
        fingerprints.append({"brain": str(root), "fingerprint": value["fingerprint"]})
    return tuple(facts), fingerprints


def _launcher_facts(plan, root=None):
    path = (root or Path(__file__).resolve().parents[2]) / "cli/approval-contract.json"
    raw = plan.read_text(path)
    if raw is None:
        raise ValueError(f"Installed launcher approval contract is unavailable: {path}")
    return read_snapshot(_object(raw, path))


def inventory(plan: FilePlan, context) -> tuple[Path, ...]:
    return mcp_inventory.local_brains(plan, context.current_vault)


def _target_roots(plan, selection, target, roots, home):
    # Command-first CLI forms can select another registered Brain after the verb.
    # Native project scope limits rule loading, not the executable's --vault target.
    if selection.scope == "user" or selection.surface == "cli":
        return roots
    scope = mcp_registration.McpScope(selection.scope)
    registered = []
    for root in roots:
        _, records = mcp_registration.read_records(plan, root, home, scope)
        if any(record["client"] == selection.client and record["scope"] == selection.scope
               and record["target_path"] == str(target) for record in records):
            registered.append(root)
    # A workspace binding can change independently of its pinned MCP transport.
    # Its transport registration remains authoritative until explicitly retargeted.
    if registered:
        if len(registered) != 1:
            raise ValueError(f"Conflicting MCP owners for approval workspace: {target}")
        return tuple(registered)
    owners = [root for root in roots if target in mcp_inventory.workspace_paths(plan, root)]
    if len(owners) != 1:
        raise ValueError(f"Approval workspace needs one authoritative Brain registration: {target}")
    return tuple(owners)


def _selected(context, request):
    clients = ("codex", "claude") if request.client.value == "all" else (request.client.value,)
    target = None if request.scope.value == "user" else (context.workspace_dir or context.caller_dir)
    for client in clients:
        if request.scope.value == "local" and client == "codex":
            continue
        root = config_root(client, request.scope.value, context.home_dir, target)
        for surface in request.surfaces:
            yield Selection(client, request.scope.value, surface.value, root, context.cli_binary), target


def _stage_target(plan, selection, target, roots, record, *, action, adopt=(), restore=(), overrides=None, tighten=False, launcher_root=None):
    if sys.platform == "win32" and selection.surface == "cli" and action not in {"remove", "detach"}:
        raise ValueError("Native Windows shell approvals are not yet certified; MCP approvals remain separately selectable")
    if (selection.client == "codex" and selection.surface == "cli" and action not in {"remove", "detach"}
            and re.fullmatch(r"[A-Za-z0-9_./-]+", str(selection.executable)) is None):
        raise ValueError("Codex shell rules do not reliably match quoted executable paths; use an unquoted-safe Brain launcher path or MCP approvals")
    original = plan.read_text(selection.path)
    if original is None and record["owned"] and not restore and action not in {"detach", "remove"}:
        raise ValueError(f"Client policy file was removed; explicit restoration is required: {selection.path}")
    content = original or ""
    if original is None and action == "remove" and not record.get("legacy"):
        return None, ApprovalTargetStatus(selection.client, selection.scope, selection.surface, str(selection.path),
                                          "removed", (), "Missing policy file retained; ownership receipt removed.")
    if action == "detach":
        return None, ApprovalTargetStatus(selection.client, selection.scope, selection.surface, str(selection.path),
                                          "detached", (), "Native rules retained; no client reload required.")
    observed = observed_items(selection, content)
    contracts, fingerprints = _contracts(plan, roots, overrides=overrides) if action != "remove" else ((), [])
    if selection.surface == "cli" and action != "remove":
        contracts += (_launcher_facts(plan, launcher_root),)
    desired = desired_items(selection, desired_policy(contracts, selection.surface)) if action != "remove" else {}
    if tighten and action != "remove":
        desired = tightening_items(selection, desired, observed)
    # Adoption and restoration select exact names from inspect, scoped to one projection.
    prefix = selection.identity + "::"
    selected_adopt = tuple(item[len(prefix):] for item in adopt if item.startswith(prefix))
    selected_adopt = tuple(item for item in selected_adopt if not item.startswith("legacy:"))
    selected_restore = tuple(item[len(prefix):] for item in restore if item.startswith(prefix))
    legacy, legacy_findings, legacy_edit = record.get("legacy", {}), (), None
    if selection.client == "codex" and selection.surface == "cli":
        from _bootstrap.approval_migration import migrate_codex
        legacy_path = selection.root / "rules/default.rules"
        legacy_before = plan.read_text(legacy_path)
        legacy_after, legacy, legacy_findings = migrate_codex(
            selection, legacy_before, desired, legacy,
            tuple(item for item in adopt if item.startswith(prefix + "legacy:")), remove=action == "remove")
        if legacy_after != (legacy_before or ""):
            legacy_edit = (legacy_path, legacy_after)
    reconciled = reconcile_items(desired, observed, record["owned"], tuple(record["exclusions"]),
                                 adopt=selected_adopt, restore=selected_restore, remove=action == "remove")
    candidate = render_items(selection, content, reconciled.observed)
    if candidate != content:
        plan.write_text(selection.path, candidate)
    if legacy_edit:
        plan.write_text(*legacy_edit)
    updated = {**record, "owned": reconciled.owned, "exclusions": list(reconciled.exclusions), "contracts": fingerprints, "legacy": legacy}
    if selection.scope != "user" and selection.surface == "mcp" and action != "remove" and len(roots) == 1:
        updated["owner"] = str(roots[0])
    findings = [ApprovalItemStatus(prefix + item, status) for item, status in reconciled.findings]
    findings.extend(ApprovalItemStatus(identity, status) for identity, status in legacy_findings)
    # Include every desired identity, so explicit adoption does not need guessed native syntax.
    present = {item.identity for item in findings}
    findings.extend(ApprovalItemStatus(prefix + key, "managed" if key in reconciled.owned else "desired")
                    for key in sorted(desired) if prefix + key not in present)
    conflicts = any(item.state in {"modified", "deleted", "excluded", "unowned_conflict", "legacy_modified"} for item in findings)
    state = "conflicted" if conflicts else "changed" if candidate != content else "current"
    overrides_found = tuple(known_overrides(selection, candidate, desired)) if action != "remove" else ()
    if overrides_found:
        findings.extend(ApprovalItemStatus(prefix + key, "overridden") for key in overrides_found)
        if not conflicts:
            state = "overridden"
    if action == "remove" and not reconciled.owned and not legacy:
        updated = None
        state = "removed"
    activation = ("Restart Codex to load rule-file changes; MCP host reload may also be required."
                  if selection.client == "codex" else "Verify settings in a fresh Claude session; running sessions may retain policy.")
    if selection.scope == "project":
        activation += " Project allow rules require native workspace trust; Brain does not grant it."
    return updated, ApprovalTargetStatus(selection.client, selection.scope, selection.surface, str(selection.path),
                                         state, tuple(findings), activation)


def tightening_items(selection, desired, observed):
    """No new allowance or removed review rule while a target transition is active."""
    def allowance(key, value):
        if selection.client == "claude":
            return key.startswith("allow:")
        if selection.surface == "mcp":
            return value in {"approve", "auto", "writes"}
        return 'decision="allow"' in key or 'decision= "allow"' in key
    result = dict(desired)
    for key, value in desired.items():
        if allowance(key, value) and observed.get(key) != value:
            if key in observed:
                result[key] = observed[key]
            else:
                result.pop(key, None)
    for key, value in observed.items():
        if not allowance(key, value) and key not in result:
            result[key] = value
    return result


def _save_records(plan, home, records):
    path = ledger_path(home)
    if records or plan.read_text(path) is not None:
        plan.write_text(path, json.dumps({"schema": LEDGER_SCHEMA, "records": records}, indent=2) + "\n")


def commit(plan: FilePlan, home: Path) -> tuple[str, ...]:
    """Retain write-intent evidence across process death, in addition to rollback."""
    changes = plan.changes()
    if not changes:
        return ()
    journal = journal_path(home)
    pending = FilePlan()
    if pending.read_bytes(journal) is not None:
        raise ValueError(f"Incomplete approval transaction requires explicit recovery: {journal}")
    plan.validate()
    encode = lambda data: base64.b64encode(data).decode() if data is not None else None
    value = {"schema": JOURNAL_SCHEMA, "changes": [{"path": str(c.path), "before": encode(c.before), "after": encode(c.after)} for c in changes]}
    journal_content = json.dumps(value, indent=2) + "\n"
    if len(journal_content.encode()) > 32_000_000:
        raise ValueError("Approval transaction exceeds the recoverable journal size")
    pending.write_text(journal, journal_content)
    apply_file_changes(pending.changes())
    try:
        apply_file_changes(changes, before_write=plan.validate_dependencies)
    except BaseException as exc:
        raise FileTransactionError(f"Approval transaction interrupted; inspect {journal}: {exc}",
                                   tuple(sorted({journal, *(c.path for c in changes)}, key=str))) from exc
    try:
        apply_file_changes((FileChange(journal, journal_content.encode(), None),))
    except BaseException as exc:
        raise FileTransactionError(f"Approvals applied but transaction receipt remains: {journal}", (journal,)) from exc
    return tuple(str(c.path) for c in changes)


def manage(context, request, *, inspect=False):
    action = "inspect" if inspect else request.action.value
    home = context.home_dir
    with nullcontext() if inspect or context.dry_run else mcp_registration.registration_lock(home):
        if action == "recover":
            return recover(context, request)
        plan = FilePlan()
        if FilePlan().read_bytes(journal_path(home)) is not None:
            raise ValueError(f"Incomplete approval transaction: {journal_path(home)}; recover before another policy write")
        records = read_records(plan, home)
        from .approval_lifecycle import active_transitions
        if active_transitions(plan, home) and not inspect:
            raise ValueError("A target transition is incomplete; inspect/recover it before changing approval policy")
        roots = () if action in {"remove", "detach"} else inventory(plan, context)
        results = []
        versions = {}
        selected_keys = set()
        for selection, target in _selected(context, request):
            old = records.get(selection.identity)
            if old is None and action in {"repair", "detach", "remove"}:
                results.append(ApprovalTargetStatus(selection.client, selection.scope, selection.surface, str(selection.path),
                                                    "not_managed", (), "No opt-in recorded; no changes."))
                continue
            record = old or _new_record(selection, target)
            try:
                if action not in {"remove", "detach"} and selection.client not in versions:
                    versions[selection.client] = _client_version(selection.client)
                target_roots = () if action in {"remove", "detach"} else _target_roots(plan, selection, target, roots, home)
                updated, result = _stage_target(plan, selection, target, target_roots, record, action=action,
                                               adopt=getattr(request, "adopt_items", ()), restore=getattr(request, "restore_items", ()),
                                               launcher_root=context.distribution_root)
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                results.append(ApprovalTargetStatus(selection.client, selection.scope, selection.surface,
                                                    str(selection.path), "blocked", (), str(exc)))
                continue
            results.append(result)
            selected_keys.update(item.identity for item in result.items)
            if updated is None:
                records.pop(selection.identity, None)
            else:
                records[selection.identity] = updated
        requested_keys = set(getattr(request, "adopt_items", ())) | set(getattr(request, "restore_items", ()))
        if requested_keys - selected_keys:
            raise ValueError("adopt/restore contains an identity outside the selected projections")
        if not inspect:
            _save_records(plan, home, records)
        paths = tuple(str(c.path) for c in plan.changes())
        if not inspect and not context.dry_run:
            paths = commit(plan, home)
        return ApprovalsPayload(POLICY, tuple(results), paths)


def recover(context, request):
    """Explicitly restore a pending transaction only when no later edit is lost."""
    journal = journal_path(context.home_dir)
    probe = FilePlan()
    content = probe.read_text(journal)
    if content is None:
        from .approval_lifecycle import recover_transitions
        return recover_transitions(context)
    if len(content) > 32_000_000:
        raise ValueError("approval recovery journal exceeds its size boundary")
    pending = _object(content, journal)
    if pending.get("schema") != JOURNAL_SCHEMA or not isinstance(pending.get("changes"), list):
        raise ValueError("unsupported approval recovery journal")
    from .approval_lifecycle import transition_path
    control_paths = {ledger_path(context.home_dir), transition_path(context.home_dir)}
    selections = tuple(selection for selection, _ in _selected(context, request))
    # Recovery restores admitted before/after bytes, not individual policy items.
    # Claude's two surfaces share one file; make that recovery unit explicit.
    allowed = {selection.path for selection in selections
               if selection.client != "claude" or len(request.surfaces) == 2}
    allowed.update(selection.root / "rules/default.rules" for selection, _ in _selected(context, request)
                   if selection.client == "codex" and selection.surface == "cli")
    changes, seen, remaining = [], set(), []
    for item in pending["changes"]:
        if (not isinstance(item, dict) or set(item) != {"path", "before", "after"}
                or not isinstance(item["path"], str) or not Path(item["path"]).is_absolute()):
            raise ValueError("invalid approval recovery entry")
        path = Path(item["path"])
        if path in seen:
            raise ValueError("duplicate approval recovery destination")
        seen.add(path)
        if path not in allowed and path not in control_paths:
            remaining.append(item)
    if remaining:
        remaining.extend(item for item in pending["changes"] if Path(item["path"]) in control_paths)
    else:
        allowed.update(control_paths)
    for item in pending["changes"]:
        path = Path(item["path"])
        if path not in allowed:
            continue
        try:
            before = base64.b64decode(item["before"], validate=True) if item["before"] is not None else None
            after = base64.b64decode(item["after"], validate=True) if item["after"] is not None else None
        except (ValueError, TypeError) as exc:
            raise ValueError("invalid approval recovery bytes") from exc
        current = probe.read_bytes(path)
        if current not in (before, after):
            raise ValueError(f"Preserving user edit after interrupted approval transaction: {path}")
        if current != before:
            changes.append(FileChange(path, current, before))
    replacement = (json.dumps({"schema": JOURNAL_SCHEMA, "changes": remaining}, indent=2) + "\n").encode() if remaining else None
    journal_changed = replacement != content.encode()
    if not context.dry_run:
        probe.validate()
        try:
            apply_file_changes(tuple(changes))
            if journal_changed:
                apply_file_changes((FileChange(journal, content.encode(), replacement),))
        except (OSError, RuntimeError) as exc:
            raise FileTransactionError(f"Approval recovery interrupted: {exc}",
                                       (journal, *(c.path for c in changes))) from exc
    if remaining:
        paths = tuple(item["path"] for item in remaining if Path(item["path"]) not in control_paths)
        status = ApprovalTargetStatus("machine", request.scope.value, "all", str(journal), "blocked", (),
                                      "Recovery remains pending; select the remaining scopes/targets. Claude whole-file recovery requires both MCP and CLI surfaces: " + ", ".join(paths))
        return ApprovalsPayload(POLICY, (status,), tuple(str(c.path) for c in changes) + ((str(journal),) if journal_changed else ()))
    from .approval_lifecycle import recover_transitions
    try:
        recovered = recover_transitions(context) if not context.dry_run else ApprovalsPayload(POLICY, (), ())
    except (OSError, ValueError, RuntimeError) as exc:
        raise FileTransactionError(f"Policy file recovery applied; transition recovery remains: {exc}",
                                   (journal, *(c.path for c in changes))) from exc
    return ApprovalsPayload(POLICY, recovered.targets, tuple(str(c.path) for c in changes) + (str(journal),) + recovered.changed_paths)


def inspect_registered(context):
    """Doctor inspects opt-ins only and never creates policy or machine locks."""
    plan = FilePlan()
    home = context.home_dir
    try:
        records = read_records(plan, home)
        from .approval_lifecycle import active_transitions
        if plan.read_bytes(journal_path(home)) is not None or active_transitions(plan, home):
            raise ValueError("Approval recovery evidence is pending; inspect and explicitly recover before repair")
        if not records:
            return ()
        roots = inventory(plan, context)
    except (OSError, ValueError) as exc:
        return (ApprovalTargetStatus("machine", "user", "all", str(ledger_path(home)), "blocked", (), str(exc)),)
    results = []
    for record in records.values():
        selection = record_selection(record, home)
        target = Path(record["target"]) if record["target"] else None
        try:
            relevant = _target_roots(plan, selection, target, roots, home)
            _, result = _stage_target(plan, selection, target, relevant, record, action="inspect")
        except (OSError, ValueError) as exc:
            result = ApprovalTargetStatus(selection.client, selection.scope, selection.surface, str(selection.path), "blocked", (), str(exc))
        results.append(result)
    return tuple(results)
