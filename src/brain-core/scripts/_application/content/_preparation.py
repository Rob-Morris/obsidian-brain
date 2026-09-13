"""Freeze ingestion naming and bind the selected create or append operation."""

from datetime import datetime
from pathlib import Path

from ..preparation import ObservedResource, OperationPreparation, bind_operation, canonical_json, content_digest, prepare_content


def plan_ingest(context, request, router, retrieval, body, *, frozen_inputs=None):
    import process
    frozen = dict(frozen_inputs or {})
    choice = frozen.get("creation", {})
    now = datetime.fromisoformat(choice["effective_at"]) if choice else context.clock.now()
    plan = process.plan_ingestion(
        router, str(context.selected_brain.vault_root), body, title=request.title,
        type_hint=request.type_key, index=retrieval.index,
        type_embeddings=retrieval.type_embeddings, type_embeddings_meta=retrieval.metadata,
        doc_embeddings=retrieval.doc_embeddings, doc_embeddings_meta=retrieval.metadata,
        classification_mode=request.mode.value, effective_at=now,
        chosen_filename=choice.get("filename"), chosen_key=choice.get("key"))
    if plan["action_taken"] == "created":
        created = plan["plan"]
        frozen["creation"] = {"effective_at": created.effective_at,
                              "filename": Path(created.path).name, "key": created.fields.get("key")}
    return plan, frozen


def ingestion_binding(context, request, *, plan, frozen_inputs=None):
    result = {key: value for key, value in plan.items() if key != "plan"}
    observations = [ObservedResource("ingestion-choice", "selected-target", content_digest(canonical_json(result)))]
    domain = plan.get("plan")
    if plan["action_taken"] == "created":
        observations.extend((ObservedResource("destination", domain.path, None),
                             ObservedResource("content", domain.path, content_digest(domain.content)),
                             ObservedResource("definition", domain.artefact["key"], content_digest(canonical_json(domain.artefact)))))
    elif plan["action_taken"] == "updated":
        observations.extend((ObservedResource("document", domain.opened.path, domain.opened.revision),
                             ObservedResource("content", domain.opened.path, content_digest(domain.new_body)),
                             ObservedResource("definition", domain.opened.artefact["key"],
                                              content_digest(canonical_json(domain.opened.artefact)))))
    return bind_operation(request, observations=observations, frozen_inputs=frozen_inputs,
                          review={"action": result["action_taken"], "path": result.get("path"),
                                  "type": result.get("type"), "title": result.get("title"),
                                  "needs_decision": result["needs_decision"]})


def prepare_ingestion(context, request, *, frozen_inputs=None):
    from _common import vault_mutation_lock
    from ..results import Error
    from .ingest import load_ingestion_inputs
    with vault_mutation_lock(context.selected_brain.vault_root):
        inputs = load_ingestion_inputs(context, request)
        if isinstance(inputs, Error):
            raise ValueError(inputs.error.message)
        router, retrieval = inputs
        body, frozen = prepare_content(context, request.content, frozen_inputs)
        plan, frozen = plan_ingest(context, request, router, retrieval, body, frozen_inputs=frozen)
        if plan["action_taken"] == "error":
            raise ValueError(plan["message"])
        return ingestion_binding(context, request, plan=plan, frozen_inputs=frozen)


INGESTION = OperationPreparation(prepare_ingestion)
