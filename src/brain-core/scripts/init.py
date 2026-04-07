#!/usr/bin/env python3
"""
init.py — Set up Brain MCP server for Claude Code.

Handles scenarios:
  1. Current directory:  python3 /vault/.brain-core/scripts/init.py
  2. Global default:     python3 /vault/.brain-core/scripts/init.py --user
  3. Specific folder:    python3 /vault/.brain-core/scripts/init.py --project /path/to/project
  4. Local-only:         python3 /vault/.brain-core/scripts/init.py --local

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
CLAUDE_LOCAL_MD_FILE = os.path.join(".claude", "CLAUDE.local.md")
LOCAL_SETTINGS_FILE = os.path.join(".claude", "settings.local.json")
BRAIN_CORE_MARKER = os.path.join(".brain-core", "VERSION")
MCP_SERVER_REL = os.path.join(".brain-core", "mcp", "server.py")
MCP_PROXY_REL = os.path.join(".brain-core", "mcp", "proxy.py")
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
    proxy_script = str(vault_root / MCP_PROXY_REL)
    server_script = str(vault_root / MCP_SERVER_REL)
    return {
        "command": python_path,
        "args": [proxy_script, python_path, server_script],
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


# -- Inline safe-write (duplicated from _common.safe_write — init.py is self-contained) --

def _safe_write(path: Path, content: str) -> str:
    """Atomic file write: tmp → fsync → os.replace.  Returns resolved path."""
    target = os.path.realpath(str(path))
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    tmp_path = f"{target}.{os.getpid()}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, target)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return target


def _safe_write_json(path: Path, data: dict) -> str:
    """Atomic JSON write."""
    content = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    return _safe_write(path, content)


def _backup_if_exists(path: Path) -> None:
    try:
        if path.stat().st_size > 0:
            backup = path.with_suffix(path.suffix + ".bak")
            shutil.copy2(path, backup)
            info(f"Backed up {path.name} → {backup.name}")
    except OSError:
        pass


def _upsert_mcp_server(json_path: Path, server_config: dict) -> None:
    """Read-merge-write brain server config into a JSON file."""
    existing = _read_json_safe(json_path)

    if not existing and json_path.is_file():
        _backup_if_exists(json_path)
        existing = {}

    if "mcpServers" not in existing:
        existing["mcpServers"] = {}

    existing["mcpServers"][BRAIN_SERVER_NAME] = server_config
    _safe_write_json(json_path, existing)
    info(f"Wrote {BRAIN_SERVER_NAME} → {json_path}")


def write_project_mcp_json(server_config: dict, target_dir: Path) -> None:
    """Write or merge brain into .mcp.json."""
    _upsert_mcp_server(target_dir / MCP_CONFIG_FILE, server_config)


def write_local_settings_json(server_config: dict, target_dir: Path) -> None:
    """Write brain into .claude/settings.local.json (local scope)."""
    _upsert_mcp_server(target_dir / LOCAL_SETTINGS_FILE, server_config)


def write_user_claude_json(server_config: dict) -> None:
    """Write brain into ~/.claude.json (user scope)."""
    _upsert_mcp_server(Path.home() / ".claude.json", server_config)


# ---------------------------------------------------------------------------
# Scope recording
# ---------------------------------------------------------------------------

def _record_init_scope(vault_root: Path, scope: str, config_path: str):
    """Record which scope init used, for future upgrade automation."""
    local_dir = vault_root / ".brain" / "local"
    local_dir.mkdir(parents=True, exist_ok=True)
    scope_file = local_dir / "init-scope.json"
    data = {"scope": scope, "config_path": config_path}
    scope_file.write_text(json.dumps(data, indent=2) + "\n")


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
    elif scope == "local":
        write_target = target_dir or Path.cwd()
        write_local_settings_json(server_config, write_target)
        return f"{write_target / LOCAL_SETTINGS_FILE} (direct)"
    else:
        write_target = target_dir or Path.cwd()
        write_project_mcp_json(server_config, write_target)
        return f"{write_target / MCP_CONFIG_FILE} (direct)"


# ---------------------------------------------------------------------------
# CLAUDE.md bootstrap
# ---------------------------------------------------------------------------

def ensure_claude_md(target_dir: Path, local: bool = False) -> None:
    """Ensure CLAUDE.md (or .claude/CLAUDE.local.md) has the brain bootstrap line."""
    rel_path = CLAUDE_LOCAL_MD_FILE if local else CLAUDE_MD_FILE
    claude_md = target_dir / rel_path
    content = ""

    if claude_md.is_file():
        with open(claude_md, "r", encoding="utf-8") as f:
            content = f.read()
        if CLAUDE_MD_BOOTSTRAP in content:
            info(f"{rel_path} already has bootstrap line")
            return
        separator = "\n" if content.endswith("\n") else "\n\n"
        with open(claude_md, "a", encoding="utf-8") as f:
            f.write(f"{separator}{CLAUDE_MD_BOOTSTRAP}\n")
        info(f"Appended brain bootstrap to {rel_path}")
    else:
        _safe_write(claude_md, f"{CLAUDE_MD_BOOTSTRAP}\n")
        info(f"Created {rel_path} with brain bootstrap")


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
            "  cd /my/project && python3 /vault/.brain-core/scripts/init.py\n"
            "      Configure current directory to use the brain\n\n"
            "  python3 /vault/.brain-core/scripts/init.py --local\n"
            "      Same, but gitignored (uses .claude/settings.local.json)\n\n"
            "  python3 /vault/.brain-core/scripts/init.py --user\n"
            "      Register as default brain for all projects\n\n"
            "  python3 /vault/.brain-core/scripts/init.py --project /my/project\n"
            "      Configure a specific folder without cd-ing into it\n"
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
        "--local", action="store_true",
        help="Use local scope (gitignored — .claude/settings.local.json)",
    )
    parser.add_argument(
        "--project",
        help="Target folder to configure (default: current directory)",
    )
    args = parser.parse_args()

    if args.user and (args.local or args.project):
        fatal("--user cannot be combined with --local or --project")

    vault_root = find_vault_root(args.vault)
    header(f"Brain vault: {vault_root}")

    if args.user:
        target_dir = None
        scope = "user"
        scope_label = "user (all projects)"
    else:
        target_dir = Path(args.project).resolve() if args.project else Path.cwd().resolve()
        if not target_dir.is_dir():
            fatal(f"Not a directory: {target_dir}")
        scope = "local" if args.local else "project"
        scope_label = f"{scope} ({target_dir})"

    info(f"Scope: {scope_label}")

    python_path = find_python(vault_root)
    info(f"Python: {python_path}")

    server_config = build_mcp_config(python_path, vault_root)

    # Warn if brain already exists at user scope (project config takes priority)
    if scope != "user":
        data = _read_json_safe(Path.home() / ".claude.json")
        if BRAIN_SERVER_NAME in data.get("mcpServers", {}):
            info(
                f'Note: "{BRAIN_SERVER_NAME}" is already registered globally '
                f"(~/.claude.json). This project-level install will take "
                f"priority over the global one."
            )

    header("Registering MCP server")
    method = register_mcp(server_config, scope, target_dir)

    if scope == "user":
        config_path = str(Path.home() / ".claude.json")
    elif scope == "local":
        config_path = str((target_dir or Path.cwd()) / LOCAL_SETTINGS_FILE)
    else:
        config_path = str((target_dir or Path.cwd()) / MCP_CONFIG_FILE)
    _record_init_scope(vault_root, scope, config_path)

    if target_dir:
        header("CLAUDE.md")
        ensure_claude_md(target_dir, local=args.local)

    header("Done")
    info(f"Vault:    {vault_root}")
    info(f"Scope:    {scope_label}")
    info(f"Server:   {BRAIN_SERVER_NAME}")
    info(f"Method:   {method}")
    print(file=sys.stderr)
    if _has_claude_cli():
        info("Verify:   claude mcp list")
        info(f"Undo:     claude mcp remove {BRAIN_SERVER_NAME} --scope {scope}")
    else:
        info("Verify:   restart Claude Code and check /mcp")
    print(file=sys.stderr)


if __name__ == "__main__":
    main()
