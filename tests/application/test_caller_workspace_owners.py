"""Owner behaviour for caller-local workspace mutations."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from brain_test_support import folder_tree, register_other_brain

from _application._caller_workspace import CallerWorkspaceStatus
from _application.context import Capability
from _application.registry import current_application_catalogue, current_request_resolver
from _application.results import ErrorCode
from _application.types import (
    Authority,
    Availability,
    DependencyTier,
    EffectClass,
    Locality,
    Projection,
    RetryClass,
)
from _application.workspace.configure_bootstrap import (
    WorkspaceConfigureBootstrapRequest,
)
from _application.workspace.setup import WorkspaceSetupRequest
from _application.workspace.unregister import WorkspaceUnregisterRequest
from _application.workspace.update_metadata import (
    WorkspaceMetadataLink,
    WorkspaceUpdateMetadataRequest,
)
from command_application import application_for
import configure
import workspace_registry


class _CallerFilesystemProvider:
    provider_id = "caller_filesystem"


def _caller_application(root, workspace: Path | None, *, dry_run=False):
    return application_for(
        root,
        dependency_tier=DependencyTier.PORTABLE,
        workspace_dir=workspace,
        providers=(_CallerFilesystemProvider(),),
        capabilities=(
            Capability("caller_filesystem", Availability.AVAILABLE),
        ),
        dry_run=dry_run,
    )


@pytest.mark.parametrize(
    ("command_id", "request_type", "payload"),
    (
        (
            "workspace.configure-bootstrap",
            WorkspaceConfigureBootstrapRequest,
            {"surface": "claude"},
        ),
        ("workspace.setup", WorkspaceSetupRequest, {}),
        (
            "workspace.unregister",
            WorkspaceUnregisterRequest,
            {"key": "example"},
        ),
        (
            "workspace.update-metadata",
            WorkspaceUpdateMetadataRequest,
            {"tags": ["project/example"]},
        ),
    ),
)
def test_workspace_mutations_have_one_caller_local_contract(
    command_id,
    request_type,
    payload,
):
    request = current_request_resolver().resolve(command_id, payload)
    entry = current_application_catalogue().resolve(request)

    assert type(request) is request_type
    compound = command_id in {"workspace.setup", "workspace.unregister"}
    assert entry.dependency_tier is (DependencyTier.PORTABLE if compound else DependencyTier.BOOTSTRAP)
    assert entry.locality is (Locality.SELECTED_BRAIN_AND_CALLER_LOCAL if compound else Locality.CALLER_LOCAL)
    assert entry.required_providers == ("caller_filesystem",)
    assert entry.optional_providers == ()
    assert entry.authority is Authority.OPERATOR
    assert entry.effect_class is (EffectClass.SELECTED_BRAIN_AND_CALLER_LOCAL_MUTATION if compound else EffectClass.CALLER_LOCAL_MUTATION)
    assert entry.retry_class is RetryClass.RECEIPT_REQUIRED
    assert entry.eligible_projections == (
        Projection.CLI,
        Projection.SCRIPT,
        Projection.PYTHON,
    )
    assert entry.projections[0].projection is Projection.MCP
    assert entry.projections[0].supported is False
    assert ("folder" if compound else "caller") in entry.projections[0].reason.lower()


def test_workspace_owner_preflight_requires_available_caller_filesystem(
    command_vault_clone,
    tmp_path,
):
    workspace = (tmp_path / "workspace").resolve()
    workspace.mkdir()
    result = application_for(
        command_vault_clone.vault_root,
        dependency_tier=DependencyTier.BOOTSTRAP,
        workspace_dir=workspace,
    ).invoke(WorkspaceConfigureBootstrapRequest())

    assert result.status == "error"
    assert result.error.code is ErrorCode.CAPABILITY_UNAVAILABLE
    assert result.effects == "none"
    assert result.error.details.missing == ("provider:caller_filesystem",)


def test_workspace_dry_run_plans_without_invoking_legacy_mutator(
    command_vault_clone,
    tmp_path,
    monkeypatch,
):
    workspace = (tmp_path / "workspace").resolve()
    workspace.mkdir()
    monkeypatch.setattr(
        configure,
        "configure_workspace_bootstrap_action",
        lambda *_args, **_kwargs: pytest.fail("dry-run must not mutate"),
    )

    result = _caller_application(
        command_vault_clone.vault_root,
        workspace,
        dry_run=True,
    ).invoke(WorkspaceConfigureBootstrapRequest("all"))

    assert result.status == "ok"
    assert result.result.status is CallerWorkspaceStatus.PLANNED
    assert result.result.dry_run is True
    assert result.committed_effects == ()


def test_workspace_bootstrap_enumerates_each_changed_caller_file(
    command_vault_clone,
    tmp_path,
    monkeypatch,
):
    workspace = (tmp_path / "workspace").resolve()
    workspace.mkdir()
    monkeypatch.setattr(
        configure,
        "configure_workspace_bootstrap_action",
        lambda *_args, **_kwargs: {
            "status": "ok",
            "steps": [
                {
                    "name": "workspace_bootstrap_agents",
                    "status": "changed",
                    "message": "Created AGENTS.md.",
                },
                {
                    "name": "workspace_bootstrap_claude",
                    "status": "changed",
                    "message": "Created CLAUDE.md.",
                },
            ],
        },
    )

    result = _caller_application(command_vault_clone.vault_root, workspace).invoke(
        WorkspaceConfigureBootstrapRequest()
    )

    assert tuple(effect.subject for effect in result.committed_effects) == (
        "caller-workspace:AGENTS.md",
        "caller-workspace:CLAUDE.md",
    )


def test_workspace_setup_reports_known_partial_binding_effect(
    command_vault_clone,
    tmp_path,
    monkeypatch,
):
    workspace = (tmp_path / "workspace").resolve()
    workspace.mkdir()
    import vault_registry
    from _bootstrap import workspace_binding
    vault_registry.register(command_vault_clone.vault_root, "command-vault")
    def fail_local(*_args, **kwargs):
        raise OSError("Local manifest write failed.")
    monkeypatch.setattr(workspace_binding, "save_workspace_manifest_data", fail_local)

    result = _caller_application(command_vault_clone.vault_root, workspace).invoke(
        WorkspaceSetupRequest()
    )

    assert result.status == "partial"
    assert result.error.code is ErrorCode.CONFLICT
    assert result.error.message == "Local manifest write failed."
    assert [effect.kind for effect in result.committed_effects] == ["workspace.registered", "workspace.path-registered"]
    assert not (workspace / ".brain/local/workspace.yaml").exists()
    assert (command_vault_clone.vault_root / result.committed_effects[0].subject).exists()


def test_a_registry_repair_cannot_run_between_setups_row_and_manifest_writes(command_vault_clone, tmp_path, monkeypatch):
    """Lock order is vault, then folder: a rebind holds the vault lock through its manifest write."""
    import _common
    import vault_registry
    from _bootstrap import workspace_binding
    from _bootstrap.file_lock import MutationLockError
    from _portable.registry_maintenance import repair_registry

    root = command_vault_clone.vault_root
    vault_registry.register(root, "command-vault")
    register_other_brain(tmp_path, "other-brain")
    workspace = (tmp_path / "workspace").resolve()
    (workspace / ".brain" / "local").mkdir(parents=True)
    (workspace / ".brain" / "local" / "workspace.yaml").write_text(
        "brain: other-brain\nslug: rebound\nlinks:\n  workspace: rebound\n")
    real_save, real_lock = workspace_binding.save_workspace_manifest_data, _common.vault_mutation_lock
    between = []

    def save_after_a_repair(*args, **kwargs):
        try:
            between.append(repair_registry(root, dry_run=False))
        except MutationLockError as exc:
            between.append(exc)
        return real_save(*args, **kwargs)

    monkeypatch.setattr(_common, "vault_mutation_lock", lambda target, *, timeout=30.0, create_parent=True:
                        real_lock(target, timeout=0.2, create_parent=create_parent))
    monkeypatch.setattr(workspace_binding, "save_workspace_manifest_data", save_after_a_repair)
    result = _caller_application(root, workspace).invoke(WorkspaceSetupRequest(force=True))

    assert result.status == "ok", result
    assert len(between) == 1 and isinstance(between[0], MutationLockError), between
    assert workspace_registry.load_registry(root)["rebound"] == {"path": str(workspace)}
    assert repair_registry(root, dry_run=False).status == "noop"


@pytest.mark.parametrize("drift", ["git-init", "git-dir", "gitignore"])
def test_workspace_setup_rechecks_scaffold_under_caller_lock(command_vault_clone, tmp_path, monkeypatch, drift):
    from contextlib import contextmanager
    import shutil
    import subprocess
    import _common
    import vault_registry

    root = command_vault_clone.vault_root
    workspace = (tmp_path / "workspace").resolve()
    workspace.mkdir()
    vault_registry.register(root, "command-vault")
    if drift == "git-dir":
        subprocess.run(["git", "init", "--separate-git-dir", str(tmp_path / "original-git"), str(workspace)], check=True, capture_output=True)
    elif drift != "git-init":
        subprocess.run(["git", "init", str(workspace)], check=True, capture_output=True)
    actual_lock = _common.vault_mutation_lock
    destinations = []

    @contextmanager
    def change_before_caller_lock(target, *args, **kwargs):
        if target == workspace:
            if drift == "git-init":
                subprocess.run(["git", "init", str(workspace)], check=True, capture_output=True)
            elif drift == "git-dir":
                gitdir = tmp_path / "moved-git"
                shutil.copytree(tmp_path / "original-git", gitdir)
                (workspace / ".git").write_text(f"gitdir: {gitdir}\n")
            else:
                (workspace / ".gitignore").write_text("user-owned\n")
            destination = (tmp_path / "moved-git/info/exclude" if drift == "git-dir"
                else workspace / ".gitignore" if drift == "gitignore"
                else workspace / ".git/info/exclude")
            destinations.append((destination, destination.read_bytes()))
        with actual_lock(target, *args, **kwargs):
            yield

    monkeypatch.setattr(_common, "vault_mutation_lock", change_before_caller_lock)
    result = _caller_application(root, workspace).invoke(WorkspaceSetupRequest())
    assert result.status == "partial", result
    assert "changed after admission" in result.error.message
    assert [effect.kind for effect in result.committed_effects] == ["workspace.registered", "workspace.path-registered"]
    assert not (workspace / ".brain/local/workspace.yaml").exists()
    assert destinations
    for destination, original in destinations:
        assert destination.read_bytes() == original


@pytest.mark.parametrize("destination", [".gitignore", ".git/info/exclude"])
def test_workspace_setup_reports_admitted_scaffold_effect(command_vault_clone, tmp_path, destination):
    import subprocess
    import vault_registry

    root = command_vault_clone.vault_root
    workspace = (tmp_path / "workspace").resolve()
    subprocess.run(["git", "init", str(workspace)], check=True, capture_output=True)
    if destination == ".gitignore":
        (workspace / destination).write_text("user-owned\n")
    vault_registry.register(root, "command-vault")
    result = _caller_application(root, workspace).invoke(WorkspaceSetupRequest())
    assert result.status == "ok", result
    assert ".brain/local/" in (workspace / destination).read_text()
    assert [(effect.kind, effect.subject) for effect in result.committed_effects
            if effect.kind == "workspace.scaffolded"] == [
        ("workspace.scaffolded", "caller-workspace:" + str(workspace / destination))]


def test_workspace_metadata_decodes_typed_links_and_reports_manifest_effect(
    command_vault_clone,
    tmp_path,
    monkeypatch,
):
    workspace = (tmp_path / "workspace").resolve()
    workspace.mkdir()
    calls = []

    def update(root, **kwargs):
        kwargs.pop("before_write")()
        calls.append((root, kwargs))
        return {
            "status": "ok",
            "steps": [
                {
                    "name": "workspace_metadata",
                    "status": "changed",
                    "message": "Updated metadata.",
                }
            ],
        }

    monkeypatch.setattr(configure, "configure_workspace_metadata_action", update)
    request = current_request_resolver().resolve(
        "workspace.update-metadata",
        {
            "tags": ["project/example"],
            "links": {"repository": "https://example.test/repo"},
            "clear_links": True,
        },
    )

    result = _caller_application(command_vault_clone.vault_root, workspace).invoke(request)

    assert request.links == (
        WorkspaceMetadataLink("repository", "https://example.test/repo"),
    )
    assert calls[0][1] == {
        "workspace_dir": workspace,
        "tags": ["project/example"],
        "clear_tags": False,
        "links": ["repository=https://example.test/repo"],
        "clear_links": True,
        "parent": None,
        "clear_parent": False,
    }
    assert tuple(effect.subject for effect in result.committed_effects) == (
        "caller-workspace:.brain/local/workspace.yaml",
    )


def _linked(root: Path, tmp_path: Path, *, key="linked", brain="command-vault", manifest_key=None,
            manifest=True, folder_name="linked"):
    """A registry row for ``key`` and, optionally, the manifest at its folder."""
    import vault_registry

    vault_registry.register(root, "command-vault")
    folder = (tmp_path / folder_name).resolve()
    folder.mkdir(exist_ok=True)
    workspace_registry.register_workspace(root, key, folder)
    if manifest:
        path = folder / ".brain" / "local" / "workspace.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"brain: {brain}\nslug: {folder_name}\ndefaults:\n  tags:\n    - project/demo\n"
            f"links:\n  workspace: {manifest_key or key}\n  repository: demo\n")
    return folder


def test_workspace_unregister_removes_both_ends_of_the_link(command_vault_clone, tmp_path):
    from _bootstrap.workspace_binding import read_workspace_manifest
    from _common._workspace import resolve_workspace_binding

    root = command_vault_clone.vault_root
    folder = _linked(root, tmp_path)

    result = _caller_application(root, None).invoke(WorkspaceUnregisterRequest("linked"))

    assert result.status == "ok", result
    assert result.warnings == ()
    assert workspace_registry.load_registry(root) == {}
    manifest = read_workspace_manifest(folder)
    assert manifest == {"slug": "linked", "defaults": {"tags": ["project/demo"]}, "links": {"repository": "demo"}}
    assert resolve_workspace_binding({}, manifest)[0] == "unconfigured"
    assert [(effect.kind, effect.subject) for effect in result.committed_effects] == [
        ("workspace.unregister", "caller-workspace-registration:linked"),
        ("workspace.unbound", f"{folder}/.brain/local/workspace.yaml"),
    ]
    assert any(str(folder) in step.message for step in result.result.steps)


@pytest.mark.parametrize("case", ["unreachable", "vault-root", "other-brain", "other-key", "no-manifest",
                                  "brain-unresolved", "unreadable"])
def test_workspace_unregister_drops_only_the_row_when_the_folder_is_not_this_link(
        command_vault_clone, tmp_path, case):
    import shutil
    import vault_registry
    from _application.results import WarningCode

    root = command_vault_clone.vault_root
    if case == "vault-root":
        vault_registry.register(root, "command-vault")
        folder = root
        workspace_registry.register_workspace(root, "linked", folder)
    else:
        if case == "other-brain":
            register_other_brain(tmp_path, "other-brain")
        brain = {"other-brain": "other-brain", "brain-unresolved": "unknown-here"}.get(case, "command-vault")
        folder = _linked(root, tmp_path, brain=brain, manifest_key="another" if case == "other-key" else None,
                         manifest=case != "no-manifest")
        if case == "unreachable":
            shutil.rmtree(folder)
        if case == "unreadable":
            (folder / ".brain" / "local" / "workspace.yaml").write_text("brain: [unclosed\n")
    before = folder_tree(folder) if folder.is_dir() and folder != root else None

    result = _caller_application(root, None).invoke(WorkspaceUnregisterRequest("linked"))

    assert result.status == "ok", result
    assert workspace_registry.load_registry(root) == {}
    assert [effect.subject for effect in result.committed_effects] == ["caller-workspace-registration:linked"]
    assert [warning.code for warning in result.warnings] == [WarningCode.FOLLOW_UP_REQUIRED]
    assert str(folder) in result.warnings[0].message
    expected = {"brain-unresolved": "does not resolve on this machine", "other-brain": "names another Brain",
                "unreadable": "could not be inspected"}.get(case)
    if expected:
        assert expected in result.warnings[0].message
    if before is not None:
        assert folder_tree(folder) == before


def test_workspace_unregister_refuses_while_an_mcp_integration_is_registered(command_vault_clone, tmp_path):
    import json as json_module
    from _bootstrap import mcp_registration

    root = command_vault_clone.vault_root
    folder = _linked(root, tmp_path)
    record = mcp_registration._record_for(mcp_registration.McpClient.CLAUDE, mcp_registration.McpScope.PROJECT,
                                          folder, folder / ".mcp.json", {"command": "brain", "args": ["mcp", "serve"]})
    (root / ".brain" / "local" / "init-state.json").write_text(json_module.dumps({"version": 2, "records": [record]}))
    before = folder_tree(folder)

    result = _caller_application(root, None).invoke(WorkspaceUnregisterRequest("linked"))

    assert result.status == "error"
    assert result.effects == "none"
    assert f"Remove registered MCP integrations for {folder}" in result.error.message
    assert "linked" in workspace_registry.load_registry(root)
    assert folder_tree(folder) == before


def test_workspace_unregister_refuses_an_unknown_key(command_vault_clone):
    result = _caller_application(command_vault_clone.vault_root, None).invoke(WorkspaceUnregisterRequest("absent"))

    assert result.status == "error"
    assert result.error.code is ErrorCode.INVALID_REQUEST
    assert result.effects == "none"


def test_workspace_unregister_takes_the_two_locks_one_after_the_other(command_vault_clone, tmp_path, monkeypatch):
    from contextlib import contextmanager
    import _common

    root = command_vault_clone.vault_root
    folder = _linked(root, tmp_path)
    actual_lock = _common.vault_mutation_lock
    events = []

    @contextmanager
    def recording_lock(target, *args, **kwargs):
        events.append(("enter", Path(target)))
        with actual_lock(target, *args, **kwargs):
            yield
        events.append(("exit", Path(target)))

    monkeypatch.setattr(_common, "vault_mutation_lock", recording_lock)
    result = _caller_application(root, None).invoke(WorkspaceUnregisterRequest("linked"))

    assert result.status == "ok", result
    assert events == [("enter", root), ("exit", root), ("enter", folder), ("exit", folder)]


def test_workspace_unregister_dry_run_writes_nothing_at_either_end(command_vault_clone, tmp_path):
    root = command_vault_clone.vault_root
    folder = _linked(root, tmp_path)
    registry = (root / ".brain/local/workspaces.json").read_bytes()
    before = folder_tree(folder)

    result = _caller_application(root, None, dry_run=True).invoke(WorkspaceUnregisterRequest("linked"))

    assert result.status == "ok", result
    assert result.result.status is CallerWorkspaceStatus.PLANNED
    assert result.committed_effects == ()
    assert (root / ".brain/local/workspaces.json").read_bytes() == registry
    assert folder_tree(folder) == before


@pytest.mark.parametrize("failure", ["os-error", "binding-error", "legacy-unlink"])
def test_workspace_unregister_reports_a_failed_manifest_write_as_partial(command_vault_clone, tmp_path, monkeypatch, failure):
    from _bootstrap import workspace_binding

    root = command_vault_clone.vault_root
    folder = _linked(root, tmp_path, manifest=failure != "legacy-unlink")
    canonical = folder / ".brain" / "local" / "workspace.yaml"
    if failure == "legacy-unlink":
        legacy = folder / ".brain" / "workspace.yaml"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text("brain: command-vault\nslug: linked\nlinks:\n  workspace: linked\n")
        real_unlink = Path.unlink

        def refuse_legacy(self, *args, **kwargs):
            if self == legacy:
                raise PermissionError("legacy manifest is read-only")
            return real_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", refuse_legacy)
    else:
        def fail(*_args, **_kwargs):
            if failure == "os-error":
                raise OSError("manifest write failed")
            raise workspace_binding.WorkspaceBindingError("manifest write failed")

        monkeypatch.setattr(workspace_binding, "save_workspace_manifest_data", fail)

    result = _caller_application(root, None).invoke(WorkspaceUnregisterRequest("linked"))

    assert result.status == "partial", result
    subjects = [effect.subject for effect in result.committed_effects]
    if failure == "legacy-unlink":
        # The canonical manifest was written before the legacy one could be removed.
        assert canonical.is_file()
        assert subjects == ["caller-workspace-registration:linked", str(canonical)]
    else:
        assert subjects == ["caller-workspace-registration:linked"]
    assert str(canonical) in result.error.message
    assert result.error.message.index("remove its brain and links.workspace") < result.error.message.index("brain workspace setup")
    assert workspace_registry.load_registry(root) == {}


@pytest.mark.parametrize("dry_run", [False, True])
def test_workspace_unregister_treats_an_uninspectable_folder_as_a_refusal(command_vault_clone, tmp_path, dry_run):
    from _application.results import WarningCode
    import os
    import sys

    if sys.platform == "win32" or os.geteuid() == 0:
        pytest.skip("POSIX permission bits that bind the test user")

    root = command_vault_clone.vault_root
    locked = (tmp_path / "locked").resolve()
    locked.mkdir()
    folder = _linked(root, tmp_path, folder_name="locked/linked")
    locked.chmod(0)
    try:
        result = _caller_application(root, None, dry_run=dry_run).invoke(WorkspaceUnregisterRequest("linked"))
    finally:
        locked.chmod(0o755)

    assert result.status == "ok", result
    assert [warning.code for warning in result.warnings] == [WarningCode.FOLLOW_UP_REQUIRED]
    assert "could not be inspected" in result.warnings[0].message
    assert ("linked" in workspace_registry.load_registry(root)) is dry_run
    assert (folder / ".brain" / "local" / "workspace.yaml").read_text().startswith("brain: command-vault")


def test_workspace_unregister_never_recreates_a_folder_deleted_mid_command(command_vault_clone, tmp_path, monkeypatch):
    import shutil
    from _application.workspace import unregister

    root = command_vault_clone.vault_root
    folder = _linked(root, tmp_path)
    real_refusal = unregister._refusal

    def delete_after_the_first_check(*args):
        refusal = real_refusal(*args)
        if folder.exists():
            shutil.rmtree(folder)
        return refusal

    monkeypatch.setattr(unregister, "_refusal", delete_after_the_first_check)
    result = _caller_application(root, None).invoke(WorkspaceUnregisterRequest("linked"))

    assert result.status == "ok", result
    assert "unreachable" in result.warnings[0].message
    assert not folder.exists()


@pytest.mark.parametrize("race", ["relinked", "manifest-edited"])
def test_workspace_unregister_rechecks_under_the_folder_lock(command_vault_clone, tmp_path, monkeypatch, race):
    from contextlib import contextmanager
    import _common

    root = command_vault_clone.vault_root
    folder = _linked(root, tmp_path)
    manifest = folder / ".brain" / "local" / "workspace.yaml"
    actual_lock = _common.vault_mutation_lock

    @contextmanager
    def racing_lock(target, *args, **kwargs):
        if Path(target) == folder:
            if race == "relinked":
                workspace_registry.register_workspace(root, "linked", folder)
            else:
                manifest.write_text(manifest.read_text().replace("workspace: linked", "workspace: another"))
        with actual_lock(target, *args, **kwargs):
            yield

    monkeypatch.setattr(_common, "vault_mutation_lock", racing_lock)
    result = _caller_application(root, None).invoke(WorkspaceUnregisterRequest("linked"))

    assert result.status == "ok", result
    assert manifest.read_text().startswith("brain: command-vault")
    assert [effect.kind for effect in result.committed_effects] == ["workspace.unregister"]
    assert ("written again" if race == "relinked" else "names another workspace") in result.warnings[0].message


@pytest.mark.parametrize("drift", [False, True])
def test_workspace_unregister_prepared_operation_binds_the_linked_manifest(command_vault_clone, tmp_path, drift):
    from dataclasses import replace
    from _application.application import CommandApplication
    from _application.consent import ConsentScope
    from command_application import context_for

    root = command_vault_clone.vault_root
    folder = _linked(root, tmp_path)
    manifest = folder / ".brain" / "local" / "workspace.yaml"
    context = context_for(root, context_kind="cli-job", dependency_tier=DependencyTier.PORTABLE,
        workspace_dir=None, providers=(_CallerFilesystemProvider(),),
        capabilities=(Capability("caller_filesystem", Availability.AVAILABLE),))
    operation = context.access.prepare("workspace.unregister", {"key": "linked"})
    context.authorisation.service.request(request_id="consent-drift", scope=ConsentScope.OPERATION,
        operation_id=operation.operation_id, digest=operation.digest, review=operation.review)
    if drift:
        manifest.write_text(manifest.read_text() + "# edited after review\n")
    invocation = replace(context, invocation_id="execute-drift", operation_id=operation.operation_id)
    invocation = replace(invocation, access=context.authorisation.bind(invocation))
    request = current_request_resolver().resolve("workspace.unregister", {"key": "linked"})

    result = CommandApplication(invocation, context.authorisation.catalogue).invoke(request)

    if drift:
        assert result.status == "error", result
        assert result.effects == "none"
        assert "changed" in result.error.message.lower(), result.error.message
        assert "linked" in workspace_registry.load_registry(root)
    else:
        assert result.status == "ok", result
        assert workspace_registry.load_registry(root) == {}


def test_workspace_commands_reject_missing_context_and_ambiguous_payloads(
    command_vault_clone,
):
    application = _caller_application(command_vault_clone.vault_root, None)

    missing_context = application.invoke(WorkspaceConfigureBootstrapRequest())

    assert missing_context.status == "error"
    assert missing_context.error.code is ErrorCode.CAPABILITY_UNAVAILABLE
    assert missing_context.effects == "none"
    with pytest.raises(ValueError, match="unexpected fields"):
        current_request_resolver().resolve("workspace.configure-bootstrap", {"workspace_dir": "/tmp"})
    with pytest.raises(ValueError, match="requires at least one change"):
        current_request_resolver().resolve("workspace.update-metadata", {})
    with pytest.raises(ValueError, match="slug must match"):
        WorkspaceUnregisterRequest("Not Canonical")
    with pytest.raises(ValueError, match="slug must match"):
        WorkspaceSetupRequest(slug="Not Canonical")
    with pytest.raises(ValueError, match="link name"):
        current_request_resolver().resolve(
            "workspace.update-metadata",
            {"links": {1: "not-a-string-key"}},
        )


@pytest.mark.parametrize('command_id', ['workspace.configure-bootstrap', 'workspace.repair-registry'])
def test_workspace_preview_enters_and_spends_specific_consent_without_content_effects(
    command_vault_clone, tmp_path, command_id,
):
    from dataclasses import replace
    from _application.application import CommandApplication
    from _application.consent import ConsentScope
    from _application.receipts import OutcomeReference, ExecutionState, ReceiptState
    from command_application import context_for

    root = command_vault_clone.vault_root
    workspace = tmp_path / 'preview-workspace'
    workspace.mkdir()
    registry = root / '.brain/local/workspaces.json'
    # A row that names no usable folder: a dry run plans the rebuild a real run would make.
    malformed = '{"workspaces": {"relative": "foreign"}}\n'
    registry.write_text(malformed)
    context = context_for(root, context_kind='cli-job', dry_run=True,
        dependency_tier=DependencyTier.PORTABLE, workspace_dir=workspace,
        providers=(_CallerFilesystemProvider(),), capabilities=(Capability('caller_filesystem', Availability.AVAILABLE),))
    arguments = {'surface': 'all'} if command_id == 'workspace.configure-bootstrap' else {}
    operation = context.access.prepare(command_id, arguments)
    context.authorisation.service.request(request_id='consent-preview', scope=ConsentScope.OPERATION,
        operation_id=operation.operation_id, digest=operation.digest, review=operation.review)
    invocation = replace(context, invocation_id='execute-preview', operation_id=operation.operation_id)
    invocation = replace(invocation, access=context.authorisation.bind(invocation))
    request = current_request_resolver().resolve(command_id, arguments)
    result = CommandApplication(invocation, context.authorisation.catalogue).invoke(request)

    assert result.status == 'ok', result
    assert result.committed_effects == ()
    assert context.authorisation.service.inspect(operation.operation_id)['state'] == 'spent'
    outcome = context.receipt_reader.read(OutcomeReference('execute-preview')).outcome
    assert outcome.execution is ExecutionState.SUCCEEDED
    assert outcome.receipt.state is ReceiptState.NONE
    assert registry.read_text() == malformed
    assert not (workspace / 'AGENTS.md').exists()
    assert not (registry.parent / 'workspaces.json.bak').exists()


@pytest.mark.parametrize("path", ["invoke", "prepare"])
def test_workspace_setup_refuses_an_unregistered_brain_without_registering_it(command_vault_clone, tmp_path, path):
    import vault_registry
    from _application.consent import ConsentError
    from command_application import context_for

    root = command_vault_clone.vault_root
    workspace = (tmp_path / "workspace").resolve()
    workspace.mkdir()
    expected = f"not registered on this machine; run {vault_registry.register_guidance(root)} first"

    if path == "invoke":
        result = _caller_application(root, workspace).invoke(WorkspaceSetupRequest())
        assert result.status == "error"
        assert result.effects == "none"
        assert expected in result.error.message
    else:
        context = context_for(root, context_kind="cli-job", dependency_tier=DependencyTier.PORTABLE,
            workspace_dir=workspace, providers=(_CallerFilesystemProvider(),),
            capabilities=(Capability("caller_filesystem", Availability.AVAILABLE),))
        with pytest.raises(ConsentError) as raised:
            context.access.prepare("workspace.setup", {})
        assert raised.value.reason == "invalid_request"
        assert expected in str(raised.value)
    assert vault_registry.load_registry_entries() == {}
    assert not (workspace / ".brain").exists()


def test_workspace_unregister_consent_review_names_the_recorded_folder(command_vault_clone, tmp_path):
    from command_application import context_for

    root = command_vault_clone.vault_root
    folder = _linked(root, tmp_path)
    caller = (tmp_path / "caller").resolve()
    caller.mkdir()
    context = context_for(root, context_kind="cli-job", dependency_tier=DependencyTier.PORTABLE,
        workspace_dir=caller, providers=(_CallerFilesystemProvider(),),
        capabilities=(Capability("caller_filesystem", Availability.AVAILABLE),))

    operation = context.access.prepare("workspace.unregister", {"key": "linked"})

    assert json.loads(operation.review)["targets"] == [
        str(root / ".brain" / "local" / "workspaces.json"),
        str(folder / ".brain" / "local" / "workspace.yaml"),
        str(folder / ".brain" / "workspace.yaml"),
    ]


def test_workspace_unregister_never_resolves_a_relative_row_against_the_current_directory(
        command_vault_clone, tmp_path, monkeypatch):
    import vault_registry
    from _bootstrap.workspace_binding import read_workspace_manifest, save_workspace_manifest_data

    root = command_vault_clone.vault_root
    vault_registry.register(root, "command-vault")
    monkeypatch.chdir(tmp_path)
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    save_workspace_manifest_data(foreign, {"brain": "command-vault", "slug": "foreign", "links": {"workspace": "foreign"}})
    registry = root / ".brain/local/workspaces.json"
    registry.write_text(json.dumps({"workspaces": {"foreign": {"path": "foreign"}}}))
    before = registry.read_bytes()

    result = _caller_application(root, None).invoke(WorkspaceUnregisterRequest("foreign"))

    assert result.status == "error" and result.effects == "none"
    assert "names no usable folder" in result.error.message
    assert registry.read_bytes() == before
    assert read_workspace_manifest(foreign)["links"] == {"workspace": "foreign"}, "the cwd folder is untouched"


def test_a_registry_write_that_loses_a_race_writes_nothing_and_is_retryable(command_vault_clone, tmp_path, monkeypatch):
    """Every writer compare-and-swaps the bytes it read, so a row committed meanwhile survives (DM1)."""
    root = command_vault_clone.vault_root
    folder = _linked(root, tmp_path)
    registry = root / ".brain/local/workspaces.json"
    real = workspace_registry.read_registry_strict
    calls = []

    def then_mcp_commits(vault_root):
        rows, content = real(vault_root)
        calls.append(vault_root)
        if len(calls) == 2:  # unregister_workspace's own read: MCP commits between it and the write
            data = json.loads(registry.read_text())
            data["workspaces"]["by-mcp"] = {"path": str(tmp_path)}
            registry.write_text(json.dumps(data))
        return rows, content

    monkeypatch.setattr(workspace_registry, "read_registry_strict", then_mcp_commits)
    result = _caller_application(root, None).invoke(WorkspaceUnregisterRequest("linked"))

    assert result.status == "error" and result.effects == "none" and result.retryable
    assert "changed while this command was updating it" in result.error.message
    assert set(workspace_registry.load_registry(root)) == {"linked", "by-mcp"}
    assert folder.is_dir()
