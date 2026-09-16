"""Plan and apply canonical workspace registration without command recursion."""

from dataclasses import dataclass
from datetime import datetime

from ..preparation import ObservedResource, content_digest, canonical_json


@dataclass(frozen=True, slots=True)
class WorkspaceRegistration:
    reference: str
    path: str
    status: str


def plan_registration(context, key, title=None, *, frozen_inputs=None):
    from _common._workspace import require_workspace, workspace_policy, workspace_reference
    from _lifecycle.derived_cache_state import require_fresh_compiled_router
    from ..preparation_creation import creation_binding
    from .ensure_registration import WorkspaceEnsureRegistrationRequest
    import create

    root = context.selected_brain.vault_root
    router = require_fresh_compiled_router(str(root))
    reference = workspace_reference(key, bare=True)
    frozen = dict(frozen_inputs or {})
    observations = [ObservedResource("workspace-index", reference,
        content_digest(canonical_json(router.get("artefact_index", {}))))]
    entry = router.get("artefact_index", {}).get(reference)
    if entry is not None:
        entry = require_workspace(router, reference, active=True)
        workspace_policy(router, reference, entry)
        path = root / entry["path"]
        observations.append(ObservedResource("workspace-file", str(path), content_digest(path.read_bytes())))
        return router, WorkspaceRegistration(reference, entry["path"], "attached"), None, observations, frozen
    choice = frozen.get("registration", {})
    plan = create.plan_artefact_creation(str(root), router, "living/workspace",
        title or key, key=key, frontmatter_overrides={"workspace_mode": "linked"},
        effective_at=datetime.fromisoformat(choice["effective_at"]) if choice else context.clock.now(),
        chosen_filename=choice.get("filename"))
    frozen["registration"] = {"effective_at": plan.effective_at, "filename": plan.path.rsplit("/", 1)[-1]}
    # Reuse the creation boundary's definition/template/content observations.
    binding = creation_binding(context, WorkspaceEnsureRegistrationRequest(key, title), plan=plan,
                               frozen_inputs=frozen)
    observations.extend(binding.observations)
    return router, WorkspaceRegistration(reference, plan.path, "created"), plan, observations, frozen


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
