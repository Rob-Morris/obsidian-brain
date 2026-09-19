"""Installed, isolated stdio bootstrap; the selected Core owns its runtime/proxy."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys




def serve() -> int:
    """Resolve a generic route without provisioning, registration writes or stdout noise."""
    if sys.argv[1:] != ["mcp", "serve"]:
        print("brain: stdio bootstrap accepts only mcp serve", file=sys.stderr)
        return 2
    if sys.version_info < (3, 12):
        print("brain: bootstrap requires Python 3.12+; reinstall with --bootstrap-python", file=sys.stderr)
        return 4
    distribution = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(distribution / "cli"), str(distribution / "src" / "brain-core" / "scripts")]
    try:
        from _distribution import verify_distribution
        from _bootstrap.workspace_binding import resolve_brain_target
        from _bootstrap.runtime import target_managed_python

        verify_distribution(distribution)
        # Workspace context is the only persisted project routing input. A stale
        # inherited selected-vault pin must not replace workspace/default resolution.
        target = resolve_brain_target(workspace_env=os.environ.get("BRAIN_WORKSPACE_DIR"),
                                      vault_root_env=None, start_dir=Path.cwd())
        vault = Path(target.vault_root)
        environment = dict(os.environ)
        for key in tuple(environment):
            if key.startswith("PYTHON") or key in {
                "BRAIN_CLI_BUNDLE", "BRAIN_CLI_DISTRIBUTION_ROOT", "BRAIN_VAULT_ROOT",
                "BRAIN_VENV_LAUNCHER", "VIRTUAL_ENV", "BRAIN_SKIP_BOOTSTRAP",
            }:
                environment.pop(key)
        for key in ("PYTHONPATH", "PYTHONHOME", "BRAIN_VENV_LAUNCHER"):
            os.environ.pop(key, None)
        python = target_managed_python(vault, launcher=Path(sys.executable))
        environment["PYTHONPATH"] = str(vault / ".brain-core")
        environment["BRAIN_VAULT_ROOT"] = str(vault)
        if target.workspace_dir:
            environment["BRAIN_WORKSPACE_DIR"] = target.workspace_dir
        argv = [str(python), "-s", "-P", "-m", "brain_mcp.proxy", str(python), "brain_mcp.server"]
        if os.name == "nt":
            return subprocess.call(argv, env=environment)
        os.execve(str(python), argv, environment)
    except (OSError, RuntimeError, ValueError, ImportError, subprocess.SubprocessError) as exc:
        print(f"brain: MCP startup failed: {exc}", file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(serve())
