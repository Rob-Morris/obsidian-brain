"""Caller-local workspace compatibility upgrades and bounded discovery."""

from __future__ import annotations

import ast
import base64
from contextlib import ExitStack
from dataclasses import dataclass, field
from enum import Enum
import json
import hashlib
import os
from pathlib import Path
import shlex
from typing import ClassVar

from .context import LauncherContext
from .contracts import (
    CommandError, CommandWarning, CommittedEffect, ErrorCode, Ok, Partial,
    RequestErrorDetails, WarningCode, no_effect_error,
)


class UpgradeScope(str, Enum):
    FOLDER = "folder"
    BRAIN = "brain"
    MACHINE = "machine"


class RegistrationConflict(ValueError):
    """Known inventories make contradictory claims; never select a winner."""


@dataclass(frozen=True, slots=True)
class WorkspaceOutcome:
    path: str
    status: str
    brain: str | None = None
    slug: str | None = None
    source_version: int | None = None
    target_version: int = 1
    files: tuple[str, ...] = ()
    message: str = ""
    command: str | None = None


@dataclass(frozen=True, slots=True)
class WorkspaceUpgradePayload:
    scope: UpgradeScope
    complete: bool
    folders: tuple[WorkspaceOutcome, ...]
    coverage: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkspaceUpgradeProgress(RequestErrorDetails):
    """Keep typed folder outcomes and recovery evidence on partial results."""
    progress: WorkspaceUpgradePayload
    recovery_paths: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DiscoveredWorkspace:
    path: str
    registration: str
    brain: str | None
    slug: str | None
    source_version: int | None
    compatibility: str
    command: str | None
    message: str = ""


@dataclass(frozen=True, slots=True)
class WorkspaceDiscoverPayload:
    roots: tuple[str, ...]
    complete: bool
    folders: tuple[DiscoveredWorkspace, ...]
    coverage: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkspaceUpgradeRequest:
    COMMAND_ID: ClassVar[str] = "workspace.upgrade"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = WorkspaceUpgradePayload

    scope: UpgradeScope = UpgradeScope.FOLDER

    def __post_init__(self):
        if not isinstance(self.scope, UpgradeScope):
            raise ValueError("scope must be folder, brain or machine")


@dataclass(frozen=True, slots=True)
class WorkspaceDiscoverRequest:
    COMMAND_ID: ClassVar[str] = "workspace.discover"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = WorkspaceDiscoverPayload

    roots: tuple[Path, ...] = field(metadata={"example": (Path(os.path.abspath(os.sep)) / "workspace-root",)})

    def __post_init__(self):
        if not self.roots or any(not isinstance(root, Path) or not root.is_absolute() for root in self.roots):
            raise ValueError("roots must contain explicit absolute local directories")


def _command(verb, *, vault=None, folder=None, request=None):
    parts = ["brain", "workspace", verb]
    if vault is not None:
        parts.extend(("--vault", str(vault)))
    if folder is not None:
        parts.extend(("--workspace", str(folder)))
    if request is not None:
        parts.extend(("--request-json", json.dumps(request, separators=(",", ":"))))
    return shlex.join(parts)


def _manifest(plan, folder):
    from _bootstrap.workspace_binding import manifest_path_for, legacy_manifest_path_for
    from _common._yaml import load_mapping_text

    current, legacy = manifest_path_for(folder), legacy_manifest_path_for(folder)
    current_text, legacy_text = plan.read_text(current), plan.read_text(legacy)
    if current_text is not None and legacy_text is not None and current_text != legacy_text:
        raise ValueError(f"Conflicting current and legacy workspace manifests: {folder}")
    source = current if current_text is not None else legacy if legacy_text is not None else None
    text = current_text if current_text is not None else legacy_text
    return source, current, legacy, load_mapping_text(text, source=str(source)) if text is not None else None


