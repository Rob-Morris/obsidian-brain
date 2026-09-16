"""Creation bindings reuse the domain's single naming and rendering planner."""

from datetime import datetime
from pathlib import Path
from dataclasses import replace

from ._mutation_support import frontmatter_mapping
from .preparation import (
    ObservedResource, OperationPreparation, bind_operation, canonical_json,
    content_digest, prepare_content,
)


def plan_artefact_create(context, request, router, body, *, frozen_inputs=None):
    import create
    from _common import parse_frontmatter, serialize_frontmatter
    from _common._workspace import reject_workspace_membership_changes, validate_ownership, normalise_tags
    from .workspace_context import resolve_mutation_context, apply_semantic_tags

    frozen = dict(frozen_inputs or {})
    choice = frozen.get("creation", {})
    effective_at = (datetime.fromisoformat(choice["effective_at"])
                    if choice else context.clock.now())
    overrides = frontmatter_mapping(request.frontmatter)
    reject_workspace_membership_changes(overrides)
    explicit_parent = request.parent if request.parent is not None else overrides.pop("parent", None)
    overrides.pop("parent", None)
    explicit_tags = overrides.get("tags")
    effective = resolve_mutation_context(context, router, request.workspace_context,
        parent=explicit_parent, creation=True, explicit_tags=() if explicit_tags is None else explicit_tags)
    plan = create.plan_artefact_creation(
        str(context.selected_brain.vault_root), router, request.type, request.title,
        body=body, frontmatter_overrides=overrides,
        parent=effective.parent, key=request.key, effective_at=effective_at,
        chosen_filename=choice.get("filename"), chosen_key=choice.get("key"),
    )
    if plan.fields["type"] == "living/workspace" and effective.workspace is not None:
        raise ValueError("Workspace hubs are self-scoped; use workspace.ensure-registration to create one")
    fields = apply_semantic_tags(plan.fields, effective)
    if effective.workspace is not None:
        fields["workspace"] = effective.workspace
    reference = (fields["type"].rsplit("/", 1)[-1] + "/" + fields["key"]
                 if fields.get("key") else plan.path)
    validate_ownership(reference, fields, router.get("artefact_index", {}))
    if fields.get("parent") != effective.parent:
        raise ValueError("Template parent must be supplied through the explicit creation parent")
    _, rendered_body = parse_frontmatter(plan.content)
    plan = replace(plan, fields=fields, content=serialize_frontmatter(fields, body=rendered_body))
    effective = replace(effective, tags=normalise_tags(fields.get("tags", [])))
    frozen["creation"] = {"effective_at": plan.effective_at,
                          "filename": Path(plan.path).name,
                          "key": plan.fields.get("key")}
    return plan, frozen, effective


def creation_binding(context, request, *, plan, file_index=None, frozen_inputs=None, effective=None):
    from _common import document_revision_at

    observations = [
        ObservedResource("destination", plan.path, None),
        ObservedResource("rendered-content", plan.path, content_digest(plan.content)),
        ObservedResource("definition", str(plan.artefact["key"]),
                         content_digest(canonical_json(plan.artefact))),
    ]
    if effective is not None:
        observations.extend(effective.sources)
    template = plan.artefact.get("template_file")
    if template:
        path = context.selected_brain.vault_root / template
        observations.append(ObservedResource("template", template,
                                              document_revision_at(path) if path.exists() else None))
    if plan.parent_context is not None:
        observations.append(ObservedResource("parent", plan.parent,
                                              content_digest(canonical_json(plan.parent_context))))
    if file_index is not None:
        index = dict(file_index)
        index["md_relpaths"] = sorted(index.get("md_relpaths", ()))
        observations.append(ObservedResource("link-resolution", plan.path,
                                              content_digest(canonical_json(index))))
    return bind_operation(request, observations=observations,
                          frozen_inputs=frozen_inputs,
                          review={"operation": "create", "path": plan.path,
                                  "parent": plan.parent, "type": plan.fields["type"],
                                  "content_sha256": content_digest(plan.content),
                                  **({"mutation_context": effective.review()} if effective is not None else {}),
                                  "fix_links": getattr(request, "fix_links", False)})


def prepare_artefact_create(context, request, *, frozen_inputs=None):
    from _common import vault_mutation_lock
    from _lifecycle.derived_cache_state import require_fresh_compiled_router
    import fix_links

    root = str(context.selected_brain.vault_root)
    with vault_mutation_lock(root):
        router = require_fresh_compiled_router(root)
        body, frozen = prepare_content(context, request.content, frozen_inputs)
        plan, frozen, effective = plan_artefact_create(context, request, router, body,
                                            frozen_inputs=frozen)
        index = fix_links.file_index_for_mutation(root) if request.fix_links else None
        return creation_binding(context, request, plan=plan, file_index=index,
                                frozen_inputs=frozen, effective=effective)


ARTEFACT_CREATION = OperationPreparation(prepare_artefact_create)


def named_creation_binding(context, request, *, plan, frozen_inputs=None):
    return bind_operation(request, observations=(
        ObservedResource("destination", plan.path, None),
        ObservedResource("rendered-content", plan.path, content_digest(plan.content)),
    ), frozen_inputs=frozen_inputs, review={"operation": "create", "resource": plan.resource,
                                           "path": plan.path, "content_sha256": content_digest(plan.content)})


def prepare_named_create(context, request, *, frozen_inputs=None):
    from _common import vault_mutation_lock
    from _lifecycle.derived_cache_state import require_fresh_compiled_router
    import create

    root = str(context.selected_brain.vault_root)
    with vault_mutation_lock(root):
        router = require_fresh_compiled_router(root)
        body, frozen = prepare_content(context, request.content, frozen_inputs)
        fields = getattr(request.target, "frontmatter", None)
        plan = create.plan_named_resource_creation(root, router, request.target.resource,
                                                   request.target.name, body,
                                                   frontmatter_mapping(fields) if fields else None)
        if (context.selected_brain.vault_root / plan.path).exists():
            raise ValueError("named document already exists")
        return named_creation_binding(context, request, plan=plan, frozen_inputs=frozen)


NAMED_CREATION = OperationPreparation(prepare_named_create)
