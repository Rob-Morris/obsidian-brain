"""Export installed application/proxy facts for bootstrap-safe approval planning."""

from __future__ import annotations

import json
from pathlib import Path
import sys

from _bootstrap.approval_policy import CommandFact, snapshot


def build_contract() -> dict:
    """Project the canonical catalogue and proxy controls without starting MCP."""
    from _application.registry import current_application_catalogue
    from _application.projection import project_identity
    from _application.types import Projection

    core = Path(__file__).resolve().parents[1]
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    from brain_mcp import _proxy_controls

    facts = []
    for entry in current_application_catalogue().entries:
        identity = project_identity(entry.command_id)
        facts.append(CommandFact(entry.command_id, entry.command_version, "application", entry.initial_class.value,
                                 identity.mcp_tool if Projection.MCP in entry.eligible_projections else None,
                                 identity.cli_argv if Projection.CLI in entry.eligible_projections else (), entry.authority.value))
    for action, name in (("status", _proxy_controls.STATUS_TOOL), ("refresh", _proxy_controls.REFRESH_TOOL),
                         ("restart", _proxy_controls.RESTART_TOOL)):
        facts.append(CommandFact(name, 1, "proxy", action, name, ()))
    return snapshot(tuple(facts))


def build_launcher_contract() -> dict:
    root = Path(__file__).resolve().parents[3]
    if str(root / "cli") not in sys.path:
        sys.path.insert(0, str(root / "cli"))
    from launcher_catalogue import LAUNCHER_CATALOGUE
    return snapshot(tuple(CommandFact(e.command_id, e.command_version, "launcher", e.effect_class,
                                      None, e.entry_point[1:], e.authority) for e in LAUNCHER_CATALOGUE.entries))


if __name__ == "__main__":
    print(json.dumps(build_launcher_contract() if "--launcher" in sys.argv else build_contract(), indent=2))
