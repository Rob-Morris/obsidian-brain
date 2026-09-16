"""Session preparation freezes transcript selection and complete lifecycle effects."""

from datetime import datetime

from ..preparation import ObservedResource, OperationPreparation, bind_operation, canonical_json, content_digest
from ..preparation_transition import transition_binding
from ..workspace_context import resolve_mutation_context, apply_semantic_tags, validate_subject_membership


def session_plan(context, request, router, *, frozen_inputs=None):
    from start_shaping_session import plan_shaping_session, render_transcript_template
    from _common import parse_frontmatter, ensure_parent_tag, canonical_living_artefact_key
    frozen = dict(frozen_inputs or {})
    frozen.setdefault("effective_at", context.clock.now().isoformat())
    now = datetime.fromisoformat(frozen["effective_at"])
    root = context.selected_brain.vault_root
    plan = plan_shaping_session(str(root), router, request.target, mode=request.mode.value,
                               _now=now, chosen_transcript=frozen.get("transcript_path"))
    if plan["transcript_exists"]:
        original, _ = parse_frontmatter((root / plan["transcript_path"]).read_text(encoding="utf-8"))
    else:
        source_path = plan["lifecycle"].result["path"] if plan["lifecycle"] else plan["prepared"].resolved_path
        original, _ = parse_frontmatter(render_transcript_template(
            plan["template"], source_path, plan["prepared"].artefact["key"], now))
    effective = resolve_mutation_context(context, router, request.workspace_context,
        creation=not plan["transcript_exists"], parent=original.get("parent") if plan["transcript_exists"] else None)
    transcript_fields = original
    transcript_fields = apply_semantic_tags(transcript_fields, effective)
    if not plan["transcript_exists"]:
        for field, value in (("workspace", effective.workspace), ("parent", effective.parent)):
            transcript_fields.pop(field, None)
            if value is not None:
                transcript_fields[field] = value
        ensure_parent_tag(transcript_fields)
        plan = plan_shaping_session(str(root), router, request.target, mode=request.mode.value,
            _now=now, _prepared_target=plan["prepared"],
            chosen_transcript=frozen.get("transcript_path"), transcript_fields=transcript_fields)
    target = plan["prepared"]
    reference = canonical_living_artefact_key(target.artefact, target.fields) or target.resolved_path
    lifecycle_fields = (next(item["fields"] for item in plan["lifecycle"].writes
                             if item["path"] == target.resolved_path)
                        if plan["lifecycle"] else target.fields)
    target_fields = apply_semantic_tags(lifecycle_fields, effective)
    validate_subject_membership(router, target_fields, reference, target.fields)
    if plan["transcript_exists"]:
        original, _ = parse_frontmatter((root / plan["transcript_path"]).read_text(encoding="utf-8"))
        transcript_fields = apply_semantic_tags(original, effective)
        validate_subject_membership(router, transcript_fields, plan["transcript_path"], original)
    plan.update(target_fields=target_fields, transcript_fields=transcript_fields,
                mutation_context=effective, reconcile_indexes=True)
    frozen["transcript_path"] = plan["transcript_path"]
    return plan, frozen


def session_binding(context, request, *, plan, router, frozen_inputs=None):
    from _portable.maintenance_inputs import file_identity
    from start_shaping_session import _transcript_artefact

    root = context.selected_brain.vault_root
    target, transcript = plan["prepared"], plan["transcript_path"]
    observations = [ObservedResource("shaping-target", target.resolved_path, file_identity(root / target.resolved_path)),
                    ObservedResource("transcript", transcript, file_identity(root / transcript)),
                    ObservedResource("transcript-template", plan["transcript_type"],
                                     content_digest(plan["template"]) if plan["template"] else None),
                    ObservedResource("shaping-definition", target.artefact["key"],
                                     content_digest(canonical_json(target.artefact)))]
    observations.extend(plan["mutation_context"].sources)
    observations.append(ObservedResource("transcript-definition", plan["transcript_type"],
        content_digest(canonical_json(_transcript_artefact(router)))))
    for fields in (plan["target_fields"], plan["transcript_fields"]):
        parent = fields.get("parent")
        if parent is not None:
            observations.append(ObservedResource("shaping-parent", parent,
                file_identity(root / router["artefact_index"][parent]["path"])))
    lifecycle_review = None
    if plan["lifecycle"] is not None:
        binding = transition_binding(context, request, plan=plan["lifecycle"], router=router)
        observations.extend(binding.observations)
        lifecycle_review = binding.review
    return bind_operation(request, observations=observations, frozen_inputs=frozen_inputs,
                          review={"operation": "start-shaping", "target": target.resolved_path,
                                  "transcript": transcript, "append": plan["transcript_exists"],
                                  "mode": plan["mode"], "lifecycle": lifecycle_review,
                                  "mutation_context": plan["mutation_context"].review(),
                                  "target_fields": plan["target_fields"], "transcript_fields": plan["transcript_fields"]})


def prepare_session(context, request, *, frozen_inputs=None):
    from _common import vault_mutation_lock
    from _lifecycle.derived_cache_state import require_fresh_compiled_router
    with vault_mutation_lock(context.selected_brain.vault_root):
        router = require_fresh_compiled_router(context.selected_brain.vault_root)
        plan, frozen = session_plan(context, request, router, frozen_inputs=frozen_inputs)
        return session_binding(context, request, plan=plan, router=router, frozen_inputs=frozen)


SHAPING_SESSION = OperationPreparation(prepare_session)


def session_effect_snapshot(context, plan):
    from ..workspace_transitions import transition_effect_snapshot
    from _portable.maintenance_inputs import file_identity
    observed = transition_effect_snapshot(context, plan["lifecycle"]) if plan["lifecycle"] else {}
    paths = set(observed) | {plan["prepared"].resolved_path, plan["transcript_path"]}
    return {path: file_identity(context.selected_brain.vault_root / path) for path in paths}


def committed_session_effects(context, request, plan, before):
    from ..receipts import CommittedEffect
    after = session_effect_snapshot(context, plan)
    changed = {path for path, revision in after.items() if revision != before[path]}
    if plan["lifecycle"]:
        for move in plan["lifecycle"].movement.moves:
            if move["source"] in changed and move["dest"] in changed and after[move["source"]] is None:
                changed.discard(move["source"])
    return tuple(CommittedEffect(request.COMMAND_ID, path) for path in sorted(changed))
