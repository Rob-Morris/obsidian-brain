"""Plan and apply canonical workspace registration without command recursion."""

from dataclasses import dataclass
from datetime import datetime

from ..preparation import ObservedResource, content_digest, canonical_json


class MissingWorkspaceRegistration(ValueError):
    """Folder binding cannot create its missing workspace implicitly."""
    def __init__(self, root, key):
        import json
        import shlex

        self.key = key
        command = (f"brain workspace create --vault {shlex.quote(str(root))} "
                   f"--request-json {shlex.quote(json.dumps({'key': key}))}")
        super().__init__(f"workspace/{key} does not exist in the selected Brain. "
                         f"Create it explicitly with: {command}; then retry workspace register.")


@dataclass(frozen=True, slots=True)
class WorkspaceRegistration:
    reference: str
    path: str
    status: str


def inspect_registration(context, key):
    from _common import read_exact_file_content
    from _common._workspace import require_workspace, workspace_policy, workspace_reference
    from _lifecycle.derived_cache_state import require_fresh_compiled_router
    root = context.selected_brain.vault_root
    router = require_fresh_compiled_router(str(root))
    reference = workspace_reference(key, bare=True)
    observations = [ObservedResource("workspace-index", f"{root.resolve()}#{reference}",
        content_digest(canonical_json(router.get("artefact_index", {}))))]
    entry = router.get("artefact_index", {}).get(reference)
    if entry is not None:
        entry = require_workspace(router, reference, active=True)
        workspace_policy(router, reference, entry)
        path = root / entry["path"]
        read_exact_file_content(path)
        observations.append(ObservedResource("workspace-file", str(path), content_digest(path.read_bytes())))
        return router, WorkspaceRegistration(reference, entry["path"], "attached"), observations
    require_readable_workspace_identity(root, router, key)
    return router, None, observations


def require_registration(context, key):
    """Resolve an active canonical workspace without creating content."""
    router, registration, observations = inspect_registration(context, key)
    if registration is None:
        raise MissingWorkspaceRegistration(context.selected_brain.vault_root, key)
    return router, registration, observations


def plan_registration(context, key, title=None, *, frozen_inputs=None):
    from ..preparation_creation import creation_binding
    from .create import WorkspaceCreateRequest
    import create

    root = context.selected_brain.vault_root
    router, registration, observations = inspect_registration(context, key)
    frozen = dict(frozen_inputs or {})
    if registration is not None:
        return router, registration, None, observations, frozen
    choice = frozen.get("registration", {})
    plan = create.plan_artefact_creation(str(root), router, "living/workspace",
        title or key, key=key, frontmatter_overrides={"workspace_mode": "linked"},
        effective_at=datetime.fromisoformat(choice["effective_at"]) if choice else context.clock.now(),
        chosen_filename=choice.get("filename"))
    frozen["registration"] = {"effective_at": plan.effective_at, "filename": plan.path.rsplit("/", 1)[-1]}
    # Reuse the creation boundary's definition/template/content observations.
    binding = creation_binding(context, WorkspaceCreateRequest(key, title), plan=plan,
                               frozen_inputs=frozen)
    observations.extend(binding.observations)
    return router, WorkspaceRegistration("workspace/" + key, plan.path, "created"), plan, observations, frozen


def apply_registration(context, router, registration, plan, effects):
    """Record content effects before reconciling fallible derived indexes."""
    if plan is None:
        return
    import create
    from ..receipts import CommittedEffect
    from .._transition_indexes import reconcile_transition_indexes

    create.apply_artefact_creation(str(context.selected_brain.vault_root), router, plan)
    effects.append(CommittedEffect("workspace.registered", registration.path))
    reconcile_transition_indexes(context)


def require_readable_workspace_identity(root, router, key):
    """Name an encoded canonical hub omitted from the strict identity index."""
    from _common import read_exact_file_content, parse_frontmatter, NonStandardVaultTextError, iter_artefact_paths
    for artefact in router.get("artefacts", ()):
        if artefact.get("frontmatter_type") != "living/workspace":
            continue
        for relative in iter_artefact_paths(str(root), artefact):
            content = read_exact_file_content(root / relative, convert_lossless=True)
            fields, _ = parse_frontmatter(content)
            if fields.get("key") == key and content.conversion_code:
                raise NonStandardVaultTextError(relative, content.conversion_code)


def observe_path_claims(root, folder, *, allowed_roots=()):
    """Refuse foreign ownership using the existing machine Brain inventory."""
    from pathlib import Path
    from _bootstrap.file_transaction import FilePlan
    from _bootstrap.workspace_claims import inspect_path_claims
    from ._preparation import _observe_file

    plan = FilePlan()
    inspection = inspect_path_claims(plan, root, folder, allowed_roots=allowed_roots)
    inspection.require_unclaimed()
    observations = tuple(_observe_file(file) for file in inspection.observed_files)
    plan.validate()
    return observations


def unique_observations(observations):
    """Combine independently read boundaries only when their evidence agrees."""
    unique = {}
    for observation in observations:
        key = observation.kind, observation.identity
        if key in unique and unique[key] != observation:
            raise ValueError(f"Workspace evidence changed during planning: {observation.identity}; retry")
        unique[key] = observation
    return tuple(unique.values())
