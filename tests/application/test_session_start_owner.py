"""Owner behaviour for typed managed session bootstrap."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import json

import pytest

from _application.registry import current_application_catalogue, current_request_resolver
from _application.session.start import SessionBootstrapPage, SessionStartPayload, SessionStartRequest, _payload
from _application.runtime.status import RuntimeStatusRequest
from _application._response_budget import MODEL_TEXT_BUDGET, encoded_result_size
from _application.results import Ok
from _application.types import DependencyTier, EffectClass
from _bootstrap import readiness
from _command_interface.derived_snapshots import FileDerivedSnapshotStore
from _command_interface.context import SynchronousSessionMirror
from brain_mcp._session_mirror import SessionMirrorWorker
from command_application import application_for


REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_VERSION = (REPO_ROOT / "src" / "brain-core" / "VERSION").read_text().strip()


def _mark_ready(vault_root):
    path = vault_root / ".brain/local/runtime-status.json"
    path.write_text(
        json.dumps(
            {
                "schema": "brain.runtime-status/1",
                "core_version": CORE_VERSION,
                "run_id": "test-ready",
                "state": "ready",
                "phase": None,
                "components": {
                    "router": "ready",
                    "lexical": "ready",
                    "semantic": "disabled",
                },
                "retry_after_ms": None,
                "started_at": "2026-08-12T01:00:00+00:00",
                "last_error": None,
            }
        ),
        encoding="utf-8",
    )


class _PublishedSessionMirror:
    """Observe the owner's model while preserving real mirror persistence."""

    def __init__(self, root):
        self.writer = SynchronousSessionMirror(root)
        self.model = None

    def publish(self, model):
        self.writer.publish(model)
        self.model = model


def _complete_bootstrap(application, root, mirror, record_property):
    result = application.invoke(SessionStartRequest())
    assert result.status == "ok", result
    assert mirror.model is not None
    rendered = (root / ".brain/local/session.md").read_text(encoding="utf-8")
    # Measure the exact typed envelope the owner constructs before deciding
    # between its public result variants; do not alter that admission budget.
    full = Ok(SessionStartRequest.COMMAND_ID, SessionStartRequest.COMMAND_VERSION,
              _payload(mirror.model))
    record_property("typed_bootstrap_bytes", encoded_result_size(full))
    record_property("legacy_bootstrap_budget_bytes", MODEL_TEXT_BUDGET)
    assert encoded_result_size(result) < MODEL_TEXT_BUDGET
    if isinstance(result.result, SessionStartPayload):
        assert result.result.bootstrap_complete
        return asdict(result.result), rendered

    assert isinstance(result.result, SessionBootstrapPage)
    assert encoded_result_size(full) >= MODEL_TEXT_BUDGET
    revision = result.result.revision
    chunks = []
    position = 0
    for _ in range(100):
        assert result.status == "ok", result
        assert isinstance(result.result, SessionBootstrapPage)
        assert encoded_result_size(result) < MODEL_TEXT_BUDGET
        page = result.result
        assert page.revision == revision
        assert page.range.start == position
        assert page.range.end == position + len(page.content)
        assert page.range.total_characters == len(rendered)
        chunks.append(page.content)
        position = page.range.end
        assert page.bootstrap_complete == (page.range.next_cursor is None)
        if page.range.next_cursor is None:
            break
        assert page.range.end > page.range.start
        assert page.range.next_cursor.revision == revision
        assert page.range.next_cursor.offset == position
        result = application.invoke(SessionStartRequest(cursor=page.range.next_cursor))
        assert (root / ".brain/local/session.md").read_text(encoding="utf-8") == rendered
    else:
        pytest.fail("Legacy bootstrap did not finish within the fixture page bound")
    assert position == len(rendered)
    assert "".join(chunks) == rendered
    # The paged variant publishes Markdown rather than all structured fields.
    # Assert owner facts against its real published model, not invented fields
    # on SessionBootstrapPage. The SDK test separately proves wire delivery.
    return mirror.model, rendered