def _contract(plan, vault, override=None):
    from _bootstrap.workspace_schema import SCHEMA_VERSION

    core = override or vault / ".brain-core"
    path = core / "scripts/_bootstrap/workspace_schema.py"
    text = plan.read_text(path)
    if text is None:
        raise ValueError(f"Brain {vault} has no supported workspace schema contract; run brain upgrade --vault {shlex.quote(str(vault))}")
    tree = ast.parse(text, filename=str(path))
    versions = [node.value.value for node in tree.body if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "SCHEMA_VERSION" for target in node.targets)
                and isinstance(node.value, ast.Constant)]
    functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    if versions != [SCHEMA_VERSION] or "plan_workspace_migration" not in functions:
        raise ValueError(f"Brain {vault} requires a compatible CLI/runtime upgrade; workspace schema contract is unsupported")


def _rows(plan, vault):
    import workspace_registry

    path = vault / ".brain/local/workspaces.json"
    raw = plan.read_bytes(path)
    rows = workspace_registry.decode_registry(raw) if raw is not None else {}
    if workspace_registry.canonical_path(vault) in rows:
        raise ValueError(f"Vault-self is not a workspace registration: {path}")
    return rows


def _inventory(plan, selected=None):
    from _bootstrap.mcp_inventory import local_brains

    unreachable = []
    roots = local_brains(plan, selected, unreachable=unreachable)
    coverage = [str(item) for item in unreachable]
    rows_by_brain, claims = {}, {}
    for vault in roots:
        rows = _rows(plan, vault)
        rows_by_brain[vault] = rows
        for path, row in rows.items():
            if path in claims and claims[path] != (vault, row["slug"]):
                raise RegistrationConflict(f"Conflicting workspace registration claims for {path}")
            claims[path] = (vault, row["slug"])
    return rows_by_brain, claims, coverage


def _identity(plan, vault, brain, slug):
    import vault_registry
    from _common._workspace import require_workspace
    from _lifecycle.derived_cache_state import require_fresh_compiled_router

    path = Path(vault_registry.registry_path())
    plan.read_bytes(path)
    resolved = vault_registry.require_live(brain)
    if resolved is None or Path(resolved).resolve() != vault.resolve():
        raise ValueError(f"Manifest Brain {brain!r} does not resolve to selected Brain {vault}; resolve the binding explicitly")
    router_path = vault / ".brain/local/compiled-router.json"
    plan.read_bytes(router_path)
    router = require_fresh_compiled_router(vault)
    try:
        entry = require_workspace(router, "workspace/" + slug, active=True)
    except ValueError as exc:
        raise ValueError(f"{exc}. Create/reactivate explicitly: {_command('create', vault=vault, request={'key': slug})}") from exc
    hub = vault / entry["path"]
    plan.read_bytes(hub)


def _cross_brain_claims(plan, selected, folder, *, strict=False):
    """Observe other registries without freezing this operation's own row write."""
    from _bootstrap.workspace_claims import inspect_path_claims, WorkspaceClaimConflict

    inspection = inspect_path_claims(plan, selected, folder, require_complete=strict)
    try:
        inspection.require_unclaimed()
    except WorkspaceClaimConflict as exc:
        raise RegistrationConflict(str(exc)) from exc
    return inspection


def _stage_folder(plan, vault, folder, *, expected_slug=None, contract_override=None):
    import workspace_registry
    from _bootstrap.workspace_schema import plan_workspace_migration
    from _common._yaml import dump_mapping_text

    if folder == vault:
        return WorkspaceOutcome(str(folder), "noop", message="Vault-self has no workspace schema."), None
    if not folder.is_dir():
        raise ValueError(f"Registered workspace folder is unavailable: {folder}; reconnect it and rerun workspace upgrade")
    plan.observe_directory(folder)
    _contract(plan, vault, contract_override)
    source, current, legacy, data = _manifest(plan, folder)
    migration = plan_workspace_migration(data)
    state = migration.classification
    if data is None:
        raise ValueError(f"Workspace manifest is missing: {folder}; register explicitly with a canonical slug")
    if expected_slug is not None and (not state.linked or state.slug != expected_slug):
        raise ValueError(f"Manifest and registered canonical identity disagree at {folder}; resolve explicitly before upgrading")
    if state.linked:
        _identity(plan, vault, state.brain, state.slug)
        workspace_registry.stage_link_row(plan, vault, state.slug, folder)
    elif expected_slug is not None:
        raise ValueError(f"Unlinked manifest cannot substantiate a registration: {folder}")
    if migration.changed or source != current:
        plan.write_text(current, dump_mapping_text(migration.payload))
    if source == legacy or plan.read_bytes(legacy) is not None:
        plan.delete(legacy)
    files = tuple(str(change.path) for change in plan.changes())
    return WorkspaceOutcome(str(folder), "planned" if files else "noop", state.brain,
                            state.slug, state.source_version, files=files), migration


