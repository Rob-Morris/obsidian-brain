"""
Lightweight HTTP client for the Obsidian CLI REST endpoint (dsebastien/obsidian-cli-rest).

Stdlib only — no external dependencies. All functions catch network/parse errors and
return None/False so the MCP server never crashes due to CLI unavailability.
"""

import json
import os
import urllib.request
import urllib.error

OBSIDIAN_CLI_URL = os.environ.get("OBSIDIAN_CLI_URL", "http://localhost:27124")


def check_available() -> bool:
    """Health check — returns True if the Obsidian CLI REST server is reachable."""
    try:
        req = urllib.request.Request(f"{OBSIDIAN_CLI_URL}/", method="GET")
        with urllib.request.urlopen(req, timeout=1) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def search(vault_name: str, query: str) -> list[dict] | None:
    """Search via Obsidian's live index. Returns list of result dicts or None on failure."""
    try:
        url = f"{OBSIDIAN_CLI_URL}/search/simple/?query={urllib.request.quote(query)}&vault={urllib.request.quote(vault_name)}"
        req = urllib.request.Request(url, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if isinstance(data, list):
                return data
            return None
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None


def move(vault_name: str, source: str, dest: str) -> dict | None:
    """Move/rename a file via Obsidian CLI (wikilink-safe). Returns result dict or None."""
    try:
        payload = json.dumps({
            "vault": vault_name,
            "source": source,
            "dest": dest,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{OBSIDIAN_CLI_URL}/vault/move/",
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None
