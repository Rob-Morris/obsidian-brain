"""Observe newly declared workspace relevance without adopting ownership."""

from .preparation import ObservedResource


def observe_workspace_associations(context, router, tags, *, existing_tags=(), new_identity=None):
    """Validate only new authored associations; historical tags remain editable.

    Active identity is resolved from the fresh router. Only otherwise unresolved
    references need one archive enumeration, shared across this preparation.
    """
    from _common import parse_frontmatter, read_exact_file_content
    from _common._workspace import canonical_query_workspace, normalise_tags

    old = {tag.strip() for tag in existing_tags if isinstance(tag, str)} if isinstance(existing_tags, (list, tuple)) else set()
    new = set()
    for tag in normalise_tags(tags or ()):
        if not tag.startswith("workspace/") or tag in old or tag == new_identity:
            continue
        try:
            new.add(canonical_query_workspace(tag))
        except ValueError as exc:
            raise ValueError(f"Invalid workspace relevance tag {tag}: {exc}") from exc
    root = context.selected_brain.vault_root
    index = router.get("artefact_index", {})
    archived = None
    observations = []
    for reference in sorted(new):
        entry = index.get(reference)
        if entry is not None and entry.get("type") == "living/workspace":
            path = entry["path"]
        else:
            if archived is None:
                from _portable.vault_files import list_archived_artefacts
                archived = list_archived_artefacts(router, root)
            matches = [entry for entry in archived if entry.get("type") == "living/workspace"
                       and "workspace/" + str(entry.get("key")) == reference]
            if len(matches) != 1:
                raise ValueError(f"Workspace association {reference} must resolve to one existing active or archived workspace; "
                                 "use workspace.create for a missing identity or remove the new association")
            path = matches[0]["path"]
        target = root / path
        target.resolve().relative_to(root.resolve())
        content = read_exact_file_content(target, convert_lossless=True)
        fields, _body = parse_frontmatter(content)
        if fields.get("type") != "living/workspace" or "workspace/" + str(fields.get("key")) != reference:
            raise ValueError(f"Workspace association {reference} changed during preparation; refresh the router and retry")
        observations.append(ObservedResource("workspace-association", reference, content.revision))
    return tuple(observations)
