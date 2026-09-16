"""Complete ownership reassignment planned before the shared transition boundary."""

from dataclasses import dataclass, replace
from pathlib import Path

from .workspace_context import EffectiveMutationContext, resolve_mutation_context, apply_semantic_tags
from .preparation import ObservedResource, content_digest, canonical_json


@dataclass(frozen=True)
class WorkspaceReassignmentPlan:
    transition: object
    mutation_context: EffectiveMutationContext


def plan_reassignment(context, request, router, *, frozen_inputs=None):
    import edit
    from _common import (resolve_type, living_artefact_index_entry, resolve_parent_reference,
                         document_revision_at, parent_chain_entries,
                         resolve_folder, apply_terminal_status_folder)
    from _common._workspace import validate_ownership
    from _lifecycle.ownership_graph import read_ownership_graph
    from .preparation_transition import transition_time
    from .workspace_integrity import lifecycle_guard_observations

    root = context.selected_brain.vault_root
    graph = read_ownership_graph(root)
    if request.path.startswith("_Archive/") and request.path in graph.by_path:
        path = request.path
    else:
        path, _absolute, _fields, _body, _definition = edit._read_open_path(str(root), router, request.path)
    if path not in graph.by_path:
        raise ValueError(f"{path} is not a living or temporal artefact")
    subjects = graph.subtree(path)
    if len(subjects) > 1 and not request.recursive:
        raise ValueError("Owned descendants require recursive=true: " + ", ".join(item.path for item in subjects[1:]))
    effective = resolve_mutation_context(context, router, request.workspace_context)
    if effective.workspace is None and (request.workspace_context is None or request.workspace_context.value != "global"):
        raise ValueError("Clearing membership requires explicit workspace_context=global")
    effective_at, frozen = transition_time(context, frozen_inputs)
    updated = {}
    root_parent = subjects[0].parent
    if request.clear_parent:
        root_parent = None
    elif request.parent is not None:
        root_parent, _entry = resolve_parent_reference(str(root), router, request.parent)
    for record in subjects:
        fields = dict(record.fields)
        if fields.get("type", "").startswith("living/") and record.reference is None:
            raise ValueError(f"Living artefact {record.path} needs a valid canonical key before reassignment")
        if fields.get("type") == "living/workspace" and effective.workspace != record.reference:
            raise ValueError(f"Workspace hub {record.reference} must remain self-scoped")
        if effective.workspace is None:
            fields.pop("workspace", None)
        else:
            fields["workspace"] = effective.workspace
        if record.path == path and (request.clear_parent or request.parent is not None):
            if root_parent is None:
                fields.pop("parent", None)
            else:
                fields["parent"] = root_parent
            edit._replace_exact_tag(fields, record.parent, root_parent)
        updated[record.path] = apply_semantic_tags(fields, effective)
    index = dict(router.get("artefact_index", {}))
    needed = {item.reference for item in subjects if item.reference}
    pending = [fields["parent"] for fields in updated.values() if fields.get("parent")]
    if root_parent:
        pending.append(root_parent)
    while pending:
        reference = pending.pop()
        if reference in needed:
            continue
        record = graph.resolve(reference)
        needed.add(reference)
        if record.parent:
            pending.append(record.parent)
    for reference in needed:
        record = graph.resolve(reference)
        fields = updated.get(record.path, record.fields)
        definition = resolve_type(router, fields["type"])
        index[reference] = living_artefact_index_entry(definition, record.path, fields)
    view = {**router, "artefact_index": index}
    sources = list(effective.sources)
    sources.append(ObservedResource("workspace-ownership-graph", "vault", content_digest(canonical_json(graph.observation()))))
    guards = []
    moves, writes = [], []
    parent_changed = root_parent != subjects[0].parent
    for record in subjects:
        fields = updated[record.path]
        reference = record.reference or record.path
        validate_ownership(reference, fields, index)
        definition = resolve_type(router, fields["type"])
        parent = fields.get("parent")
        if parent:
            owner = graph.resolve(parent)
            if owner.archived and not record.archived:
                raise ValueError(f"Active artefact {record.path} cannot have archived parent {parent}")
            parent_chain_entries(view, parent)
            sources.append(ObservedResource("workspace-parent", parent, document_revision_at(root / owner.path)))
        guards.append((record.path, reference, reference, record.fields, fields))
        destination = record.path
        if parent_changed and not record.archived:
            folder = apply_terminal_status_folder(resolve_folder(definition, parent=parent, router=view), definition, fields)
            destination = str(Path(folder) / Path(record.path).name)
        if fields != record.fields:
            fields = {**fields, "modified": effective_at}
            writes.append({"path": record.path, "fields": fields, "body": record.body})
        if destination != record.path:
            moves.append({"source": record.path, "dest": destination})
    sources.extend(lifecycle_guard_observations(context, router, guards, reassigning=True))
    effective = replace(effective, parent=root_parent,
        parent_source=("explicit" if request.parent is not None else "preserved") if root_parent else "none",
        sources=tuple({(item.kind, item.identity): item for item in sources}.values()))
    destinations = {item["source"]: item["dest"] for item in moves}
    changed_subjects = tuple(sorted({destinations.get(item["path"], item["path"]) for item in writes}
                                    | set(destinations.values())))
    base = edit.plan_artefact_transition(str(root), router, operation="set-workspace", writes=writes,
        moves=moves, observations=tuple(item.path for item in subjects), allow_archive_paths=True,
        result={"path": destinations.get(path, path), "workspace": effective.workspace,
                "subjects": tuple(destinations.get(item.path, item.path) for item in subjects),
                "changed": bool(writes or moves),
                "changed_subjects": changed_subjects,
                "moves": tuple({"old_path": item["source"], "new_path": item["dest"]} for item in moves)})
    return WorkspaceReassignmentPlan(base, effective), frozen
