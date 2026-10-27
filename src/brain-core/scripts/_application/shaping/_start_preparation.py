"""Session preparation freezes transcript selection and complete lifecycle effects."""

from datetime import datetime
from dataclasses import replace

from ..preparation import ObservedResource, OperationPreparation, bind_operation, canonical_json, content_digest
from ..preparation_transition import transition_binding
from ..workspace_context import resolve_mutation_context, apply_semantic_tags, validate_subject_membership
from .._decoding import OMITTED


def session_plan(context, request, router, *, frozen_inputs=None):
    from _common._source_access import resolve_content_source
    from start_shaping_session import plan_shaping_session, render_transcript_template
    from _common import parse_frontmatter, ensure_parent_tag, canonical_living_artefact_key, read_exact_file_content
    frozen = dict(frozen_inputs or {})
    frozen.setdefault("effective_at", context.clock.now().isoformat())
    now = datetime.fromisoformat(frozen["effective_at"])
    root = context.selected_brain.vault_root
    plan = plan_shaping_session(str(root), router, request.target, mode=request.mode.value,
                               _now=now, chosen_transcript=frozen.get("transcript_path"))
    if plan["transcript_exists"]:
        original, _ = parse_frontmatter(read_exact_file_content(resolve_content_source(root, plan["transcript_path"]), convert_lossless=True))
    else:
        source_path = plan["lifecycle"].result["path"] if plan["lifecycle"] else plan["prepared"].resolved_path
        original, _ = parse_frontmatter(render_transcript_template(
            plan["template"], source_path, plan["prepared"].artefact["key"], now))
    effective = resolve_mutation_context(context, router, request.workspace_context,
        creation=not plan["transcript_exists"], parent=original.get("parent") if plan["transcript_exists"] else OMITTED)
    from ..workspace_associations import observe_workspace_associations
    associations = (observe_workspace_associations(context, router, original.get("tags", ()))
                    if not plan["transcript_exists"] else ())
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
        original, _ = parse_frontmatter(read_exact_file_content(resolve_content_source(root, plan["transcript_path"]), convert_lossless=True))
        transcript_fields = apply_semantic_tags(original, effective)
        validate_subject_membership(router, transcript_fields, plan["transcript_path"], original)
    from _common._workspace import workspace_discovery_metadata
    effective = replace(effective, sources=effective.sources + associations,
        relevant_workspaces=tuple(sorted({reference for fields in (target_fields, transcript_fields)
            for reference in workspace_discovery_metadata(fields)["relevant_workspaces"]})))
    plan.update(target_fields=target_fields, transcript_fields=transcript_fields,
                mutation_context=effective, reconcile_indexes=True)
    plan["semantic_transition"] = semantic_transition(context, router, plan)
    frozen["transcript_path"] = plan["transcript_path"]
    return plan, frozen


def semantic_transition(context, router, plan):
    """Adapt final session writes to the existing native semantic plan owner."""
    from _common._source_access import resolve_content_source
    import os
    import edit
    from _common import parse_frontmatter, serialize_frontmatter, read_exact_file_content
    from start_shaping_session import render_transcript_link
    target, lifecycle = plan["prepared"], plan["lifecycle"]
    writes = {item["path"]: dict(item) for item in lifecycle.writes} if lifecycle else {}
    body = writes.get(target.resolved_path, {"body": target.body})["body"]
    target_path = lifecycle.result["path"] if lifecycle else target.resolved_path
    if lifecycle:
        link_write = next((item for item in lifecycle.movement.links.writes if item.path in (target.resolved_path, target_path)), None)
        if link_write:
            _, body = parse_frontmatter(link_write.after)
    fields = dict(plan["target_fields"])
    content = render_transcript_link(fields, body, os.path.splitext(plan["transcript_path"])[0],
                                    os.path.splitext(os.path.basename(plan["transcript_path"]))[0], plan["now"].isoformat())
    original_fields = writes.get(target.resolved_path, {"fields": target.fields})["fields"]
    if content is None and original_fields != plan["target_fields"]:
        content = serialize_frontmatter(fields, body=body)
    if content is not None:
        fields, body = parse_frontmatter(content)
        writes[target.resolved_path] = {"path": target.resolved_path, "fields": fields, "body": body}
    if plan["transcript_exists"]:
        path = plan["transcript_path"]
        content = read_exact_file_content(resolve_content_source(context.selected_brain.vault_root, path))
        original, body = parse_frontmatter(content)
        if original != plan["transcript_fields"]:
            content = serialize_frontmatter(plan["transcript_fields"], body=body)
        content = content.rstrip() + f"\n\n## {plan['mode'].capitalize()} session start — {plan['now'].strftime('%H:%M')}\n"
        fields, body = parse_frontmatter(content)
        writes[path] = {"path": path, "fields": fields, "body": body}
    return edit.plan_artefact_transition(str(context.selected_brain.vault_root), router,
        operation="shaping.start", writes=tuple(writes.values()), moves=lifecycle.movement.moves if lifecycle else (),
        result=lifecycle.result if lifecycle else {"path": target.resolved_path})


def session_binding(context, request, *, plan, router, frozen_inputs=None):
    from _portable.maintenance_inputs import public_file_identity
    from start_shaping_session import _transcript_artefact

    root = context.selected_brain.vault_root
    target, transcript = plan["prepared"], plan["transcript_path"]
    observations = [ObservedResource("shaping-target", target.resolved_path, public_file_identity(root, target.resolved_path)),
                    ObservedResource("transcript", transcript, public_file_identity(root, transcript)),
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
                public_file_identity(root, router["artefact_index"][parent]["path"])))
    lifecycle_review = None
    if plan["semantic_transition"] is not None:
        binding = transition_binding(context, request, plan=plan["semantic_transition"], router=router, frozen_inputs=frozen_inputs)
        frozen_inputs = binding.frozen_inputs
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
    """Merge session documents with the shared lifecycle source snapshot."""
    from ..workspace_transitions import (
        TransitionSnapshot, transition_effect_snapshot, source_effect_snapshot,
    )
    observed = (transition_effect_snapshot(context, plan["lifecycle"])
                if plan["lifecycle"] else TransitionSnapshot({}, ()))
    paths = {plan["prepared"].resolved_path, plan["transcript_path"]} - observed.revisions.keys()
    extra = source_effect_snapshot(context, paths)
    return TransitionSnapshot({**observed.revisions, **extra.revisions}, observed.conversions)


def committed_session_effects(context, request, plan, before):
    from _common._source_access import SourceAccessError
    from ..receipts import CommittedEffect
    try:
        after = session_effect_snapshot(context, plan).revisions
    except (SourceAccessError, OSError) as exc:
        # This observer runs after the mutation has entered its effect phase.
        raise RuntimeError("Shaping session effects could not be observed safely") from exc
    changed = {path for path, revision in after.items() if revision != before.revisions[path]}
    if plan["lifecycle"]:
        for move in plan["lifecycle"].movement.moves:
            if move["source"] in changed and move["dest"] in changed and after[move["source"]] is None:
                changed.discard(move["source"])
    return tuple(CommittedEffect(request.COMMAND_ID, path) for path in sorted(changed))
