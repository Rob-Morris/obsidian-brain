#!/usr/bin/env python3
"""Reproducible stdio server used by the pinned real-client capture harness."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import sys

from mcp.server.fastmcp import FastMCP


REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (
    REPO_ROOT / "src" / "brain-core",
    REPO_ROOT / "src" / "brain-core" / "scripts",
):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from brain_mcp._command_adapter import register_application_tools
from _application.receipts import MemoryReceiptStore
from _application.registry import current_application_catalogue, current_request_resolver
from _application.types import Availability, DependencyTier, Projection, SnapshotFreshness
from _application.projection import project_identity
from _command_interface.context import compose_local_context


class _Clock:
    def now(self):
        return datetime(2026, 8, 10, 6, 0, tzinfo=timezone.utc)


def _context_factory(vault_root: Path, allowed_tools: frozenset[str]):
    counter = 0
    clock = _Clock()
    receipts = MemoryReceiptStore(clock)

    def create(**_metadata):
        nonlocal counter
        counter += 1
        return compose_local_context(
            vault_root=vault_root,
            brain_id="real-client-capture",
            profile="operator",
            allowed_tools=allowed_tools,
            dependency_tier=DependencyTier.MANAGED,
            provider_ids=(),
            capability_states=(),
            snapshot_token="capture-snapshot",
            snapshot_freshness=SnapshotFreshness.FRESH,
            snapshot_observed_at=clock.now(),
            correlation_id=f"capture-correlation-{counter}",
            invocation_id=f"capture-invocation-{counter}",
            receipt_store=receipts,
            clock=clock,
        )

    return create


def main() -> int:
    vault_root = Path(os.environ["BRAIN_CAPTURE_VAULT"]).resolve()
    catalogue = current_application_catalogue()
    allowed_tools = frozenset(
        project_identity(entry.command_id).mcp_tool
        for entry in catalogue.entries
        if Projection.MCP in entry.eligible_projections
    )
    mcp = FastMCP("brain")
    register_application_tools(
        mcp,
        catalogue=catalogue,
        resolver=current_request_resolver(),
        context_factory=_context_factory(vault_root, allowed_tools),
    )
    mcp.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
