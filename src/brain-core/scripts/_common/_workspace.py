"""Workspace identity, membership and policy rules shared by domain owners."""

from _workspace_contract import WorkspacePolicy

from ._slugs import validate_key


def reject_workspace_membership_changes(changes):
    """Membership is written only by creation context or explicit reassignment."""
    if changes and "workspace" in changes:
        raise ValueError("workspace is handler-owned; use workspace_context for creation or explicit workspace reassignment")


def reject_shared_policy_changes(type_name, changes):
    """Keep workspace policy writes behind their validation and index owner."""
    if type_name == "living/workspace" and changes:
        for field in ("default_parent", "default_tags"):
            if field in changes:
                raise ValueError(f"{field} is handler-owned; use workspace.update-policy")


def update_metadata_links(existing, updates, *, clear=False):
    """Edit descriptive links while preserving the setup-owned workspace binding."""
    if not isinstance(existing, dict):
        raise ValueError("Workspace links must be a mapping")
    if any(name.strip() == "workspace" for name in updates):
        raise ValueError("links.workspace is handler-owned; use brain workspace setup")
    links = dict(existing)
    if clear:
        links = {"workspace": existing["workspace"]} if "workspace" in existing else {}
    links.update(updates)
    return links


def workspace_reference(value, *, bare=False):
    """Validate a workspace reference; bare keys are accepted only at manifest edges."""
    from ._artefacts import normalize_artefact_key
    if bare:
        return "workspace/" + validate_key(value)
    reference = normalize_artefact_key(value)
    if reference is None or not reference.startswith("workspace/"):
        raise ValueError("workspace must reference workspace/{key}")
    return reference


def normalise_tags(value):
    """Validate a policy tag list and preserve its first-occurrence order."""
    if not isinstance(value, (list, tuple)) or any(
        not isinstance(tag, str) or not tag.strip() for tag in value
    ):
        raise ValueError("default tags must be a list of non-empty strings")
    return tuple(dict.fromkeys(tag.strip() for tag in value))


def merge_metadata_tags(existing, incoming):
    """Validate both touched tag lists before merging their normalized values."""
    return tuple(dict.fromkeys(normalise_tags(existing) + normalise_tags(incoming)))


def indexed_workspace_fields(fields):
    """Project index inputs without concealing malformed values from validation."""
    from ._artefacts import normalize_artefact_key
    workspace = fields.get("workspace")
    status = fields.get("status")
    result = {
        "workspace": normalize_artefact_key(workspace) or workspace,
        "status": status.strip() if isinstance(status, str) else status,
    }
    if fields.get("type") == "living/workspace":
        parent = fields.get("default_parent")
        tags = fields.get("default_tags", [])
        try:
            tags = list(normalise_tags(tags))
        except ValueError:
            pass
        result.update(default_parent=normalize_artefact_key(parent) or parent,
                      default_tags=tags)
    return result


def membership(reference, entry):
    """Workspace hubs own their identity even without a stored membership field."""
    explicit = entry.get("workspace")
    if entry.get("type") == "living/workspace":
        own = workspace_reference(reference)
        if explicit is not None and workspace_reference(explicit) != own:
            raise ValueError(f"Workspace hub {own} must be self-scoped")
        return own
    return workspace_reference(explicit) if explicit is not None else None


def validate_ownership(reference, entry, index):
    """Require each ownership edge to be wholly global or in one workspace."""
    from ._artefacts import normalize_artefact_key
    own_workspace = membership(reference, entry)
    parent = entry.get("parent")
    if not parent:
        return
    parent = normalize_artefact_key(parent)
    if parent not in index:
        raise ValueError(f"Missing living parent for {reference}: {entry.get('parent')}")
    if own_workspace != membership(parent, index[parent]):
        raise ValueError(f"Parent {parent} and child {reference} must share one workspace")


def is_terminal(router, entry):
    """Use the installed type's lifecycle contract for mutation eligibility."""
    from ._artefacts import resolve_artefact_definition_for_prefix
    definition = resolve_artefact_definition_for_prefix(router, entry["type"].rsplit("/", 1)[-1])
    statuses = ((definition or {}).get("frontmatter") or {}).get("terminal_statuses") or ()
    return entry.get("status") in statuses


def require_workspace(router, reference, *, active=False):
    """Resolve an exact durable identity, optionally requiring mutation eligibility."""
    reference = workspace_reference(reference)
    entry = (router.get("artefact_index") or {}).get(reference)
    if not entry or entry.get("type") != "living/workspace":
        raise ValueError(f"{reference} does not resolve to a living workspace; run brain workspace setup to repair the binding")
    membership(reference, entry)
    validate_ownership(reference, entry, router.get("artefact_index", {}))
    if active and is_terminal(router, entry):
        raise ValueError(f"{reference} is terminal/inactive; reactivate it before scoped mutation")
    return entry


def validate_default_parent(router, reference, parent):
    """Resolve a non-terminal living default parent in the selected workspace."""
    from ._artefacts import normalize_artefact_key
    canonical = normalize_artefact_key(parent)
    entry = (router.get("artefact_index") or {}).get(canonical)
    if not canonical or not entry or not str(entry.get("type", "")).startswith("living/"):
        raise ValueError(f"Default parent {parent!r} must resolve to a living artefact")
    if is_terminal(router, entry):
        raise ValueError(f"Default parent {canonical} is terminal/inactive")
    if membership(canonical, entry) != reference:
        raise ValueError(f"Default parent {canonical} must belong to {reference}")
    return canonical


def workspace_policy(router, reference, fields, *, local=False):
    """Validate shared policy or the local defaults mapping against one identity."""
    if not isinstance(fields, dict):
        raise ValueError("Workspace policy must be a mapping")
    parent_key, tags_key = ("parent", "tags") if local else ("default_parent", "default_tags")
    parent = fields.get(parent_key)
    if parent is not None:
        parent = validate_default_parent(router, reference, parent)
    return WorkspacePolicy(parent, normalise_tags(fields.get(tags_key, [])))


def manifest_workspace_reference(manifest):
    """Read only the explicit bare manifest link; never infer from paths or tags."""
    links = manifest.get("links", {})
    if not isinstance(links, dict):
        raise ValueError("Workspace links must be a mapping")
    if "workspace" not in links:
        raise ValueError("Workspace links.workspace is missing; run brain workspace setup")
    return workspace_reference(links["workspace"], bare=True)


def resolve_workspace_binding(router, manifest, *, brain_binding_error=None):
    """Classify local intent without promoting registry or path evidence to identity."""
    if manifest is None:
        return "unconfigured", None, None
    reference = None
    try:
        reference = manifest_workspace_reference(manifest)
        if any(not isinstance(manifest.get(field), str) or not manifest[field].strip()
               for field in ("brain", "slug")):
            raise ValueError("Workspace binding requires brain and slug; run brain workspace setup")
        if brain_binding_error:
            raise ValueError(brain_binding_error)
        entry = require_workspace(router, reference)
        workspace_policy(router, reference, entry)
        workspace_policy(router, reference, manifest.get("defaults", {}), local=True)
        if is_terminal(router, entry):
            return "terminal_inactive", reference, f"Reactivate {reference} before scoped mutation."
        return "valid", reference, None
    except ValueError as exc:
        return "configured_invalid", reference, str(exc)
