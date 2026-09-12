#!/usr/bin/env python3
"""Canonical granular Brain MCP composition root.

The public MCP surface is projected mechanically from the selected Brain's
application catalogue.  Semantic execution remains owned by the application
commands; this module supplies only trusted local invocation context, MCP
registration and the replacement-proxy protocol gate.
"""

from __future__ import annotations

from collections.abc import Mapping
import logging
import os
from pathlib import Path
import sys

from mcp.server import MCPServer
from mcp.server.mcpserver import Context


SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from _application.registry import (  # noqa: E402
    current_application_catalogue,
    current_request_resolver,
)
from _common import _operational_log  # noqa: E402
from _command_interface.direct import (  # noqa: E402
    DirectContextComposer,
)

from ._command_adapter import (  # noqa: E402
    application_interface_header,
    register_application_tools,
)
from ._proxy_protocol_gate import install_proxy_protocol_gate  # noqa: E402
from ._session_mirror import SessionMirrorWorker  # noqa: E402


_EXIT_VERSION_DRIFT = 10
_LOADED_VERSION = (
    (Path(__file__).resolve().parent.parent / "VERSION")
    .read_text(encoding="utf-8")
    .strip()
)
_MCP_CONTEXT_COMPOSER: DirectContextComposer | None = None
_SESSION_MIRROR: SessionMirrorWorker | None = None


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
        # MCPServer/anyio can wrap SystemExit and lose its status. A direct exit
        # preserves the proxy's distinguished, replay-safe restart signal.
        os._exit(_EXIT_VERSION_DRIFT)


def _invocation_id_from_metadata(metadata: object) -> str:
    extras = metadata if isinstance(metadata, Mapping) else getattr(metadata, "model_extra", None)
    invocation = (
        extras.get("brainInvocation") if isinstance(extras, Mapping) else None
    )
    invocation_id = (
        invocation.get("invocationId") if isinstance(invocation, Mapping) else None
    )
    if (
        not isinstance(invocation_id, str)
        or not invocation_id.startswith("mcp-")
        or len(invocation_id) > 128
    ):
        raise RuntimeError("granular MCP calls require a proxy-owned invocation ID")
    return invocation_id


def _proxy_invocation_id(context: Context) -> str:
    """Read the invocation identity authenticated by the local proxy."""

    try:
        metadata = context.request_context.meta
    except (LookupError, ValueError) as exc:
        raise RuntimeError("granular MCP calls require proxy invocation metadata") from exc
    return _invocation_id_from_metadata(metadata)


def _mcp_context_factory(*, command_id, catalogue, mcp_context):
    del catalogue
    composer = _mcp_context_composer()
    return composer.compose(
        command_id=command_id,
        invocation_id=_proxy_invocation_id(mcp_context),
    )


def _mcp_context_composer() -> DirectContextComposer:
    global _MCP_CONTEXT_COMPOSER, _SESSION_MIRROR
    if _MCP_CONTEXT_COMPOSER is not None:
        return _MCP_CONTEXT_COMPOSER
    root = _selected_vault()
    workspace_value = os.environ.get("BRAIN_WORKSPACE_DIR")
    workspace = Path(workspace_value).expanduser() if workspace_value else None
    if workspace is not None and not workspace.is_absolute():
        raise RuntimeError("BRAIN_WORKSPACE_DIR must be absolute")
    _SESSION_MIRROR = SessionMirrorWorker(root)
    _MCP_CONTEXT_COMPOSER = DirectContextComposer(
        vault_root=root,
        catalogue=current_application_catalogue(),
        operator_key=os.environ.get("BRAIN_OPERATOR_KEY"),
        workspace_dir=workspace,
        session_mirror=_SESSION_MIRROR,
    )
    return _MCP_CONTEXT_COMPOSER


def _build_public_mcp() -> MCPServer:
    public = MCPServer(name="brain")
    catalogue = current_application_catalogue()
    allowed_tools = None
    if os.environ.get("BRAIN_VAULT_ROOT"):
        identity = _mcp_context_composer().identity()
        allowed_tools = identity.allowed_tools
    register_application_tools(
        public,
        catalogue=catalogue,
        resolver=current_request_resolver(),
        context_factory=_mcp_context_factory,
        invocation_guard=_check_version_drift,
        allowed_tools=allowed_tools,
    )
    install_proxy_protocol_gate(
        public,
        application_interface_header(
            catalogue,
            allowed_tools=allowed_tools,
        ),
    )
    return public


mcp = _build_public_mcp()


def _install_diagnostics(root: Path) -> _operational_log.OperationalLogger | None:
    """Best-effort operational logging; a diagnostics failure never blocks serving."""
    try:
        logger = _operational_log.install(root, "server")
        logger.record("process.started")
        return logger
    except Exception:
        return None


def _close_session_mirror() -> bool:
    """Close the mirror within its one deadline and report incomplete delivery."""

    if _SESSION_MIRROR is None:
        return True
    complete = _SESSION_MIRROR.close()
    if not complete:
        logging.getLogger("brain.session-mirror").warning(
            "newest session mirror was not persisted before server shutdown"
        )
    return complete


def main() -> None:
    root = _selected_vault()
    logger = _install_diagnostics(root)
    try:
        mcp.run(transport="stdio")
    finally:
        _close_session_mirror()
        if logger is not None:
            logger.close(exit_code=0)


if __name__ == "__main__":
    main()
