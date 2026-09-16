"""Bind caller-workspace operations to their resolved files and registration state."""

from __future__ import annotations

from pathlib import Path

from ..preparation import ObservedResource, bind_operation, content_digest


def _observe_file(path: Path) -> ObservedResource:
    if path.is_symlink():
        raise ValueError(f"workspace preparation refuses a symbolic-link target: {path}")
    revision = content_digest(path.read_bytes()) if path.exists() else None
    return ObservedResource("workspace-file", str(path.absolute()), revision)


def prepare_workspace(context, request, *, frozen_inputs=None):
    """Inspect the exact workspace identity, canonical files and indirect destinations."""
    if request.COMMAND_ID == "workspace.setup":
        from .setup import prepare_setup
        return prepare_setup(context, request, frozen_inputs=frozen_inputs)
    from _bootstrap.workspace_binding import WORKSPACE_MANIFEST_LEGACY_REL, WORKSPACE_MANIFEST_REL
    from _common import find_root_bootstrap_file
    import workspace_registry

    root = context.selected_brain.vault_root
    command = request.COMMAND_ID
    registry_commands = {"workspace.register", "workspace.unregister", "workspace.repair-registry"}
    observations = []
    files = set()
    workspace = context.workspace_dir
    if command in registry_commands:
        files.add(Path(workspace_registry._registry_path(root)))
    if command not in {"workspace.unregister", "workspace.repair-registry"}:
        if workspace is None or not workspace.is_dir():
            raise ValueError("workspace preparation requires an existing caller workspace")
        resolved = workspace.resolve(strict=True)
        stat = resolved.stat()
        observations.append(ObservedResource("caller-workspace", str(resolved), f"{stat.st_dev}:{stat.st_ino}"))
    if command in {"workspace.bind", "workspace.update-metadata"}:
        files.update((workspace / WORKSPACE_MANIFEST_REL, workspace / WORKSPACE_MANIFEST_LEGACY_REL))
    if command == "workspace.update-metadata":
        from _bootstrap.workspace_binding import read_workspace_manifest
        from _common._workspace import manifest_workspace_reference, require_workspace, workspace_policy
        from _lifecycle.derived_cache_state import require_fresh_compiled_router
        from ..preparation import canonical_json

        manifest = dict(read_workspace_manifest(workspace) or {})
        raw_defaults = manifest.get("defaults", {})
        if not isinstance(raw_defaults, dict):
            raise ValueError("Workspace defaults must be a mapping")
        defaults = dict(raw_defaults)
        if request.clear_tags:
            defaults.pop("tags", None)
        if request.tags:
            from _common._workspace import merge_metadata_tags
            defaults["tags"] = list(merge_metadata_tags(defaults.get("tags", []), request.tags))
        if request.clear_parent:
            defaults.pop("parent", None)
        elif request.parent is not None:
            defaults["parent"] = request.parent
        raw_links = manifest.get("links", {})
        if not isinstance(raw_links, dict):
            raise ValueError("Workspace links must be a mapping")
        from _common._workspace import update_metadata_links
        links = update_metadata_links(raw_links, {link.name: link.value for link in request.links}, clear=request.clear_links)
        manifest["links"] = links
        if defaults.get("parent") is not None:
            router = require_fresh_compiled_router(str(root))
            reference = manifest_workspace_reference(manifest)
            entry = require_workspace(router, reference, active=True)
            policy = workspace_policy(router, reference, defaults, local=True)
            observations.append(ObservedResource("workspace-index", reference, content_digest(canonical_json(router["artefact_index"]))))
            files.add(root / entry["path"])
            if policy.parent:
                files.add(root / router["artefact_index"][policy.parent]["path"])
    if command == "workspace.bind":
        import configure
        import vault_registry
        from _bootstrap.workspace_binding import resolve_local_brain_vault

        alias = configure._resolve_binding_brain(root, request.brain_id)
        target = resolve_local_brain_vault(alias)
        files.add(Path(vault_registry.registry_path()))
        observations.append(ObservedResource("binding-brain", alias, str(target)))
    if command == "workspace.configure-bootstrap":
        from _bootstrap.mcp_state import GROK_RULE_REL

        surfaces = {"agents", "claude", "grok"} if request.surface == "all" else {request.surface}
        if "agents" in surfaces:
            files.add(find_root_bootstrap_file(workspace, "AGENTS.md") or workspace / "AGENTS.md")
        if "claude" in surfaces:
            files.add(workspace / "CLAUDE.md")
        if "grok" in surfaces:
            files.add(workspace / GROK_RULE_REL)
    if command == "workspace.register":
        embedded = root / workspace_registry.EMBEDDED_DATA_DIR / request.slug
        observations.append(ObservedResource("embedded-workspace", str(embedded), "present" if embedded.exists() else None))
    observations.extend(_observe_file(path) for path in sorted(files))
    return bind_operation(
        request, observations=tuple(observations),
        review={"action": command, "workspace": str(workspace) if workspace else None,
                "targets": [str(path) for path in sorted(files)],
                "force": getattr(request, "force", False)},
    )
