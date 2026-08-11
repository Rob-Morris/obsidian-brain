#!/usr/bin/env python3
"""Canonical granular Brain MCP composition root.

The public MCP surface is projected mechanically from the selected Brain's
application catalogue.  Semantic execution remains owned by the application
commands; this module supplies only trusted local invocation context, MCP
registration and the replacement-proxy protocol gate.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys

from mcp.server.fastmcp import FastMCP


SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from _application.registry import (  # noqa: E402
    current_application_catalogue,
    current_request_resolver,
)
from _command_interface.direct import compose_direct_context  # noqa: E402

from ._command_adapter import (  # noqa: E402
    application_interface_header,
    register_application_tools,
)
from ._proxy_protocol_gate import install_proxy_protocol_gate  # noqa: E402


_EXIT_VERSION_DRIFT = 10
_LOADED_VERSION = (
    (Path(__file__).resolve().parent.parent / "VERSION")
    .read_text(encoding="utf-8")
    .strip()
)


def _selected_vault() -> Path:
    raw = os.environ.get("BRAIN_VAULT_ROOT")
    if not raw:
        raise RuntimeError("BRAIN_VAULT_ROOT must select the Brain served by MCP")
    unresolved = Path(raw).expanduser()
    if not unresolved.is_absolute():
        raise RuntimeError("BRAIN_VAULT_ROOT must be absolute")
    if unresolved.is_symlink():
        raise RuntimeError("BRAIN_VAULT_ROOT cannot be a symlink")
    root = unresolved.resolve()
    marker = root / ".brain-core" / "VERSION"
    if marker.is_symlink() or not marker.is_file():
        raise RuntimeError("BRAIN_VAULT_ROOT is not an installed Brain")
    return root


def _check_version_drift() -> None:
    """Exit for proxy replacement when the installed command code changed."""

    marker = _selected_vault() / ".brain-core" / "VERSION"
    try:
        disk_version = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return
    if disk_version and disk_version != _LOADED_VERSION:
        # FastMCP/anyio can wrap SystemExit and lose its status. A direct exit
        # preserves the proxy's distinguished, replay-safe restart signal.
        os._exit(_EXIT_VERSION_DRIFT)


def _invocation_id_from_metadata(metadata: object) -> str:
    extras = getattr(metadata, "model_extra", None)
    invocation = extras.get("brainInvocation") if isinstance(extras, dict) else None
    invocation_id = (
        invocation.get("invocationId") if isinstance(invocation, dict) else None
    )
    if (
        not isinstance(invocation_id, str)
        or not invocation_id.startswith("mcp-")
        or len(invocation_id) > 128
    ):
        raise RuntimeError("granular MCP calls require a proxy-owned invocation ID")
    return invocation_id


def _proxy_invocation_id() -> str:
    """Read the invocation identity authenticated by the local proxy."""

    try:
        metadata = mcp.get_context().request_context.meta
    except (LookupError, ValueError) as exc:
        raise RuntimeError("granular MCP calls require proxy invocation metadata") from exc
    return _invocation_id_from_metadata(metadata)


def _mcp_context_factory(*, command_id, catalogue):
    workspace_value = os.environ.get("BRAIN_WORKSPACE_DIR")
    workspace = Path(workspace_value).expanduser() if workspace_value else None
    if workspace is not None and not workspace.is_absolute():
        raise RuntimeError("BRAIN_WORKSPACE_DIR must be absolute")
    return compose_direct_context(
        vault_root=_selected_vault(),
        command_id=command_id,
        catalogue=catalogue,
        operator_key=os.environ.get("BRAIN_OPERATOR_KEY"),
        workspace_dir=workspace,
        invocation_id=_proxy_invocation_id(),
    )


def _build_public_mcp() -> FastMCP:
    public = FastMCP(name="brain")
    catalogue = current_application_catalogue()
    register_application_tools(
        public,
        catalogue=catalogue,
        resolver=current_request_resolver(),
        context_factory=_mcp_context_factory,
        invocation_guard=_check_version_drift,
    )
    install_proxy_protocol_gate(public, application_interface_header(catalogue))
    return public


mcp = _build_public_mcp()


def main() -> None:
    _selected_vault()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
