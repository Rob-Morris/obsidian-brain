#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def configured_python(vault: Path) -> Path:
    config = vault / ".mcp.json"
    payload = json.loads(config.read_text(encoding="utf-8"))
    servers = payload.get("mcpServers")
    server = servers.get("brain") if isinstance(servers, dict) else None
    command = server.get("command") if isinstance(server, dict) else None
    if not isinstance(command, str):
        raise RuntimeError(f"Brain MCP Python is missing from {config}")
    python = Path(command)
    allowed = Path("/home/brain/.brain/venvs")
    if not python.is_file() or not python.absolute().is_relative_to(allowed):
        raise RuntimeError(f"Brain MCP Python is outside the managed runtime root: {python}")
    return python


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault", required=True, type=Path)
    parser.add_argument("--requirement", required=True)
    args = parser.parse_args()
    if not args.requirement.startswith("mcp=="):
        raise RuntimeError("only an exact mcp==VERSION compatibility pin is accepted")
    python = configured_python(args.vault.resolve())
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            args.requirement,
        ],
        check=True,
    )
    print(json.dumps({"python": str(python), "requirement": args.requirement}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