def _apply_folder(context, vault, folder, *, expected_slug=None, contract_override=None, strict_inventory=False):
    from _bootstrap.file_transaction import FilePlan, FileTransactionError, apply_file_changes
    from _bootstrap.file_lock import vault_mutation_lock
    from _bootstrap.mcp_registration import registration_lock

    # Freeze exact files/directory identities before acquiring mutation locks.
    plan = FilePlan()
    result, _ = _stage_folder(plan, vault, folder, expected_slug=expected_slug,
                              contract_override=contract_override)

    with ExitStack() as locks:
        locks.enter_context(registration_lock(context.home_dir))
        # Inventory can change after the outer breadth inspection. Re-read it
        # under the coordination lock and retain its observations in this plan.
        inventory = FilePlan()
        _cross_brain_claims(inventory, vault, folder, strict=strict_inventory)
        inventory.validate()
        plan.validate()
        locks.enter_context(vault_mutation_lock(vault))
        if folder != vault:
            locks.enter_context(vault_mutation_lock(folder))
        plan.validate()
        changes = plan.changes()
        if context.dry_run or not changes:
            return result, ()
        # Recovery material shares the existing bounded launcher-outcome store;
        # it is evidence, not another registration or transaction authority.
        from .context import launcher_state_home
        token = hashlib.sha256(str(folder).encode()).hexdigest()[:16]
        recovery = launcher_state_home() / "brain/command-outcomes" / f"{context.invocation_id}.workspace-{token}.json"
        backup = FilePlan()
        backup.write_text(recovery, json.dumps({"schema": "brain.workspace-upgrade-recovery/1",
            "invocation_id": context.invocation_id,
            "files": [{"path": str(change.path),
                       "before": base64.b64encode(change.before).decode() if change.before is not None else None,
                       "after_sha256": hashlib.sha256(change.after).hexdigest() if change.after is not None else None}
                      for change in changes]}, sort_keys=True) + "\n")
        def revalidate():
            inventory.validate()
            plan.validate_dependencies()
        apply_file_changes(backup.changes(), before_write=revalidate)
        try:
            apply_file_changes(changes, before_write=revalidate)
        except (OSError, ValueError, FileTransactionError) as exc:
            raise FileTransactionError(f"{exc}. Original bytes retained in {recovery}; inspect before retrying.",
                                       tuple({recovery, *getattr(exc, 'surviving_paths', ())})) from exc
        from dataclasses import replace
        return replace(result, status="changed"), tuple(
            CommittedEffect("workspace.upgrade", f"file:{path}") for path in [recovery, *(change.path for change in changes)])


