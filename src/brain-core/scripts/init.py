#!/usr/bin/env python3
"""
init.py — Set up Brain MCP server for Claude Code.

Handles three scenarios:
  1. Local vault setup:  python3 .brain-core/scripts/init.py
  2. Global default:     python3 .brain-core/scripts/init.py --user
  3. Project folder:     python3 .brain-core/scripts/init.py --project /path/to/project

Can be run from terminal, Claude Code, or Cowork.

Self-contained — no imports from _common (may run before deps are installed).
Idempotent — safe to re-run. Never clobbers non-brain MCP config.

Prefers `claude mcp add-json` when available, falls back to direct config
file editing (.mcp.json for project/local, ~/.claude.json for user scope).
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BRAIN_SERVER_NAME = "brain"
MCP_CONFIG_FILE = ".mcp.json"
CLAUDE_MD_FILE = "CLAUDE.md"
BRAIN_CORE_MARKER = os.path.join(".brain-core", "VERSION")
MCP_SERVER_REL = os.path.join(".brain-core", "mcp", "server.py")
VENV_PYTHON_REL = os.path.join(".venv", "bin", "python")

CLAUDE_MD_BOOTSTRAP = (
    'If brain MCP tools are available, call brain_read(resource="router") '
    "at session start."
)


# ---------------------------------------------------------------------------
# Vault root discovery (self-contained, no _common import)
# ---------------------------------------------------------------------------

def _is_vault_root(path: Path) -> bool:
    return (path / BRAIN_CORE_MARKER).is_file()


def _find_vault_root_from_script() -> Optional[Path]:
    """Walk up from this script's location to find a vault root."""
    current = Path(__file__).resolve().parent
    for _ in range(10):
        if _is_vault_root(current):
            return current
        current = current.parent
    return None


def find_vault_root(vault_arg: Optional[str] = None) -> Path:
    """Resolve vault root from argument, env var, or script location."""
    if vault_arg:
        p = Path(vault_arg).resolve()
        if _is_vault_root(p):
            return p
        fatal(f"Not a vault root: {p}")

    env_root = os.environ.get("BRAIN_VAULT_ROOT")
    if env_root:
        p = Path(env_root).resolve()
        if _is_vault_root(p):
            return p

    root = _find_vault_root_from_script()
    if root:
        return root

    fatal(
        "Could not find vault root.\n"
        "Run from inside a vault, use --vault, or set BRAIN_VAULT_ROOT."
    )


# ---------------------------------------------------------------------------
# Python / dependency detection
# ---------------------------------------------------------------------------

def find_python(vault_root: Path) -> str:
    """Find a Python with the mcp package available."""
    # Check vault venv
    venv_python = vault_root / VENV_PYTHON_REL
    if venv_python.is_file():
        if _python_has_mcp(str(venv_python)):
            return str(venv_python)

    # Check current Python
    if _python_has_mcp(sys.executable):
        return sys.executable

    # Check common Python 3 paths
    for candidate in ["python3.12", "python3.11", "python3.10", "python3"]:
        path = shutil.which(candidate)
        if path and _python_has_mcp(path):
            return path

    fatal(
        "No Python with the 'mcp' package found.\n"
        f"Run: cd {vault_root} && make install\n"
        "Or:  pip install 'mcp>=1.0.0' --break-system-packages"
    )


