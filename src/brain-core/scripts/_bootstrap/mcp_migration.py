"""Bounded legacy admission into canonical MCP ownership, with resumable effects."""

from __future__ import annotations

import base64
import json
from pathlib import Path

from _bootstrap.file_transaction import FilePlan, apply_file_changes
from _bootstrap import mcp_inventory, mcp_registration as registration
from _bootstrap.mcp_state import INIT_STATE_REL


def migration_plan(home: Path, cli_binary: Path, selected: Path | None = None) -> FilePlan:
    """Adopt only exact recorded native projections; never infer intent from files."""
    plan = FilePlan()
    vaults = mcp_inventory.local_brains(plan, selected)
    machine_path, user_records = registration.read_records(plan, None, home, registration.McpScope.USER)
    claims = {registration._record_id(record): record for record in user_records}
    for vault in vaults:
        registry_path = vault / ".brain/local/workspaces.json"
        registry = registration._json_object(plan, registry_path)
        workspaces = registry.get("workspaces", {})
        if not isinstance(workspaces, dict):
            raise ValueError(f"Invalid workspace registry: {registry_path}")
        canonical = {slug: {"path": value} if isinstance(value, str) else value
                     for slug, value in workspaces.items()}
        if canonical != workspaces:
            registration._write_json(plan, registry_path, {**registry, "workspaces": canonical})
        path = vault / INIT_STATE_REL
        data = registration._json_object(plan, path)
        if not data and plan.read_bytes(path) is None:
            continue
        if data.get("version") not in (1, registration.LEDGER_VERSION):
            raise ValueError(f"Unsupported MCP registration history: {path}")
        records = data.get("records")
        if not isinstance(records, list):
            raise ValueError(f"Incomplete MCP ownership evidence: {path}")
        retained = []
        for raw in records:
            if not isinstance(raw, dict):
                raise ValueError(f"Invalid MCP ownership claim: {path}")
            client = registration.McpClient(raw.get("client"))
            scope = registration.McpScope(raw.get("scope"))
            if client is registration.McpClient.ALL or (scope is registration.McpScope.LOCAL and client is not registration.McpClient.CLAUDE):
                raise ValueError(f"Invalid native MCP scope: {path}")
            target_value = raw.get("target_path")
            if scope is not registration.McpScope.USER and not isinstance(target_value, str):
                raise ValueError(f"Invalid MCP target ownership evidence: {path}")
            target = None if scope is registration.McpScope.USER else Path(target_value)
            if target is not None:
                if not target.is_absolute() or not target.is_dir():
                    raise ValueError(f"Incomplete migration: target unavailable: {target}")
                registration._validate_target(vault, target)
                registration.plan_reverse_registration(plan, vault, target)
            destination = registration._config_path(client, scope, target, home).absolute()
            if raw.get("config_path") != str(destination) or not isinstance(raw.get("server_config"), dict):
                raise ValueError(f"Invalid ownership evidence for {destination}")
            observed = registration.observed_server(plan, client, destination)
            expected = {**raw["server_config"], "env": raw["server_config"].get("env", {})}
            registration.validate_server(expected, path)
            if observed is not None and not registration.server_matches(client, observed, expected):
                raise ValueError(f"Modified/conflicting MCP projection requires explicit resolution: {destination}")
            record = {**raw, "schema": registration.REGISTRATION_SCHEMA, "server_config": expected}
            if client is registration.McpClient.CLAUDE and target is not None:
                from _bootstrap.mcp_state import build_session_hook_command, is_session_hook_command, bootstrap_line_for_target

                old_hook = raw.get("hook_command")
                desired_hook = build_session_hook_command(vault, target, python_path=expected["command"])
                if old_hook is not None and old_hook != desired_hook:
                    if not is_session_hook_command(old_hook, vault, target):
                        raise ValueError(f"Unrecognised hook ownership requires explicit resolution: {path}")
                    hook_path, command = registration._ensure_hook(plan, target, vault, expected["command"], old_hook)
                    record.update(hook_path=str(hook_path), hook_command=command)
                if raw.get("bootstrap_line") not in (None, bootstrap_line_for_target(target)):
                    raise ValueError(f"Legacy bootstrap ownership needs explicit recovery before migration: {path}")
            if scope is registration.McpScope.USER:
                if raw.get("target_path") is not None:
                    raise ValueError(f"Invalid generic user claim: {destination}")
                identity = registration._record_id(record)
                previous = claims.get(identity)
                if previous is not None and previous["server_config"] != expected:
                    raise ValueError(f"Conflicting user registration claims: {destination}")
                claims[identity] = record
            else:
                retained.append(record)
        registration._save_records(plan, path, retained)
    registration._save_records(plan, machine_path, list(claims.values()))
    clients = tuple(sorted({registration.McpClient(record["client"]) for record in claims.values()}, key=lambda c: c.value))
    if clients:
        registration._configure_plan(None, home, None, registration.McpScope.USER, clients,
                                     registration.stable_server_config(cli_binary), plan=plan, repair=True)
    # Every migrated record must pass the ordinary canonical reader/workset.
    for vault in vaults:
        mcp_inventory.brain_targets(plan, vault, home)
    registration.read_records(plan, None, home, registration.McpScope.USER)
    return plan




