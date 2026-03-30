"""
IPC socket client for the native Obsidian CLI (Obsidian 1.12+).

Stdlib only — no external dependencies. Communicates with a running Obsidian
instance via its IPC socket (~/.obsidian-cli.sock on Unix). All functions
catch socket/parse errors and return None/False so the MCP server never
crashes due to CLI unavailability.
"""

import getpass
import json
import os
import platform
import socket


# ---------------------------------------------------------------------------
# Socket path discovery
# ---------------------------------------------------------------------------

def _get_socket_path() -> str:
    """Return the Obsidian CLI IPC socket path for the current platform."""
    if platform.system() == "Windows":
        return rf"\\.\pipe\obsidian-cli-{getpass.getuser()}"
    return os.path.expanduser("~/.obsidian-cli.sock")


def _socket_exists() -> bool:
    """Check if the IPC socket file exists (instant, no connection attempt)."""
    if platform.system() == "Windows":
        return True  # Named pipes aren't stat-able; must try connect
    return os.path.exists(_get_socket_path())


# ---------------------------------------------------------------------------
# Core IPC runner
# ---------------------------------------------------------------------------

def _send(argv: list[str], timeout: float = 5.0) -> str | None:
    """Send a command to Obsidian via IPC socket, return response or None."""
    sock_path = _get_socket_path()
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(sock_path)
        try:
            payload = json.dumps({"argv": argv, "tty": False, "cwd": "/tmp"}) + "\n"
            sock.sendall(payload.encode("utf-8"))

            chunks = []
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)

            return b"".join(chunks).decode("utf-8").strip()
        finally:
            sock.close()
    except (OSError, UnicodeDecodeError):
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_available() -> bool:
    """Check if Obsidian is running and the CLI socket is reachable."""
    if not _socket_exists():
        return False
    out = _send(["version"])
    return bool(out)


def search(vault_name: str, query: str) -> list[str] | None:
    """Search via Obsidian's live index. Returns list of file paths or None."""
    argv = ["search", f"query={query}", "format=json"]
    if vault_name:
        argv.append(f"vault={vault_name}")
    out = _send(argv)
    if out is None:
        return None
    try:
        data = json.loads(out)
        return data if isinstance(data, list) else None
    except (json.JSONDecodeError, TypeError):
        return None


def move(vault_name: str, source: str, dest: str) -> bool | None:
    """Move/rename a file via Obsidian CLI (wikilink-safe). Returns True on success, None on failure."""
    argv = ["move", f"path={source}", f"to={dest}"]
    if vault_name:
        argv.append(f"vault={vault_name}")
    out = _send(argv)
    if out is None:
        return None
    return True
