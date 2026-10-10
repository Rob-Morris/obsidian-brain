"""Direct-script admission to the canonical host-local command owners."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys

from _bootstrap.file_transaction import FilePlan
from _bootstrap.mcp_registration import APPROVAL_LEDGER_SCHEMA, user_ledger_path
from _common._venv import run_managed


def approvals_present(home: Path | None = None) -> bool:
    """Corrupt or interrupted state still requires the managed writer."""
    base = user_ledger_path(home or Path.home()).parent
    return any(FilePlan().read_bytes(base / name) is not None for name in (
        "client-approvals.json", "client-approvals.pending.json", "client-approvals.transitions.json"))


def _approval_ledger(home: Path | None):
    """The managed approval ledger's records, or ``None`` when it cannot be used as the writer would.

    ``{}`` when the ledger is absent. A ledger that cannot be read or parsed, or
    whose schema is not the one the managed writer accepts, gives ``None``: the
    writer refuses it too.
    """
    path = user_ledger_path(home or Path.home()).with_name("client-approvals.json")
    try:
        content = FilePlan().read_text(path)
        if content is None:
            return {}
        value = json.loads(content)
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict) or value.get("schema") != APPROVAL_LEDGER_SCHEMA:
        return None
    records = value.get("records")
    return records if isinstance(records, dict) else None


def approval_records_present(home: Path | None = None) -> bool:
    """Whether the managed approval ledger holds records, or cannot be used.

    The managed writer reads the strict Brain inventory around a registry
    change only when it holds records, so this is the fact that decides whether
    that inventory can refuse. A ledger it could not use counts as holding
    records: the writer refuses it too.
    """
    records = _approval_ledger(home)
    return records is None or bool(records)


def approval_state_blocks_changes(home: Path | None = None) -> bool:
    """Whether the managed writer refuses every registry change until approvals are recovered.

    That is an unusable ledger or a pending approval transaction journal.
    """
    if _approval_ledger(home) is None:
        return True
    journal = user_ledger_path(home or Path.home()).with_name("client-approvals.pending.json")
    try:
        return FilePlan().read_bytes(journal) is not None
    except (OSError, ValueError):
        return True


def invoke(command: str, request: dict, *, source_root: Path | None = None,
           vault: Path | None = None, target: Path | None = None, dry_run: bool = False) -> dict:
    """Select explicit source code when supplied; otherwise require a compatible CLI."""
    binary = shutil.which("brain")
    if binary is None:
        install_root = Path.home() / ".local"
        if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
            install_root = Path(os.environ["LOCALAPPDATA"]) / "Programs/Brain"
        candidate = install_root / "bin" / ("brain.cmd" if sys.platform == "win32" else "brain")
        if candidate.is_file():
            binary = str(candidate)
    if binary is None:
        raise ValueError("Install the managed-approvals-capable Brain CLI before this machine change.")
    environment = dict(os.environ)
    for key in ("BRAIN_CLI_BUNDLE", "BRAIN_CLI_DISTRIBUTION_ROOT", "PYTHONPATH", "PYTHONHOME", "BRAIN_VAULT_ROOT", "BRAIN_WORKSPACE_DIR"):
        environment.pop(key, None)
    prefix = [binary]
    if source_root is not None:
        source_root = source_root.resolve()
        if not (source_root / "cli/_launcher/approval_lifecycle.py").is_file():
            raise ValueError("This source cannot reconcile managed approvals; use a complete compatible distribution.")
        environment["BRAIN_CLI_BINARY"] = binary
        environment["BRAIN_CLI_DISTRIBUTION_ROOT"] = str(source_root)
        environment["PYTHONPATH"] = os.pathsep.join((str(source_root / "cli"), str(source_root / "src/brain-core/scripts")))
        prefix = [sys.executable, "-B", "-m", "_local_cli.main"]
    probe = run_managed([*prefix, "command", "describe", "approvals.configure", "--json"],
                        env=environment, capture_output=True, text=True, timeout=30)
    if probe.returncode:
        raise ValueError("The selected Brain CLI predates managed approvals; upgrade/reinstall it first.")
    words = command.split(".")
    if words[0] == "brain":
        words = words[1:]
    argv = [*prefix, *words, "--request-json", json.dumps(request), "--json"]
    if vault is not None:
        argv += ["--vault", str(vault)]
    if dry_run:
        argv.append("--dry-run")
    process = run_managed(argv, cwd=target, env=environment, capture_output=True, text=True, timeout=1800)
    try:
        result = json.loads(process.stdout)
    except ValueError as exc:
        raise RuntimeError(f"Machine command returned no receipt; inspect before retrying: {process.stderr}") from exc
    if not isinstance(result, dict) or result.get("schema") != "brain.command-result/1":
        raise RuntimeError("Machine command returned an invalid receipt; inspect before retrying")
    return result