def journal_path(home: Path) -> Path:
    return registration.user_ledger_path(home).with_name("mcp-migration.json")


def read_journal(plan: FilePlan, home: Path) -> dict:
    path = journal_path(home)
    evidence = registration._json_object(plan, path)
    if not evidence and plan.read_bytes(path) is None:
        return {}
    if (evidence.get("version") != 1 or evidence.get("phase") not in ("pending", "complete")
            or not isinstance(evidence.get("changes"), list)
            or not isinstance(evidence.get("launch_verified", False), bool)):
        raise ValueError(f"Invalid MCP migration recovery evidence: {path}")
    destinations = set()
    for item in evidence["changes"]:
        if (not isinstance(item, dict) or not isinstance(item.get("path"), str)
                or not Path(item["path"]).is_absolute() or item["path"] in destinations
                or "before" not in item or "after" not in item):
            raise ValueError(f"Invalid MCP migration change evidence: {path}")
        destinations.add(item["path"])
        for key in ("before", "after"):
            value = item[key]
            if value is not None:
                if not isinstance(value, str):
                    raise ValueError(f"Invalid MCP migration bytes: {path}")
                base64.b64decode(value, validate=True).decode("utf-8")
    return evidence


def apply_migration(plan: FilePlan, home: Path) -> tuple[Path, ...]:
    """Keep exact before/after evidence durable until all canonical writes verify."""
    changes = plan.changes()
    if not changes:
        return ()
    path = journal_path(home)
    journal_plan = FilePlan()
    previous = read_journal(journal_plan, home)
    if previous and not previous.get("launch_verified"):
        raise ValueError("Prior MCP migration requires resume/launch verification before another transition")
    encode = lambda value: None if value is None else base64.b64encode(value).decode("ascii")
    evidence = {"version": 1, "phase": "pending", "changes": [
        {"path": str(change.path), "before": encode(change.before), "after": encode(change.after)}
        for change in changes
    ]}
    registration._write_json(journal_plan, path, evidence)
    plan.validate()
    apply_file_changes(journal_plan.changes())
    applied = False
    try:
        apply_file_changes(changes, before_write=plan.validate_dependencies)
        applied = True
        _finish_journal(path, evidence)
    except BaseException as exc:
        survived = {path, *getattr(exc, "surviving_paths", ())}
        if applied:
            survived.update(change.path for change in changes)
        exc.surviving_paths = tuple(sorted(survived, key=str))
        raise
    return (*[change.path for change in changes], path)


