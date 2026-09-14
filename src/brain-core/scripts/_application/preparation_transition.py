"""Exact transition bindings derive from the domain's concrete write plans."""

from dataclasses import dataclass

from .preparation import ObservedResource, bind_operation, canonical_json, content_digest


def transition_binding(context, request, *, plan, router, frozen_inputs=None):
    """Bind selected source revisions, moves and matching writes, never the whole vault."""
    from edit import ArtefactTransitionPlan
    from rename import DeletePlan, MoveLinksPlan
    from _common import serialize_frontmatter, parse_frontmatter

    root = context.selected_brain.vault_root
    result, metadata, extra = {}, {}, ()
    if isinstance(plan, ArtefactTransitionPlan):
        movement = plan.movement
        result = plan.result
        if result.get("uninspected"):
            raise ValueError("Cannot prepare an exact recursive transition while archived candidates are unreadable")
        metadata = {item["path"]: serialize_frontmatter(item["fields"], body=item["body"])
                    for item in plan.writes}
        extra = plan.observations
    elif isinstance(plan, (MoveLinksPlan, DeletePlan)):
        movement = plan
    else:
        raise TypeError("transition owner did not supply a concrete domain plan")
    if movement.links.unreadable:
        raise ValueError("Cannot prepare complete backlink scope; unreadable candidates: "
                         + ", ".join(movement.links.unreadable))
    moves = getattr(movement, "moves", ())
    deleted = getattr(movement, "paths", ())
    sources = set(metadata) | set(deleted) | set(extra) | {item["source"] for item in moves}
    sources.update(item.path for item in movement.links.writes)
    observations = []
    types = set()
    for path in sorted(sources):
        source = root / path
        content = source.read_bytes()
        observations.append(ObservedResource("source", path, content_digest(content)))
        if path.endswith(".md"):
            fields, _body = parse_frontmatter(content.decode("utf-8"))
            if fields.get("type"):
                types.add(fields["type"])
        observations.append(ObservedResource("source-identity", path,
                                            source.resolve().relative_to(root.resolve()).as_posix()))
    for path in sorted({item["dest"] for item in moves} - sources):
        target = root / path
        observations.append(ObservedResource("destination", path,
                                            content_digest(target.read_bytes()) if target.exists() else None))
    writes = {**metadata, **{item.path: item.after for item in movement.links.writes}}
    for content in metadata.values():
        fields, _body = parse_frontmatter(content)
        if fields.get("type"):
            types.add(fields["type"])
    for definition in router.get("artefacts", ()):
        if definition.get("frontmatter_type", definition.get("type")) in types:
            observations.append(ObservedResource("definition", definition["key"],
                                                content_digest(canonical_json(definition))))
    manifest = {"moves": [{"source": item["source"], "dest": item["dest"]} for item in moves],
                "delete": list(deleted),
                "writes": {path: content_digest(content) for path, content in sorted(writes.items())},
                "result": result}
    observations.append(ObservedResource("effects", request.COMMAND_ID,
                                        content_digest(canonical_json(manifest))))
    return bind_operation(request, observations=observations, frozen_inputs=frozen_inputs,
                          review={"moves": manifest["moves"], "delete": list(deleted),
                                  "writes": sorted(writes)})


@dataclass(frozen=True, slots=True)
class TransitionPreparation:
    """A command-owned pure planner shared by preparation and guarded execution."""

    planner: object
    owner_guarded: bool = True

    def prepare(self, context, request, *, frozen_inputs=None):
        from _common import vault_mutation_lock
        from _lifecycle.derived_cache_state import require_fresh_compiled_router

        root = str(context.selected_brain.vault_root)
        with vault_mutation_lock(root):
            router = require_fresh_compiled_router(root)
            plan, frozen = self.planner(context, request, router, frozen_inputs=frozen_inputs)
            return transition_binding(context, request, plan=plan, router=router, frozen_inputs=frozen)


def transition_time(context, frozen_inputs):
    """Choose a lifecycle clock input once, then preserve it across consent."""
    frozen = dict(frozen_inputs or {})
    frozen.setdefault("effective_at", context.clock.now().isoformat())
    return frozen["effective_at"], frozen
