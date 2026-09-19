"""Registry-backed worksets for host-local MCP reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from _bootstrap.file_transaction import FilePlan
from _bootstrap import mcp_registration as registration


@dataclass(frozen=True)
class RegisteredTarget:
    vault: Path
    target: Path
    scope: registration.McpScope
    clients: tuple[registration.McpClient, ...]


def local_brains(plan: FilePlan, selected: Path | None = None) -> tuple[Path, ...]:
    """Read authoritative identities strictly; remote entries confer no local authority."""
    import vault_registry

    path = Path(vault_registry.registry_path())
    content = plan.read_text(path)
    roots = set()
    identities = set()
    for line in (content or "").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        entry = vault_registry._parse_entry(line)
        if entry is None or entry.brain_id in identities:
            raise ValueError(f"Incomplete Brain registry: malformed or duplicate identity in {path}")
        identities.add(entry.brain_id)
        if entry.kind == vault_registry.TYPE_REMOTE:
            continue
        if entry.kind != vault_registry.TYPE_LOCAL:
            raise ValueError(f"Unsupported registry kind for {entry.brain_id}")
        root = Path(entry.value)
        if not root.is_absolute() or root.is_symlink():
            raise ValueError(f"Unsafe registered Brain path: {root}")
        if plan.read_text(root / ".brain-core" / "VERSION") is None:
            raise ValueError(f"Incomplete inventory: registered Brain is unavailable: {root}")
        root = root.resolve()
        if root in roots:
            raise ValueError(f"Conflicting Brain identities for {root}")
        roots.add(root)
    if selected is not None:
        roots.add(selected.resolve())
    return tuple(sorted(roots, key=str))


def workspace_paths(plan: FilePlan, vault: Path) -> set[Path]:
    """Validate the reverse index without mistaking damaged state for an empty list."""
    from _common._slugs import is_valid_key

    path = vault / ".brain/local/workspaces.json"
    data = registration._json_object(plan, path)
    raw = data.get("workspaces", {})
    if not isinstance(raw, dict):
        raise ValueError(f"Invalid workspace registry: {path}")
    roots = {vault}
    for slug, value in raw.items():
        if not is_valid_key(slug) or not isinstance(value, dict) or not isinstance(value.get("path"), str):
            raise ValueError(f"Workspace registry migration required: {path}")
        root = Path(value["path"])
        if not root.is_absolute() or not root.is_dir():
            raise ValueError(f"Incomplete inventory: workspace {slug} is unavailable: {root}")
        roots.add(root.resolve())
    embedded = vault / "_Workspaces"
    if embedded.exists():
        roots.update(path.resolve() for path in embedded.iterdir() if path.is_dir())
    return roots


def brain_targets(plan: FilePlan, vault: Path, home: Path) -> tuple[RegisteredTarget, ...]:
    roots = workspace_paths(plan, vault)
    _, records = registration.read_records(plan, vault, home, registration.McpScope.PROJECT)
    grouped = {}
    for record in records:
        target = Path(record["target_path"])
        if target.resolve() not in roots:
            raise ValueError(f"Workspace reverse registration requires recovery: {target}")
        registration.plan_target_admission(plan, vault, target)
        key = (target, registration.McpScope(record["scope"]))
        grouped.setdefault(key, set()).add(registration.McpClient(record["client"]))
    return tuple(RegisteredTarget(vault, target, scope, tuple(sorted(clients, key=lambda c: c.value)))
                 for (target, scope), clients in sorted(grouped.items(), key=lambda item: (str(item[0][0]), item[0][1].value)))


def require_owned_native_slots(plan: FilePlan, vault: Path, home: Path, registered: tuple[RegisteredTarget, ...]) -> None:
    """Reject incomplete ownership before any composed repair or removal."""
    owned = {(client, item.scope, item.target) for item in registered for client in item.clients}
    for target_path in workspace_paths(plan, vault):
        for scope in (registration.McpScope.PROJECT, registration.McpScope.LOCAL):
            for client in registration._clients(registration.McpClient.ALL, scope):
                path = registration._config_path(client, scope, target_path, home)
                if (client, scope, target_path) not in owned and registration.observed_server(plan, client, path) is not None:
                    raise ValueError(f"Unowned MCP entry requires explicit migration/admission: {path}")


def plan_repair(plan: FilePlan, vaults: tuple[Path, ...], home: Path, cli_binary: Path, *, runtimes: dict[Path, Path] | None = None):
    """Compose all native target projections plus shared user projections once."""
    from _bootstrap.mcp_state import build_mcp_config

    targets = []
    destinations = {}
    runtimes = dict(runtimes or {})
    clients = set()
    for vault in vaults:
        registered = brain_targets(plan, vault, home)
        require_owned_native_slots(plan, vault, home, registered)
        for target in registered:
            for client in target.clients:
                path = registration._config_path(client, target.scope, target.target, home)
                owner = destinations.setdefault(path, vault)
                if owner != vault:
                    raise ValueError(f"Conflicting registered owners for {path}")
            if vault not in runtimes:
                runtimes[vault] = Path(registration._runtime_python(vault))
            server = build_mcp_config(str(runtimes[vault]), vault, workspace_dir=target.target)
            registration._configure_plan(vault, home, target.target, target.scope, target.clients,
                                         server, plan=plan, repair=True)
            clients.update(target.clients)
            targets.append(str(target.target))
    _, records = registration.read_records(plan, None, home, registration.McpScope.USER)
    user_clients = tuple(sorted({registration.McpClient(record["client"]) for record in records}, key=lambda c: c.value))
    for client in registration._clients(registration.McpClient.ALL, registration.McpScope.USER):
        path = registration._config_path(client, registration.McpScope.USER, None, home)
        if client not in user_clients and registration.observed_server(plan, client, path) is not None:
            raise ValueError(f"Unowned MCP entry requires explicit migration/admission: {path}")
    if user_clients:
        registration._configure_plan(None, home, None, registration.McpScope.USER, user_clients,
                                     registration.stable_server_config(cli_binary), plan=plan, repair=True)
        clients.update(user_clients)
    return tuple(sorted(clients, key=lambda c: c.value)), tuple(sorted(set(targets)))


def inspect_registrations(home: Path, vaults: tuple[Path, ...], cli_binary: Path | None) -> dict:
    """Separate canonical ownership, live projection drift and incomplete coverage."""
    plan = FilePlan()
    findings = []
    slots = {}
    complete = True
    for scope in (registration.McpScope.USER,):
        for client in registration._clients(registration.McpClient.ALL, scope):
            slots[(client, scope, None)] = None
    for vault in vaults:
        try:
            for target_path in workspace_paths(plan, vault):
                for scope in (registration.McpScope.PROJECT, registration.McpScope.LOCAL):
                    for client in registration._clients(registration.McpClient.ALL, scope):
                        slots[(client, scope, target_path)] = vault
            targets = brain_targets(plan, vault, home)
            for target in targets:
                for client in target.clients:
                    slots[(client, target.scope, target.target)] = vault
        except (OSError, ValueError, RuntimeError) as exc:
            complete = False
            findings.append({"state": "migration_required" if "migration" in str(exc).lower() else "incomplete",
                             "path": str(vault), "message": str(exc), "action": "brain mcp migrate --dry-run --json"})
    runtime_cache = {}
    for (client, scope, target), vault in slots.items():
        path = registration._config_path(client, scope, target, home)
        try:
            _, records = registration.read_records(plan, vault, home, scope)
            record = next((item for item in records if item["client"] == client.value and item["scope"] == scope.value
                           and item["target_path"] == (str(target) if target else None)), None)
            current = registration.observed_server(plan, client, path)
            if record is None:
                state = "unowned" if current is not None else "absent"
            elif current is None and record.get("transport_enabled", True):
                state = "missing"
            elif current is not None and (not record.get("transport_enabled", True) or current != record["server_config"]):
                state = "modified"
            else:
                if scope is registration.McpScope.USER:
                    desired = registration.stable_server_config(cli_binary) if cli_binary else None
                else:
                    from _bootstrap.mcp_state import build_mcp_config
                    if vault not in runtime_cache:
                        runtime_cache[vault] = registration._runtime_python(vault)
                    desired = build_mcp_config(runtime_cache[vault], vault, workspace_dir=target)
                if desired is None:
                    state = "stale"
                else:
                    preview = registration._configure_plan(vault, home, target, scope, (client,), desired, repair=True)
                    state = "stale" if preview.changes() else "current" if record.get("transport_enabled", True) else "bootstrap_only"
            findings.append({"client": client.value, "scope": scope.value, "path": str(path), "state": state,
                             "action": f'brain mcp repair --request-json \'{{"scope":"{scope.value}","client":"{client.value}"}}\' --json'})
        except (OSError, ValueError, RuntimeError) as exc:
            complete = False
            findings.append({"client": client.value, "scope": scope.value, "path": str(path), "state": "incomplete", "message": str(exc),
                             "action": "brain mcp migrate --dry-run --json"})
    return {"complete": complete, "healthy": complete and all(item["state"] in ("current", "bootstrap_only", "absent") for item in findings),
            "registrations": findings}


def persisted_runtime_references(home: Path, vaults: tuple[Path, ...]) -> tuple[set[str], bool]:
    """Treat even legacy/unowned native launch references as retention roots, not write authority."""
    plan = FilePlan()
    references = set()
    complete = True
    from _bootstrap.mcp_migration import read_journal
    import base64
    import json

    try:
        journal = read_journal(plan, home)
        if journal and not journal.get("launch_verified", False):
            for change in journal.get("changes", []):
                if change.get("before") is not None and Path(change["path"]).name in ("init-state.json", "mcp-registrations.json"):
                    previous = json.loads(base64.b64decode(change["before"], validate=True))
                    if not isinstance(previous, dict) or not isinstance(previous.get("records"), list):
                        raise ValueError("Invalid retired registration evidence")
                    for record in previous.get("records", []):
                        if not isinstance(record, dict):
                            raise ValueError("Invalid retired registration record")
                        _add_references(references, record.get("server_config", {}))
    except (OSError, ValueError, TypeError, KeyError):
        complete = False
    try:
        _, user_records = registration.read_records(plan, None, home, registration.McpScope.USER)
        for record in user_records:
            _add_references(references, record["server_config"])
    except (OSError, ValueError, RuntimeError):
        complete = False
    slots = {(client, registration.McpScope.USER, None) for client in registration._clients(registration.McpClient.ALL, registration.McpScope.USER)}
    try:
        local_brains(plan)
    except (OSError, ValueError, RuntimeError):
        complete = False
    for vault in vaults:
        try:
            targets = workspace_paths(plan, vault)
            data = registration._json_object(plan, vault / ".brain/local/init-state.json")
            records = data.get("records", [])
            if not isinstance(records, list):
                raise ValueError("Invalid registration records")
            for record in records:
                if not isinstance(record, dict):
                    raise ValueError("Invalid registration record")
                server = record.get("server_config")
                if isinstance(server, dict):
                    _add_references(references, server)
                target = record.get("target_path")
                if isinstance(target, str) and Path(target).is_absolute():
                    targets.add(Path(target))
            for target in targets:
                for scope in (registration.McpScope.PROJECT, registration.McpScope.LOCAL):
                    slots.update((client, scope, target) for client in registration._clients(registration.McpClient.ALL, scope))
        except (OSError, ValueError, RuntimeError):
            complete = False
    for client, scope, target in slots:
        try:
            server = registration.observed_server(plan, client, registration._config_path(client, scope, target, home))
            if server is not None:
                _add_references(references, server)
        except (OSError, ValueError, RuntimeError):
            complete = False
    return references, complete


def _add_references(references: set[str], server: dict) -> None:
    if (not isinstance(server, dict) or not isinstance(server.get("command"), str)
            or not isinstance(server.get("args", []), list)
            or any(not isinstance(argument, str) for argument in server.get("args", []))):
        raise ValueError("Invalid persisted runtime reference")
    for value in [server.get("command"), *server.get("args", [])]:
        if isinstance(value, str) and Path(value).is_absolute():
            references.add(str(Path(value).absolute()))