def resume_migration(home: Path, selected: Path | None = None) -> tuple[Path, ...]:
    """Resume a known transition, refusing drift or destinations outside registered layouts."""
    path = journal_path(home)
    probe = FilePlan()
    evidence = read_journal(probe, home)
    if evidence.get("phase") != "pending":
        return ()
    vaults = mcp_inventory.local_brains(probe, selected)
    allowed = {registration.user_ledger_path(home)}
    targets = set(vaults)
    for vault in vaults:
        allowed.update((vault / INIT_STATE_REL, vault / ".brain/local/workspaces.json"))
        targets.update(mcp_inventory.workspace_paths(probe, vault))
        for item in evidence["changes"]:
            if Path(item["path"]) != vault / INIT_STATE_REL:
                continue
            for key in ("before", "after"):
                if item[key] is None:
                    continue
                records = json.loads(base64.b64decode(item[key]))
                if not isinstance(records, dict) or not isinstance(records.get("records"), list):
                    raise ValueError(f"Invalid recorded migration targets: {vault}")
                for record in records["records"]:
                    if not isinstance(record, dict):
                        raise ValueError(f"Invalid recorded migration target: {vault}")
                    target = record.get("target_path")
                    if target is not None:
                        if not isinstance(target, str) or not Path(target).is_absolute():
                            raise ValueError(f"Invalid recorded migration target: {target}")
                        registration.plan_target_admission(probe, vault, Path(target))
                        targets.add(Path(target))
    for root, scopes in [(home, (registration.McpScope.USER,)), *[(target, (registration.McpScope.PROJECT, registration.McpScope.LOCAL)) for target in targets]]:
        for scope in scopes:
            for client in registration._clients(registration.McpClient.ALL, scope):
                allowed.add(registration._config_path(client, scope, None if scope is registration.McpScope.USER else root, home))
        allowed.add(root / ".grok/rules/brain.md")
        if root in targets:
            from _bootstrap.mcp_state import CLAUDE_LOCAL_SETTINGS_FILE, CLAUDE_MD_FILE, CLAUDE_LOCAL_MD_FILE

            allowed.update(root / relative for relative in (CLAUDE_LOCAL_SETTINGS_FILE, CLAUDE_MD_FILE, CLAUDE_LOCAL_MD_FILE))
    decode = lambda value: None if value is None else base64.b64decode(value, validate=True)
    for item in evidence["changes"]:
        destination = Path(item["path"])
        if destination not in allowed:
            raise ValueError(f"Recovery destination requires registry recovery first: {destination}")
        before, after = decode(item["before"]), decode(item["after"])
        current = probe.read_bytes(destination)
        if current == after:
            continue
        if current != before:
            raise ValueError(f"MCP migration recovery conflict: {destination}")
        if after is None:
            probe.delete(destination)
        else:
            probe.write_text(destination, after.decode("utf-8"))
    probe.validate()
    changes = probe.changes()
    applied = False
    try:
        apply_file_changes(changes, before_write=probe.validate_dependencies)
        applied = True
        _finish_journal(path, evidence)
    except BaseException as exc:
        survived = {path, *getattr(exc, "surviving_paths", ())}
        if applied:
            survived.update(change.path for change in changes)
        exc.surviving_paths = tuple(sorted(survived, key=str))
        raise
    return (*[change.path for change in changes], path)


def _finish_journal(path: Path, evidence: dict) -> None:
    probe = FilePlan()
    for item in evidence["changes"]:
        after = None if item["after"] is None else base64.b64decode(item["after"], validate=True)
        if probe.read_bytes(Path(item["path"])) != after:
            raise ValueError(f"MCP migration postcondition failed: {item['path']}")
    registration._write_json(probe, path, {**evidence, "phase": "complete"})
    probe.validate()
    apply_file_changes(probe.changes())


def verify_transition(home: Path, selected: Path | None = None) -> tuple[Path, ...]:
    """Release retired runtime references only after persisted user launch succeeds."""
    from _bootstrap.mcp_readiness import verify_command

    plan = FilePlan()
    path = journal_path(home)
    evidence = read_journal(plan, home)
    if not evidence or evidence.get("launch_verified"):
        return ()
    if evidence.get("phase") != "complete":
        raise ValueError("Resume the pending migration before verifying launch")
    _, records = registration.read_records(plan, None, home, registration.McpScope.USER)
    vaults = mcp_inventory.local_brains(plan, selected)
    if records and not vaults:
        raise ValueError("User transport is configured, but no registered Brain can verify launch; old runtime evidence remains protected")
    commands = {json.dumps(record["server_config"], sort_keys=True): record["server_config"] for record in records}
    for vault in vaults:
        for server in commands.values():
            verify_command(server, vault, vault)
    registration._write_json(plan, path, {**evidence, "launch_verified": True})
    plan.validate()
    apply_file_changes(plan.changes())
    return (path,)
