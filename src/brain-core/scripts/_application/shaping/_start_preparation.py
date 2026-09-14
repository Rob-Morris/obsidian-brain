"""Session preparation freezes transcript selection and complete lifecycle effects."""

from datetime import datetime

from ..preparation import ObservedResource, OperationPreparation, bind_operation, canonical_json, content_digest
from ..preparation_transition import transition_binding


def session_plan(context, request, router, *, frozen_inputs=None):
    from start_shaping_session import plan_shaping_session
    frozen = dict(frozen_inputs or {})
    frozen.setdefault("effective_at", context.clock.now().isoformat())
    plan = plan_shaping_session(str(context.selected_brain.vault_root), router, request.target,
                                mode=request.mode.value, _now=datetime.fromisoformat(frozen["effective_at"]),
                                chosen_transcript=frozen.get("transcript_path"))
    frozen["transcript_path"] = plan["transcript_path"]
    return plan, frozen


def session_binding(context, request, *, plan, router, frozen_inputs=None):
    from _portable.maintenance_inputs import file_identity

    root = context.selected_brain.vault_root
    target, transcript = plan["prepared"], plan["transcript_path"]
    observations = [ObservedResource("shaping-target", target.resolved_path, file_identity(root / target.resolved_path)),
                    ObservedResource("transcript", transcript, file_identity(root / transcript)),
                    ObservedResource("transcript-template", plan["transcript_type"],
                                     content_digest(plan["template"]) if plan["template"] else None),
                    ObservedResource("shaping-definition", target.artefact["key"],
                                     content_digest(canonical_json(target.artefact)))]
    lifecycle_review = None
    if plan["lifecycle"] is not None:
        binding = transition_binding(context, request, plan=plan["lifecycle"], router=router)
        observations.extend(binding.observations)
        lifecycle_review = binding.review
    return bind_operation(request, observations=observations, frozen_inputs=frozen_inputs,
                          review={"operation": "start-shaping", "target": target.resolved_path,
                                  "transcript": transcript, "append": plan["transcript_exists"],
                                  "mode": plan["mode"], "lifecycle": lifecycle_review})


def prepare_session(context, request, *, frozen_inputs=None):
    from _common import vault_mutation_lock
    from _lifecycle.derived_cache_state import require_fresh_compiled_router
    with vault_mutation_lock(context.selected_brain.vault_root):
        router = require_fresh_compiled_router(context.selected_brain.vault_root)
        plan, frozen = session_plan(context, request, router, frozen_inputs=frozen_inputs)
        return session_binding(context, request, plan=plan, router=router, frozen_inputs=frozen)


SHAPING_SESSION = OperationPreparation(prepare_session)
