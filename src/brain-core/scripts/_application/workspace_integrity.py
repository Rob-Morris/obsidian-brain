"""Discoverable membership and default-parent references gate lifecycle changes."""

from .preparation import ObservedResource, canonical_json, content_digest


def lifecycle_guard_observations(context, router, subjects, *, archiving=False, reassigning=False):
    """Subjects are (path, old reference, new reference, old fields, new fields)."""
    from _common import normalize_artefact_key, parse_frontmatter
    from _lifecycle.frontmatter_repairs import iter_candidate_artefact_markdown_files
    from _common._workspace import is_terminal, membership
    from _bootstrap.workspace_binding import load_workspace_manifest_state

    protected = {}
    for path, old_reference, new_reference, before, after in subjects:
        if not old_reference or not str(before.get("type", "")).startswith("living/"):
            continue
        breaks_identity = after is None or archiving or old_reference != new_reference
        changes_type = after is not None and before.get("type") != after.get("type")
        hub = before.get("type") == "living/workspace" and (breaks_identity or changes_type)
        changes_membership = False
        if reassigning and after is not None:
            try:
                changes_membership = membership(old_reference, before) != membership(new_reference, after)
            except ValueError:
                changes_membership = True
        default_parent = breaks_identity or changes_membership or (after is not None and (
            not str(after.get("type", "")).startswith("living/") or is_terminal(router, after)))
        if hub or default_parent:
            protected[old_reference] = (hub, default_parent)
    if not protected:
        return ()

    root = context.selected_brain.vault_root
    paths = set(iter_candidate_artefact_markdown_files(root))
    references = {reference: [] for reference in protected}
    for path in sorted(paths):
        try:
            (root / path).resolve().relative_to(root.resolve())
            fields, _ = parse_frontmatter((root / path).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise ValueError(f"Cannot inspect workspace lifecycle references in {path}: {exc}") from exc
        workspace = normalize_artefact_key(fields.get("workspace"))
        parent = normalize_artefact_key(fields.get("default_parent")) if fields.get("type") == "living/workspace" else None
        if workspace in protected and protected[workspace][0]:
            # The hub's explicit self-scope is its identity, not another member.
            own = "workspace/" + str(fields.get("key", "")) if fields.get("type") == "living/workspace" else None
            if own != workspace:
                references[workspace].append(f"{path}: workspace")
        if parent in protected:
            references[parent].append(f"{path}: default_parent")
    if context.workspace_dir is not None:
        state = load_workspace_manifest_state(context.workspace_dir)
        manifest = state.data or {}
        link = (manifest.get("links") or {}).get("workspace")
        parent = normalize_artefact_key((manifest.get("defaults") or {}).get("parent"))
        for reference, (hub, default_parent) in protected.items():
            if hub and link == reference.removeprefix("workspace/"):
                references[reference].append(f"{state.manifest_path}: links.workspace")
            if (hub or default_parent) and parent == reference:
                references[reference].append(f"{state.manifest_path}: defaults.parent")
    blockers = [f"{reference}: " + "; ".join(refs) for reference, refs in references.items() if refs]
    if blockers:
        raise ValueError("Workspace lifecycle change would strand discoverable references: "
                         + " | ".join(blockers)
                         + ". Replace or clear these references first; disconnected local manifests are not inspected.")
    return tuple(ObservedResource("workspace-lifecycle-references", reference,
                                 content_digest(canonical_json({"references": refs})))
                 for reference, refs in sorted(references.items()))