def execute_upgrade(context: LauncherContext, request: WorkspaceUpgradeRequest, *, contract_override=None):
    from _bootstrap.file_transaction import FilePlan, FileTransactionError
    from _bootstrap.file_lock import MutationLockError

    vault = context.current_vault
    if request.scope is not UpgradeScope.MACHINE and vault is None:
        return no_effect_error(type(request), ErrorCode.INVALID_REQUEST, "Select an installed Brain for folder or Brain scope.")
    try:
        if request.scope is UpgradeScope.FOLDER:
            folder = (context.workspace_dir or context.caller_dir).resolve()
            rows = _rows(FilePlan(), vault)
            # Exact folder recovery remains available with incomplete machine
            # coverage. Known contradictory claims still forbid this mutation.
            try:
                _inventory(FilePlan(), vault)
            except RegistrationConflict:
                raise
            except (OSError, ValueError):
                pass
            selected = rows.get(str(folder))
            candidates = [(vault, folder, selected["slug"] if selected else None)]
            coverage = []
        else:
            inspection = FilePlan()
            if request.scope is UpgradeScope.MACHINE:
                by_brain, _, coverage = _inventory(inspection, vault)
            else:
                by_brain, coverage = {vault: _rows(inspection, vault)}, []
            # Inspect every registry before admitting any folder writes.
            candidates = [(brain, Path(row["path"]), row["slug"])
                          for brain, rows in by_brain.items() for row in rows.values()]
            for brain in by_brain:
                try:
                    _contract(inspection, brain, contract_override)
                except (OSError, ValueError) as exc:
                    coverage.append(str(exc))
            inspection.validate()
    except (OSError, ValueError) as exc:
        return no_effect_error(type(request), ErrorCode.CONFLICT, str(exc))
    outcomes, effects, recovery = [], [], []
    for brain, folder, slug in sorted(candidates, key=lambda item: (str(item[0]), str(item[1]))):
        try:
            if context.dry_run:
                preview = FilePlan()
                _cross_brain_claims(preview, brain, folder, strict=request.scope is UpgradeScope.MACHINE)
                outcome, _ = _stage_folder(preview, brain, folder, expected_slug=slug,
                                            contract_override=contract_override)
                preview.validate()
                committed = ()
            else:
                outcome, committed = _apply_folder(context, brain, folder, expected_slug=slug,
                                                    contract_override=contract_override,
                                                    strict_inventory=request.scope is UpgradeScope.MACHINE)
            outcomes.append(outcome)
            effects.extend(committed)
        except (OSError, ValueError, RuntimeError, MutationLockError, FileTransactionError) as exc:
            surviving = tuple(str(path) for path in getattr(exc, "surviving_paths", ()))
            recovery.extend(surviving)
            effects.extend(CommittedEffect(request.COMMAND_ID, f"file:{path}") for path in surviving)
            outcomes.append(WorkspaceOutcome(str(folder), "blocked", slug=slug, message=str(exc),
                                             command=_command("upgrade", vault=brain, folder=folder)))
    complete = not coverage and all(row.status != "blocked" for row in outcomes)
    payload = WorkspaceUpgradePayload(request.scope, complete, tuple(outcomes), tuple(coverage))
    if not complete:
        message = "Workspace upgrade coverage is incomplete: " + "; ".join(
            [*coverage, *(row.message for row in outcomes if row.status == "blocked")])
        if effects:
            return Partial(request.COMMAND_ID, request.COMMAND_VERSION,
                           CommandError(ErrorCode.CONFLICT, message,
                                        WorkspaceUpgradeProgress(None, message, payload, tuple(recovery))), tuple(effects))
        return Ok(request.COMMAND_ID, request.COMMAND_VERSION, payload, warnings=(
            CommandWarning(WarningCode.FOLLOW_UP_REQUIRED, message),))
    return Ok(request.COMMAND_ID, request.COMMAND_VERSION, payload, tuple(effects))


_PRUNED = frozenset({".git", ".brain", ".brain-core", ".venv", "venvs", "node_modules", "__pycache__"})


