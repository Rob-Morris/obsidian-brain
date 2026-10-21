#!/usr/bin/env python3
"""Real result delivery over pipes, with a bootstrap-only managed-tier surrogate.

No runtime is installed or certified here. Context composition, permissions,
source admission, result storage and transport serialisation remain production
owners. The CLI bootstrap mode supplies that same explicit context boundary to
command.py's real parser/dispatcher/printer; later CLI calls use command.py itself.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import os
import sys
import uuid

REPO_ROOT = Path(__file__).resolve().parents[2]
for path in (REPO_ROOT / "src/brain-core", REPO_ROOT / "src/brain-core/scripts"):
    sys.path.insert(0, str(path))

from _application.registry import current_application_catalogue, current_request_resolver
from _application.types import DependencyTier
from _command_interface.direct import DirectContextComposer


def main():
    root = Path(os.environ["RESULT_DELIVERY_TEST_VAULT"]).resolve()
    composer = DirectContextComposer(vault_root=root)

    def context_factory(*, command_id, result_session=None, operation_id=None, **metadata):
        context = composer.compose(
            command_id=command_id, result_session=result_session,
            operation_id=operation_id, invocation_id="stdio-test-" + uuid.uuid4().hex,
            dry_run=metadata.get("dry_run", False),
        )
        # Deliberate test surrogate: admission still checks the real catalogue.
        # This proves delivery, not managed-runtime provisioning/readiness.
        return replace(context, dependency_tier=DependencyTier.MANAGED) if command_id == "session.start" else context

    try:
        if sys.argv[1:2] == ["--cli-bootstrap"]:
            from _command_interface.script import run
            return run(sys.argv[2:], context_factory=context_factory,
                       script_path=root / ".brain-core/scripts/command.py")
        from mcp.server import MCPServer
        from brain_mcp._command_adapter import register_application_tools
        server = MCPServer("result-delivery-stdio-test")
        register_application_tools(
            server, catalogue=current_application_catalogue(),
            resolver=current_request_resolver(), context_factory=context_factory,
            invocation_guard=lambda: None,
        )
        server.run(transport="stdio")
        return 0
    finally:
        composer.close()


if __name__ == "__main__":
    raise SystemExit(main())
