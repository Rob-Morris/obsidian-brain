"""Release gates for the breaking granular MCP cutover."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_ROOT = REPO_ROOT / "src" / "brain-core"
SCRIPTS_ROOT = CORE_ROOT / "scripts"
for root in (CORE_ROOT, SCRIPTS_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from _application.projection import project_identity  # noqa: E402
from _application.registry import current_application_catalogue  # noqa: E402
from _application.types import Projection  # noqa: E402
from brain_mcp import server  # noqa: E402


def test_released_mcp_tools_are_exactly_the_catalogue_projection():
    tools = asyncio.run(server.mcp.list_tools())
    actual = tuple(sorted(tool.name for tool in tools))
    expected = tuple(
        project_identity(entry.command_id).mcp_tool
        for entry in current_application_catalogue().entries
        if Projection.MCP in entry.eligible_projections
    )

    assert actual == expected
    assert not {
        "brain_init",
        "brain_session",
        "brain_read",
        "brain_create",
        "brain_edit",
        "brain_define",
        "brain_move",
        "brain_action",
        "brain_process",
    } & set(actual)


@pytest.mark.parametrize(
    "metadata",
    (
        None,
        SimpleNamespace(model_extra={}),
        SimpleNamespace(model_extra={"brainInvocation": {"invocationId": "cli-owned"}}),
        SimpleNamespace(model_extra={"brainInvocation": {"invocationId": "mcp-" + "x" * 129}}),
    ),
)
def test_server_accepts_only_bounded_proxy_invocation_identity(metadata):
    with pytest.raises(RuntimeError, match="proxy-owned"):
        server._invocation_id_from_metadata(metadata)

    accepted = server._invocation_id_from_metadata(
        SimpleNamespace(
            model_extra={"brainInvocation": {"invocationId": "mcp-accepted-id"}}
        )
    )
    assert accepted == "mcp-accepted-id"
    assert server._invocation_id_from_metadata(
        {"brainInvocation": {"invocationId": "mcp-sdk2-mapping"}}
    ) == "mcp-sdk2-mapping"


def test_server_version_guard_uses_proxy_restart_exit_code(tmp_path, monkeypatch):
    vault = tmp_path / "Brain"
    marker = vault / ".brain-core" / "VERSION"
    marker.parent.mkdir(parents=True)
    marker.write_text("0.55.7\n", encoding="utf-8")
    monkeypatch.setattr(server, "_selected_vault", lambda: vault)
    monkeypatch.setattr(server, "_LOADED_VERSION", "0.55.6")

    def exit_with(code):
        raise SystemExit(code)

    monkeypatch.setattr(server.os, "_exit", exit_with)

    with pytest.raises(SystemExit) as exc:
        server._check_version_drift()

    assert exc.value.code == 10


def test_server_version_guard_keeps_matching_process_alive(tmp_path, monkeypatch):
    vault = tmp_path / "Brain"
    marker = vault / ".brain-core" / "VERSION"
    marker.parent.mkdir(parents=True)
    marker.write_text("0.55.6\n", encoding="utf-8")
    monkeypatch.setattr(server, "_selected_vault", lambda: vault)
    monkeypatch.setattr(server, "_LOADED_VERSION", "0.55.6")
    exits = []
    monkeypatch.setattr(server.os, "_exit", exits.append)

    server._check_version_drift()

    assert exits == []