def _python_has_mcp(python_path: str) -> bool:
    """Check if a Python interpreter has the mcp package."""
    try:
        result = subprocess.run(
            [python_path, "-c", "import mcp; print('ok')"],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0 and "ok" in result.stdout
    except (OSError, subprocess.TimeoutExpired):
        return False


# ---------------------------------------------------------------------------
# MCP configuration
# ---------------------------------------------------------------------------

def build_mcp_config(python_path: str, vault_root: Path) -> dict:
    """Build the MCP server JSON config for brain."""
    server_script = str(vault_root / MCP_SERVER_REL)
    return {
        "command": python_path,
        "args": [server_script],
        "env": {"BRAIN_VAULT_ROOT": str(vault_root)},
    }


def _has_claude_cli() -> bool:
    return shutil.which("claude") is not None


def _register_via_cli(
    server_config: dict, scope: str, target_dir: Optional[Path]
) -> bool:
    """Register via `claude mcp add-json`. Returns True on success."""
    config_json = json.dumps(server_config)
    cmd = [
        "claude", "mcp", "add-json", BRAIN_SERVER_NAME, config_json,
        "--scope", scope,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
            cwd=str(target_dir) if target_dir else None,
        )
        if result.returncode == 0:
            return True
        info(f"claude mcp add-json exited {result.returncode}, "
             f"falling back to direct file edit")
        if result.stderr.strip():
            info(f"  stderr: {result.stderr.strip()}")
        return False
    except (OSError, subprocess.TimeoutExpired) as e:
        info(f"claude CLI unavailable ({e}), falling back to direct file edit")
        return False


# ---------------------------------------------------------------------------
# Direct config file editing
# ---------------------------------------------------------------------------

def _read_json_safe(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _write_json(data: dict, path: Path) -> None:
    os.makedirs(path.parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _backup_if_exists(path: Path) -> None:
    try:
        if path.stat().st_size > 0:
            backup = path.with_suffix(path.suffix + ".bak")
            shutil.copy2(path, backup)
            info(f"Backed up {path.name} → {backup.name}")
    except OSError:
        pass


def write_project_mcp_json(server_config: dict, target_dir: Path) -> None:
    """Write or merge brain into .mcp.json."""
    mcp_path = target_dir / MCP_CONFIG_FILE
    existing = _read_json_safe(mcp_path)

    if not existing and mcp_path.is_file():
        _backup_if_exists(mcp_path)
        existing = {}

    if "mcpServers" not in existing:
        existing["mcpServers"] = {}

    existing["mcpServers"][BRAIN_SERVER_NAME] = server_config
    _write_json(existing, mcp_path)
    info(f"Wrote {BRAIN_SERVER_NAME} → {mcp_path}")


def write_user_claude_json(server_config: dict) -> None:
    """Write brain into ~/.claude.json (user scope)."""
    claude_json_path = Path.home() / ".claude.json"
    existing = _read_json_safe(claude_json_path)

    if not existing and claude_json_path.is_file():
        _backup_if_exists(claude_json_path)
        existing = {}
    elif existing:
        _backup_if_exists(claude_json_path)

    if "mcpServers" not in existing:
        existing["mcpServers"] = {}

    existing["mcpServers"][BRAIN_SERVER_NAME] = server_config
    _write_json(existing, claude_json_path)
    info(f"Wrote {BRAIN_SERVER_NAME} → {claude_json_path}")


# ---------------------------------------------------------------------------
# MCP registration dispatcher
# ---------------------------------------------------------------------------

def register_mcp(
    server_config: dict, scope: str, target_dir: Optional[Path]
) -> str:
    """Register brain MCP server. Returns method description."""
    if _has_claude_cli():
        info("Found `claude` CLI")
        if _register_via_cli(server_config, scope, target_dir):
            return "claude CLI"

    if scope == "user":
        write_user_claude_json(server_config)
        return "~/.claude.json (direct)"
    else:
        write_target = target_dir or Path.cwd()
        write_project_mcp_json(server_config, write_target)
        return f"{write_target / MCP_CONFIG_FILE} (direct)"


# ---------------------------------------------------------------------------
# CLAUDE.md bootstrap
# ---------------------------------------------------------------------------

def ensure_claude_md(target_dir: Path) -> None:
    """Ensure CLAUDE.md has the brain bootstrap line."""
    claude_md = target_dir / CLAUDE_MD_FILE
    content = ""

    if claude_md.is_file():
        with open(claude_md, "r", encoding="utf-8") as f:
            content = f.read()
        if CLAUDE_MD_BOOTSTRAP in content:
            info("CLAUDE.md already has bootstrap line")
            return
        separator = "\n" if content.endswith("\n") else "\n\n"
        with open(claude_md, "a", encoding="utf-8") as f:
            f.write(f"{separator}{CLAUDE_MD_BOOTSTRAP}\n")
        info("Appended brain bootstrap to CLAUDE.md")
    else:
        with open(claude_md, "w", encoding="utf-8") as f:
            f.write(f"{CLAUDE_MD_BOOTSTRAP}\n")
        info("Created CLAUDE.md with brain bootstrap")


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def info(msg: str) -> None:
    print(f"  {msg}", file=sys.stderr)


def fatal(msg: str) -> None:
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(1)


def header(msg: str) -> None:
    print(f"\n{'─' * 60}", file=sys.stderr)
    print(f"  {msg}", file=sys.stderr)
    print(f"{'─' * 60}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Set up Brain MCP server for Claude Code.",
        epilog=(
            "Examples:\n"
            "  python3 .brain-core/scripts/init.py\n"
            "      Set up this vault for Claude Code\n\n"
            "  python3 .brain-core/scripts/init.py --user\n"
            "      Register as default brain for all projects\n\n"
            "  python3 .brain-core/scripts/init.py --project .\n"
            "      Link current folder to this vault\n\n"
            "  python3 /vault/.brain-core/scripts/init.py --project /my/project\n"
            "      Link a project folder to a specific vault\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--vault",
        help="Path to Brain vault (default: auto-detect from script location)",
    )
    parser.add_argument(
        "--user", action="store_true",
        help="Register as default brain for all projects (user scope)",
    )
    parser.add_argument(
        "--project",
        help="Project folder to configure (writes .mcp.json + CLAUDE.md)",
    )
    args = parser.parse_args()

    if args.user and args.project:
        fatal("--user and --project are mutually exclusive")

    vault_root = find_vault_root(args.vault)
    header(f"Brain vault: {vault_root}")

    if args.user:
        target_dir = None
        scope = "user"
        scope_label = "user (all projects)"
    elif args.project:
        target_dir = Path(args.project).resolve()
        if not target_dir.is_dir():
            fatal(f"Not a directory: {target_dir}")
        scope = "project"
        scope_label = f"project ({target_dir})"
    else:
        target_dir = vault_root
        scope = "local"
        scope_label = f"local ({vault_root})"

    info(f"Scope: {scope_label}")

    python_path = find_python(vault_root)
    info(f"Python: {python_path}")

    server_config = build_mcp_config(python_path, vault_root)

    header("Registering MCP server")
    method = register_mcp(server_config, scope, target_dir)

    if target_dir and target_dir != vault_root:
        header("CLAUDE.md")
        ensure_claude_md(target_dir)

    header("Done")
    info(f"Vault:    {vault_root}")
    info(f"Scope:    {scope_label}")
    info(f"Server:   {BRAIN_SERVER_NAME}")
    info(f"Method:   {method}")
    print(file=sys.stderr)
    if _has_claude_cli():
        info("Verify:   claude mcp list")
    else:
        info("Verify:   restart Claude Code and check /mcp")
    print(file=sys.stderr)


if __name__ == "__main__":
    main()
