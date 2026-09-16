"""Workspace policy composes with concrete transition plans before admission."""

from dataclasses import replace

from .preparation import ObservedResource
from .semantic_mutations import applies_policy_tags
from .workspace_context import resolve_mutation_context, apply_semantic_tags
from .workspace_integrity import lifecycle_guard_observations


def transition_effect_snapshot(context, plan):
    """Observe concrete targets so a partial never invents an uncommitted subject."""
    from .preparation import content_digest
    from rename import DeletePlan
    movement = plan if isinstance(plan, DeletePlan) else plan.movement
    paths = set(getattr(movement, "paths", ()))
    paths.update(item.path for item in movement.links.writes)
    paths.update(item["path"] for item in getattr(plan, "writes", ()))
    for move in getattr(movement, "moves", ()):
        paths.update((move["source"], move["dest"]))
    return {path: content_digest((context.selected_brain.vault_root / path).read_bytes())
            if (context.selected_brain.vault_root / path).is_file() else None for path in paths}


def committed_transition_effects(context, request, plan, before):
    from .receipts import CommittedEffect
    after = transition_effect_snapshot(context, plan)
    changed = {path for path, revision in after.items() if revision != before[path]}
    movement = getattr(plan, "movement", plan)
    for move in getattr(movement, "moves", ()):
        if move["source"] in changed and move["dest"] in changed and after[move["source"]] is None:
            changed.discard(move["source"])
    return tuple(CommittedEffect(request.COMMAND_ID, path) for path in sorted(changed))


def _reference(router, path, fields):
    from _common import canonical_living_artefact_key, resolve_type
    try:
        definition = resolve_type(router, fields.get("type"))
    except ValueError:
        return path
    return canonical_living_artefact_key(definition, fields) or path


def semantic_transition_paths(request, plan, router):
    from rename import DeletePlan
    from _common import descendant_entries

    if isinstance(plan, DeletePlan):
        return plan.paths
    if request.COMMAND_ID == "artefact.rename":
        return (plan.movement.moves[0]["source"],)
    if request.COMMAND_ID in {"artefact.archive", "artefact.unarchive"}:
        return tuple(item["path"] for item in plan.writes)
    if request.COMMAND_ID == "artefact.reparent-children":
        return tuple(item["old_path"] for item in plan.result["children"])
    paths = [plan.writes[0]["path"]]
    if request.COMMAND_ID == "artefact.convert" and request.recursive:
        reference = next((key for key, value in router.get("artefact_index", {}).items()
                          if value["path"] == paths[0]), None)
        if reference:
            paths.extend(item["path"] for item in descendant_entries(router, reference))
    return tuple(dict.fromkeys(paths))


def prepare_workspace_transition(context, request, router, plan):
    from .workspace_reassignment import WorkspaceReassignmentPlan
    if isinstance(plan, WorkspaceReassignmentPlan):
        return plan.transition, plan.mutation_context
    from _common import parse_frontmatter, serialize_frontmatter, document_revision_at
    from _common._workspace import membership, require_workspace, validate_ownership
    from rename import DeletePlan

    root = context.selected_brain.vault_root
    paths = semantic_transition_paths(request, plan, router)
    deleting = isinstance(plan, DeletePlan)
    writes = {} if deleting else {item["path"]: dict(item) for item in plan.writes}
    planned_metadata_paths = set(writes)
    subjects = []
    for path in paths:
        before, body = parse_frontmatter((root / path).read_text(encoding="utf-8"))
        after = None if deleting else dict(writes.get(path, {"fields": before})["fields"])
        if after is not None and before.get("type") == after.get("type") == "living/workspace" and before.get("key") != after.get("key"):
            if "workspace" in after:
                after["workspace"] = "workspace/" + after["key"]
        subjects.append((path, _reference(router, path, before),
                         _reference(router, path, after) if after is not None else None, before, after))
        if not deleting and path not in writes:
            writes[path] = {"path": path, "fields": after, "body": body}
    parent = subjects[0][4].get("parent") if subjects and subjects[0][4] is not None else None
    effective = resolve_mutation_context(context, router, request.workspace_context,
        parent=parent if parent in router.get("artefact_index", {}) else None)
    changes_parent = request.COMMAND_ID in {"artefact.reparent", "artefact.reparent-children"} or (
        request.COMMAND_ID == "artefact.convert" and request.parent is not None
    )
    effective = replace(effective, parent=parent,
        parent_source=("explicit" if changes_parent else "preserved") if parent else "none")
    guard_sources = lifecycle_guard_observations(context, router, subjects,
                                                archiving=request.COMMAND_ID == "artefact.archive")
    # Pending ownership is used only for this validated write set, never published as a second index.
    index = dict(router.get("artefact_index", {}))
    for path, old, new, before, after in subjects:
        if after is not None and str(after.get("type", "")).startswith("living/"):
            if old != new:
                index.pop(old, None)
            index[new] = {**after, "path": path}
    view = {**router, "artefact_index": index}
    parent_sources = {}
    for path, old, new, before, after in subjects:
        if after is None:
            continue
        old_workspace = membership(old, before)
        new_workspace = membership(new, after)
        hub_key_change = before.get("type") == after.get("type") == "living/workspace" and old != new
        if old_workspace != new_workspace and not hub_key_change:
            raise ValueError("Semantic artefact transitions must preserve workspace membership; use explicit reassignment")
        if new_workspace:
            require_workspace(view, new_workspace)
        legacy_restore = request.COMMAND_ID == "artefact.unarchive" and new_workspace is None and after.get("parent") not in index
        if not legacy_restore:
            validate_ownership(new, after, index)
        if request.COMMAND_ID not in {"artefact.convert", "artefact.reparent", "artefact.reparent-children"}:
            if after.get("parent") != before.get("parent"):
                raise ValueError("This semantic transition must preserve the subject's parent")
        parent = after.get("parent")
        if parent in index and (root / index[parent]["path"]).exists():
            parent_sources[parent] = ObservedResource("workspace-parent", parent,
                document_revision_at(root / index[parent]["path"]))
        updated = apply_semantic_tags(after, effective) if applies_policy_tags(request.COMMAND_ID) else after
        if path in planned_metadata_paths or updated != after:
            writes[path]["fields"] = updated
        else:
            writes.pop(path)
    sources = {(item.kind, item.identity): item for item in (*effective.sources, *guard_sources, *parent_sources.values())}
    effective = replace(effective, sources=tuple(sources.values()))
    if deleting:
        return plan, effective
    semantic = set(paths)
    link_writes = []
    for item in plan.movement.links.writes:
        if item.path in semantic:
            fields, body = parse_frontmatter(item.after)
            updated = apply_semantic_tags(fields, effective)
            if updated != fields:
                item = replace(item, after=serialize_frontmatter(updated, body=body))
        link_writes.append(item)
    movement = replace(plan.movement, links=replace(plan.movement.links, writes=tuple(link_writes)))
    return replace(plan, writes=tuple(writes.values()), movement=movement), effective