def execute_discover(context: LauncherContext, request: WorkspaceDiscoverRequest):
    from _bootstrap.file_transaction import FilePlan
    from _bootstrap.workspace_schema import classify_workspace_manifest

    coverage, claims, by_brain = [], {}, {}
    try:
        by_brain, claims, missing = _inventory(FilePlan(), context.current_vault)
        coverage.extend(missing)
    except (OSError, ValueError) as exc:
        coverage.append(str(exc))
    inventory_complete = not coverage
    found, visited = [], set()
    roots = tuple(sorted(set(request.roots), key=str))
    for root in roots:
        if root.is_symlink() or not root.is_dir():
            coverage.append(f"Discovery root is not a readable non-symlink directory: {root}")
            continue
        def traversal_error(exc):
            coverage.append(f"Discovery could not inspect {exc.filename}: {exc.strerror}")
        if root in visited:
            continue
        for directory, dirs, _files in os.walk(root, topdown=True, followlinks=False, onerror=traversal_error):
            folder = Path(directory)
            if folder in visited:
                dirs[:] = []
                continue
            dirs[:] = sorted(name for name in dirs if name not in _PRUNED and not (folder / name).is_symlink())
            visited.add(folder)
            if (folder / ".brain-core/VERSION").is_file():
                dirs[:] = []
                continue
            try:
                source, _, _, manifest = _manifest(FilePlan(), folder)
                if source is None:
                    continue
                state = classify_workspace_manifest(manifest)
                claim = claims.get(str(folder.resolve()))
                status = "registered" if claim else "unregistered" if inventory_complete else "unknown"
                message, command = "", None
                import vault_registry
                resolved = vault_registry.require_live(state.brain) if state.linked else None
                if claim and (resolved is None or claim[0] != Path(resolved).resolve() or claim[1] != state.slug):
                    status, message = "conflict", "Manifest and registered identity disagree; explicit resolution is required."
                elif state.linked:
                    if resolved is not None:
                        vault = Path(resolved)
                        try:
                            _identity(FilePlan(), vault, state.brain, state.slug)
                        except (ValueError, RuntimeError) as exc:
                            message = str(exc)
                        else:
                            command = (_command("upgrade", vault=vault, folder=folder)
                                       if state.compatibility.value == "legacy" or claim
                                       else _command("register", vault=vault, folder=folder, request={"slug": state.slug}))
                else:
                    message = "Choose a Brain and canonical slug, then explicitly create/register; no identity is inferred."
                found.append(DiscoveredWorkspace(str(folder), status, state.brain, state.slug,
                                                state.source_version, state.compatibility.value, command, message))
            except (OSError, ValueError, RuntimeError) as exc:
                coverage.append(f"Could not classify workspace at {folder}: {exc}")
                found.append(DiscoveredWorkspace(str(folder), "unknown", None, None, None,
                                                "unsupported", None, str(exc)))
    payload = WorkspaceDiscoverPayload(tuple(str(root) for root in roots), not coverage,
                                       tuple(sorted(found, key=lambda row: row.path)), tuple(coverage))
    warnings = (CommandWarning(WarningCode.FOLLOW_UP_REQUIRED, "; ".join(coverage)),) if coverage else ()
    return Ok(request.COMMAND_ID, request.COMMAND_VERSION, payload, warnings=warnings)


def upgrade_owner():
    from .owners import LauncherOwner
    return LauncherOwner(WorkspaceUpgradeRequest, WorkspaceUpgradePayload,
                         "_launcher.workspace:upgrade", execute_upgrade)


def discover_owner():
    from .owners import LauncherOwner
    return LauncherOwner(WorkspaceDiscoverRequest, WorkspaceDiscoverPayload,
                         "_launcher.workspace:discover", execute_discover)


def invoke_upgrade_phase(context, *, dry_run=False, source_core=None):
    """Admit and receipt the post-runtime phase as its own local invocation."""
    from dataclasses import replace
    from .adapter import project_launcher_result
    from .invocation import LauncherInvocation
    from .owners import LAUNCHER_OWNERS, LauncherOwners, LauncherOwner
    from launcher_catalogue import LAUNCHER_CATALOGUE

    child = replace(context, invocation_id=context.invocation_id + "-workspaces", dry_run=dry_run)
    owners = LAUNCHER_OWNERS
    if dry_run and source_core is not None:
        # Preview the contract that runtime provisioning will make available.
        owners = LauncherOwners(tuple(LauncherOwner(
            row.request_type, row.result_type, row.owner_ref,
            lambda ctx, req: execute_upgrade(ctx, req, contract_override=source_core),
        ) if row.command_id == WorkspaceUpgradeRequest.COMMAND_ID else row for row in owners.entries))
    result = LauncherInvocation(child, LAUNCHER_CATALOGUE, owners).invoke(WorkspaceUpgradeRequest(UpgradeScope.BRAIN))
    envelope = project_launcher_result(result).structured_content
    complete = isinstance(result, Ok) and result.result.complete
    return {"outcome": "planned" if dry_run and complete else "ok" if complete else "partial",
            "invocation_id": child.invocation_id, "result": envelope,
            "message": "Workspace metadata compatibility phase completed." if complete else
                       "Workspace metadata needs follow-up; inspect the child receipt and run the pinned workspace upgrade command."}
