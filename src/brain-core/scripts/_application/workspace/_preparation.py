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
    if command in {"workspace.bind", "workspace.setup", "workspace.update-metadata"}:
        files.update((workspace / WORKSPACE_MANIFEST_REL, workspace / WORKSPACE_MANIFEST_LEGACY_REL))
    if command in {"workspace.bind", "workspace.setup"}:
        import configure
        import vault_registry
        from _bootstrap.workspace_binding import resolve_local_brain_vault

        alias = configure._resolve_binding_brain(root, request.brain_id)
        target = resolve_local_brain_vault(alias)
        files.add(Path(vault_registry.registry_path()))
        observations.append(ObservedResource("binding-brain", alias, str(target)))
    if command == "workspace.setup":
        from _bootstrap.workspace_scaffold import _git_dir, _git_repo_root

        repo = _git_repo_root(workspace)
        if repo is not None and repo == workspace.resolve():
            gitignore = workspace / ".gitignore"
            files.add(gitignore)
            gitdir = _git_dir(workspace)
            if gitdir is not None:
                observations.append(ObservedResource("git-directory", str(gitdir), None))
                if not gitignore.exists():
                    files.add(gitdir / "info/exclude")
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
