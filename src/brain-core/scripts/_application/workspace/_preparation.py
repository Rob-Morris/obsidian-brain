"""Bind caller-workspace operations to their resolved files and registration state."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from ..preparation import ObservedResource, bind_operation, content_digest


class PlannerRefusal(ValueError):
    """A workspace planner's known refusal on the ``access.prepare`` path, with the consent reason it reports.

    One rule: a defect in the request or in the binding it names is
    ``invalid_request``; a failure to read or use local state is ``conflict``,
    the class invoke reports for the same case (a no-effect ``CONFLICT``).
    """

    def __init__(self, message: str, *, consent_reason: str = "invalid_request"):
        super().__init__(message)
        self.consent_reason = consent_reason


@contextmanager
def planner_refusals():
    """A known refusal leaves a workspace planner as ``PlannerRefusal``, never as an unknown outcome.

    ``access.prepare`` maps a ``ValueError`` to its ``consent_reason`` with no
    effects (the planner convention in ``access_session``). A binding refusal
    (already bound without ``force``, the vault root, a malformed manifest)
    is ``invalid_request``; a binding read failure (``filesystem_access``), a
    stale or missing router, a broken git checkout, a permission failure and a
    path that cannot be read as a file are ``conflict``.
    """
    from _bootstrap.workspace_binding import WORKSPACE_ERROR_FILESYSTEM_ACCESS, WorkspaceBindingError
    from _bootstrap.workspace_scaffold import GitInspectionError
    from _lifecycle.derived_cache_state import RouterCacheUnavailable

    try:
        yield
    except WorkspaceBindingError as exc:
        reason = "conflict" if exc.code == WORKSPACE_ERROR_FILESYSTEM_ACCESS else "invalid_request"
        raise PlannerRefusal(str(exc), consent_reason=reason) from exc
    except (RouterCacheUnavailable, GitInspectionError) as exc:
        raise PlannerRefusal(str(exc), consent_reason="conflict") from exc
    except (PermissionError, IsADirectoryError, NotADirectoryError) as exc:
        raise PlannerRefusal(f"workspace preparation cannot read {exc.filename}: {exc.strerror}",
                             consent_reason="conflict") from exc


def prepare_workspace_for_consent(context, request, *, frozen_inputs=None):
    """The ``access.prepare`` planner for workspace commands: a known refusal is a no-effect consent error.

    Invoke admits through ``prepare_workspace`` and ``plan_setup`` directly, so
    its errors keep their own mapping (a stale router keeps its cache details
    and the ``runtime.refresh-router`` next action).
    """
    with planner_refusals():
        return prepare_workspace(context, request, frozen_inputs=frozen_inputs)


def _observe_file(path: Path) -> ObservedResource:
    if path.is_symlink():
        raise ValueError(f"workspace preparation refuses a symbolic-link target: {path}")
    revision = content_digest(path.read_bytes()) if path.exists() else None
    return ObservedResource("workspace-file", str(path.absolute()), revision)


def _observed_or_absent(read):
    """An unreachable or uninspectable linked folder observes as absent, never as a failure."""
    try:
        return read()
    except OSError:
        return None


def _observe_linked_folder(root: Path, key: str) -> tuple[tuple[Path, ...], tuple[ObservedResource, ...]]:
    """The recorded folder's manifests, its identity and their contents."""
    from _bootstrap.workspace_binding import legacy_manifest_path_for, manifest_path_for
    import workspace_registry
    from .unregister import linked_folder

    folder = linked_folder(root, key)
    if folder is None:
        raise ValueError(workspace_registry.missing_row_message(key))

    def identity():
        stat = folder.stat()
        return f"{stat.st_dev}:{stat.st_ino}" if folder.is_dir() else None

    manifests = (manifest_path_for(folder), legacy_manifest_path_for(folder))
    observed = tuple(
        ObservedResource("linked-manifest", str(path),
                         _observed_or_absent(lambda path=path: content_digest(path.read_bytes())))
        for path in manifests
    )
    return manifests, (ObservedResource("linked-folder", str(folder), _observed_or_absent(identity)), *observed)


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
    registry_commands = {"workspace.unregister", "workspace.repair-registry"}
    observations = []
    files = set()
    targets = []
    workspace = context.workspace_dir
    if command in registry_commands:
        files.add(Path(workspace_registry._registry_path(root)))
    else:
        if workspace is None or not workspace.is_dir():
            raise ValueError("workspace preparation requires an existing caller workspace")
        resolved = workspace.resolve(strict=True)
        stat = resolved.stat()
        observations.append(ObservedResource("caller-workspace", str(resolved), f"{stat.st_dev}:{stat.st_ino}"))
    if command == "workspace.update-metadata":
        files.update((workspace / WORKSPACE_MANIFEST_REL, workspace / WORKSPACE_MANIFEST_LEGACY_REL))
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
    if command == "workspace.unregister":
        # The folder this command edits is the row's, never the caller's.
        manifests, linked_observations = _observe_linked_folder(root, request.key)
        observations.extend(linked_observations)
        targets.extend(str(path) for path in manifests)
    if command == "workspace.configure-bootstrap":
        from _bootstrap.mcp_state import GROK_RULE_REL

        surfaces = {"agents", "claude", "grok"} if request.surface == "all" else {request.surface}
        if "agents" in surfaces:
            files.add(find_root_bootstrap_file(workspace, "AGENTS.md") or workspace / "AGENTS.md")
        if "claude" in surfaces:
            files.add(workspace / "CLAUDE.md")
        if "grok" in surfaces:
            files.add(workspace / GROK_RULE_REL)
    observations.extend(_observe_file(path) for path in sorted(files))
    return bind_operation(
        request, observations=tuple(observations),
        review={"action": command, "workspace": str(workspace) if workspace else None,
                "targets": [*(str(path) for path in sorted(files)), *targets]},
    )