def test_session_start_returns_typed_bootstrap_and_refreshes_mirror(
    command_vault_clone, record_property,
):
    root = command_vault_clone.vault_root
    _mark_ready(root)
    mirror = _PublishedSessionMirror(root)
    application = application_for(
        root, dependency_tier=DependencyTier.MANAGED, session_mirror=mirror,
    )
    bootstrap, rendered = _complete_bootstrap(application, root, mirror, record_property)

    assert bootstrap["brain_core_version"] == CORE_VERSION
    access = bootstrap["access"]
    assert access["permissions"]["ceiling"] == "reader"
    assert access["authorisation"]["initial"] == "normal"
    assert access["authorisation"]["context_available"]
    assert len(json.dumps(access, separators=(",", ":")).encode()) <= 768
    assert bootstrap["core_bootstrap"]
    assert bootstrap["core_docs"]
    assert bootstrap["artefact_type_count"] > 0
    assert bootstrap["config"]["default_profile"] == "operator"
    catalogue = bootstrap["command_catalogue"]
    assert catalogue["schema"] == "brain.command-catalogue/1"
    assert catalogue["interface_epoch"] == 3
    assert catalogue["installed_application_command_count"] == 109
    assert catalogue["list"].startswith("Use command.list")
    assert catalogue["describe"].startswith("Use command.describe")
    assert "context" not in bootstrap
    assert rendered.startswith("<!-- Generated by .brain-core/scripts/session.py.")
    assert "# Brain Session" in rendered
    assert "## Command Discovery" in rendered
    assert "command_list" in catalogue["list"]
    assert "command_describe" in catalogue["describe"]
    assert bootstrap["core_bootstrap"] in rendered
    access_section = rendered.split("## Permissions and Authorisation\n", 1)[1]
    emitted_access = json.loads(access_section.split("```json\n", 1)[1].split("\n```", 1)[0])
    assert emitted_access == json.loads(json.dumps(access))
    assert "- `default_profile`: `operator`" in rendered
    assert f"Artefact type entries: {bootstrap['artefact_type_count']}." in rendered
    authored = (REPO_ROOT / "src/brain-core/session-core.md").read_text(encoding="utf-8")
    recovery = "After a known upgrade," + authored.split("After a known upgrade,", 1)[1].split("\n\n", 1)[0]
    assert recovery in bootstrap["core_bootstrap"]
    assert recovery in rendered
    for guidance in (
        'command_list({"domain":"document"})', "mcp_tool", "cli_argv",
        "availability", "authorisation_required", "permission ceiling",
        'scope:"operation"', 'scope:"command"', "Never auto-request/retry",
    ):
        assert guidance in bootstrap["core_bootstrap"]
        assert guidance in rendered


def test_session_start_composes_cached_snapshots_with_async_mirror(
    command_vault_clone,
):
    root = command_vault_clone.vault_root
    _mark_ready(root)
    mirror = SessionMirrorWorker(root, drain_timeout=1)
    application = application_for(
        root,
        dependency_tier=DependencyTier.MANAGED,
        derived_snapshots=FileDerivedSnapshotStore(root),
        session_mirror=mirror,
    )

    result = application.invoke(SessionStartRequest())

    assert result.status == "ok", result
    assert mirror.close() is True
    assert (root / ".brain/local/session.md").is_file()


def test_session_start_carries_trusted_workspace_context(command_vault_clone, record_property):
    root = command_vault_clone.vault_root
    _mark_ready(root)
    workspace = root.parent / "agent-workspace"
    workspace.mkdir()
    mirror = _PublishedSessionMirror(root)
    application = application_for(
        root, dependency_tier=DependencyTier.MANAGED,
        workspace_dir=workspace.resolve(), session_mirror=mirror,
    )
    bootstrap, rendered = _complete_bootstrap(application, root, mirror, record_property)

    assert bootstrap["workspace"]["directory"] == str(workspace.resolve())
    assert bootstrap["workspace"]["location"] == "external"
    assert bootstrap["workspace_configuration"]["binding_status"] == "unconfigured"
    workspace_section = rendered.split("## Workspace\n", 1)[1].split("\n## ", 1)[0]
    assert f"- `directory`: `{workspace.resolve()}`" in workspace_section
    assert "- `location`: `external`" in workspace_section
    configuration_section = rendered.split("## Workspace Configuration\n", 1)[1].split("\n## ", 1)[0]
    assert "- `binding_status`: `unconfigured`" in configuration_section


def test_cold_session_start_returns_the_shared_progress_snapshot(
    command_vault_clone,
    monkeypatch,
):
    status_path = command_vault_clone.vault_root / ".brain/local/runtime-status.json"
    status_path.unlink(missing_ok=True)
    monkeypatch.setattr(readiness, "_spawn_worker", lambda *_args: None)
    application = application_for(
        command_vault_clone.vault_root,
        dependency_tier=DependencyTier.MANAGED,
    )

    started = application.invoke(SessionStartRequest())
    observed = application.invoke(RuntimeStatusRequest())

    assert started.status == "error"
    assert started.retryable is True
    assert started.error.details.runtime_status == observed.result.runtime_status
    assert started.error.details.runtime_status.schema == "brain.runtime-status/1"
    assert started.error.details.runtime_status.state == "warming"
    assert "runtime.status" in started.error.next_action.instruction
    assert "session.start" in started.error.next_action.instruction


def test_session_start_catalogue_declares_managed_cache_effect():
    entry = next(
        item
        for item in current_application_catalogue().entries
        if item.command_id == "session.start"
    )

    assert entry.dependency_tier is DependencyTier.MANAGED
    assert entry.effect_class is EffectClass.DERIVED_CACHE_WRITE
    assert entry.optional_providers == ("obsidian_cli",)


def test_session_start_transport_rejects_legacy_noop_context():
    resolver = current_request_resolver()

    assert type(resolver.resolve("session.start", {})) is SessionStartRequest
    with pytest.raises(ValueError, match="unexpected fields"):
        resolver.resolve("session.start", {"context": "ignored"})
