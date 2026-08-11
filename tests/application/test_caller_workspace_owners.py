"""Owner behaviour for caller-local workspace mutations."""

from __future__ import annotations

from pathlib import Path

import pytest

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
from _application.workspace.bind import WorkspaceBindRequest
from _application.workspace.configure_bootstrap import (
    WorkspaceConfigureBootstrapRequest,
)
from _application.workspace.register import WorkspaceRegisterRequest
from _application.workspace.setup import WorkspaceSetupRequest
from _application.workspace.unregister import WorkspaceUnregisterRequest
from _application.workspace.update_metadata import (
    WorkspaceMetadataLink,
    WorkspaceUpdateMetadataRequest,
)
from command_application import application_for
import configure
import setup
import workspace_registry


class _CallerFilesystemProvider:
    provider_id = "caller_filesystem"


def _caller_application(root, workspace: Path | None, *, dry_run=False):
    return application_for(
        root,
        dependency_tier=DependencyTier.BOOTSTRAP,
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
        ("workspace.bind", WorkspaceBindRequest, {}),
        (
            "workspace.configure-bootstrap",
            WorkspaceConfigureBootstrapRequest,
            {"surface": "claude"},
        ),
        ("workspace.register", WorkspaceRegisterRequest, {"slug": "example"}),
        ("workspace.setup", WorkspaceSetupRequest, {}),
        (
            "workspace.unregister",
            WorkspaceUnregisterRequest,
            {"slug": "example"},
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
    assert entry.dependency_tier is DependencyTier.BOOTSTRAP
    assert entry.locality is Locality.CALLER_LOCAL
    assert entry.required_providers == ("caller_filesystem",)
    assert entry.optional_providers == ()
    assert entry.authority is Authority.OPERATOR
    assert entry.effect_class is EffectClass.CALLER_LOCAL_MUTATION
    assert entry.retry_class is RetryClass.RECEIPT_REQUIRED
    assert entry.eligible_projections == (
        Projection.CLI,
        Projection.SCRIPT,
        Projection.PYTHON,
    )
    assert entry.projections[0].projection is Projection.MCP
    assert entry.projections[0].supported is False
    assert "caller" in entry.projections[0].reason.lower()


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
    ).invoke(WorkspaceBindRequest())

    assert result.status == "error"
    assert result.error.code is ErrorCode.CAPABILITY_UNAVAILABLE
    assert result.effects == "none"
    assert result.error.details.missing == ("provider:caller_filesystem",)


def test_workspace_bind_uses_only_trusted_context_directory(
    command_vault_clone,
    tmp_path,
    monkeypatch,
):
    workspace = (tmp_path / "trusted-workspace").resolve()
    workspace.mkdir()
    calls = []

    def bind(root, **kwargs):
        calls.append((root, kwargs))
        return {
            "status": "ok",
            "steps": [
                {
                    "name": "workspace_binding",
                    "status": "changed",
                    "message": "Bound workspace.",
                }
            ],
            "notes": ["workspace brain: command-vault"],
        }

    monkeypatch.setattr(configure, "configure_workspace_binding_action", bind)

    result = _caller_application(command_vault_clone.vault_root, workspace).invoke(
        WorkspaceBindRequest("command-vault", "trusted-workspace", True)
    )

    assert result.status == "ok"
    assert result.result.status is CallerWorkspaceStatus.CHANGED
    assert result.result.workspace_name == "trusted-workspace"
    assert result.result.notes == ("workspace brain: command-vault",)
    assert calls == [
        (
            command_vault_clone.vault_root,
            {
                "workspace_dir": workspace,
                "brain_id": "command-vault",
                "slug": "trusted-workspace",
                "force": True,
            },
        )
    ]
    assert tuple(effect.subject for effect in result.committed_effects) == (
        "caller-workspace:.brain/local/workspace.yaml",
    )


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
    monkeypatch.setattr(
        setup,
        "_setup_workspace_core",
        lambda *_args, **_kwargs: {
            "status": "partial",
            "steps": [
                {
                    "name": "workspace_binding",
                    "status": "changed",
                    "message": "Bound workspace.",
                },
                {
                    "name": "workspace_local_scaffold",
                    "status": "error",
                    "message": "Git inspection failed.",
                },
            ],
        },
    )

    result = _caller_application(command_vault_clone.vault_root, workspace).invoke(
        WorkspaceSetupRequest()
    )

    assert result.status == "partial"
    assert result.error.code is ErrorCode.CONFLICT
    assert result.error.message == "Git inspection failed."
    assert tuple(effect.subject for effect in result.committed_effects) == (
        "caller-workspace:.brain/local/workspace.yaml",
    )


def test_workspace_metadata_decodes_typed_links_and_reports_manifest_effect(
    command_vault_clone,
    tmp_path,
    monkeypatch,
):
    workspace = (tmp_path / "workspace").resolve()
    workspace.mkdir()
    calls = []

    def update(root, **kwargs):
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
    }
    assert tuple(effect.subject for effect in result.committed_effects) == (
        "caller-workspace:.brain/local/workspace.yaml",
    )


def test_workspace_register_and_unregister_preserve_canonical_registry_behaviour(
    command_vault_clone,
    tmp_path,
):
    workspace = (tmp_path / "linked-workspace").resolve()
    workspace.mkdir()
    application = _caller_application(command_vault_clone.vault_root, workspace)

    registered = application.invoke(WorkspaceRegisterRequest("linked-workspace"))
    registry = workspace_registry.load_registry(command_vault_clone.vault_root)
    unregistered = application.invoke(WorkspaceUnregisterRequest("linked-workspace"))

    assert registered.status == "ok"
    assert registry == {"linked-workspace": {"path": str(workspace)}}
    assert tuple(effect.subject for effect in registered.committed_effects) == (
        "caller-workspace-registration:linked-workspace",
    )
    assert unregistered.status == "ok"
    assert workspace_registry.load_registry(command_vault_clone.vault_root) == {}


def test_workspace_commands_reject_missing_context_and_ambiguous_payloads(
    command_vault_clone,
):
    application = _caller_application(command_vault_clone.vault_root, None)

    missing_context = application.invoke(WorkspaceBindRequest())

    assert missing_context.status == "error"
    assert missing_context.error.code is ErrorCode.CAPABILITY_UNAVAILABLE
    assert missing_context.effects == "none"
    with pytest.raises(ValueError, match="unexpected fields"):
        current_request_resolver().resolve("workspace.bind", {"workspace_dir": "/tmp"})
    with pytest.raises(ValueError, match="requires at least one change"):
        current_request_resolver().resolve("workspace.update-metadata", {})
    with pytest.raises(ValueError, match="slug must match"):
        WorkspaceRegisterRequest("Not Canonical")
    with pytest.raises(ValueError, match="slug must match"):
        WorkspaceSetupRequest(slug="Not Canonical")
    with pytest.raises(ValueError, match="link name"):
        current_request_resolver().resolve(
            "workspace.update-metadata",
            {"links": {1: "not-a-string-key"}},
        )
