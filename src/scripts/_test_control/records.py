"""Small, single-writer run records that survive loss of the pytest process."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid


RUN_ENV = "BRAIN_TEST_CONTROL_RUN"
MAX_RECORD = 65536


def terminal_outcome(phases: dict[str, str], interrupted: bool) -> str:
    """One policy for producing and validating terminal attempt evidence."""
    if "failed" in phases.values():
        return "failed"
    if interrupted:
        return "interrupted" if phases == {"setup": "passed", "call": "skipped", "teardown": "passed"} else "unresolved"
    if phases == {"setup": "passed", "call": "passed", "teardown": "passed"}:
        return "passed"
    if phases in ({"setup": "skipped", "teardown": "passed"},
                  {"setup": "passed", "call": "skipped", "teardown": "passed"}):
        return "skipped"
    return "unresolved"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, value) -> None:
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=True)
            stream.flush()
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def append_outcome(directory: Path, record: dict) -> None:
    payload = (json.dumps(record, ensure_ascii=True) + "\n").encode()
    if len(payload) > MAX_RECORD:
        raise ValueError("Test outcome exceeds its bounded record size")
    with (directory / "outcomes.jsonl").open("ab", buffering=0) as stream:
        if stream.write(payload) != len(payload):
            raise OSError("Incomplete test outcome write")


def outcomes(directory: Path, scope: dict) -> dict[str, dict]:
    """Credit complete matching records; a torn final append grants no pass."""
    path = directory / "outcomes.jsonl"
    result = {}
    if not path.exists():
        return result
    selected = set(scope["nodeids"])
    with path.open("rb") as stream:
        while line := stream.readline(MAX_RECORD + 1):
            if len(line) > MAX_RECORD:
                raise ValueError("Oversized test outcome record")
            if not line.endswith(b"\n"):
                break
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError("Invalid test outcome record")
            if "node_index" in record:
                index = record["node_index"]
                if type(index) is not int or not 0 <= index < len(scope["nodeids"]) or "nodeid" in record:
                    raise ValueError("Invalid or ambiguous manifest test reference")
                node = scope["nodeids"][index]
            else:
                # Earlier run files contain raw IDs; retain readable history.
                node = record.get("nodeid")
            if (record.get("run_id") != scope["run_id"] or not isinstance(node, str) or node not in selected
                    or record.get("identity") != scope["identity"]
                    or record.get("outcome") not in {"passed", "skipped", "failed", "interrupted", "unresolved"}
                    or not isinstance(record.get("attempt_id"), str) or not record["attempt_id"] or node in result):
                raise ValueError("Conflicting or foreign test outcome")
            phases = record.get("phases")
            if (not isinstance(phases, dict) or not set(phases) <= {"setup", "call", "teardown"}
                    or any(value not in {"passed", "failed", "skipped"} for value in phases.values())):
                raise ValueError("Missing or invalid phase evidence")
            outcome = record["outcome"]
            interrupted = isinstance(record.get("reason"), str) and bool(record["reason"].strip())
            if outcome != terminal_outcome(phases, interrupted):
                raise ValueError("Outcome contradicts terminal phase evidence")
            if outcome == "interrupted" and (
                    phases != {"setup": "passed", "call": "skipped", "teardown": "passed"}
                    or not isinstance(record.get("reason"), str) or not record["reason"].strip()
                    or not isinstance(record.get("cleanup_confirmed"), bool)):
                raise ValueError("Interruption lacks terminal cleanup evidence")
            result[node] = {**record, "nodeid": node}
    return result


def input_identity(root: Path, extra: list[str], environment: dict[str, str]) -> str:
    """Conservatively hash authored inputs and dependencies, not Git ancestry.

    Ignored or external fixtures must be declared with --input. Environment
    values are hashed only, so credentials are never persisted in the record.
    """
    git = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                         cwd=root, capture_output=True, timeout=10)
    if git.returncode == 0:
        files = [root / os.fsdecode(name) for name in git.stdout.split(b"\0") if name]
    else:
        files = [p for p in root.rglob("*") if p.is_file() and not any(
            part in {".git", ".venv", ".pytest_cache", "__pycache__"} for part in p.relative_to(root).parts)]
    for value in extra:
        external = Path(value).resolve()
        if not external.exists():
            raise ValueError("Declared input does not exist: " + str(external))
        files.append(external)
        if external.is_dir():
            descendants = list(external.rglob("*"))
            if any(path.is_symlink() and path.is_dir() for path in descendants):
                raise ValueError("Declare symlinked input directory targets separately")
            files.extend(descendants)
    files += list(Path(__file__).parent.glob("*.py"))
    files.append(Path(__file__).parent.parent / "dev_tests.py")
    digest = hashlib.sha256()
    for path in sorted(set(files)):
        digest.update(str(path).encode())
        if path.is_symlink():
            digest.update(os.readlink(path).encode())
        if path.is_file():
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
        elif path.is_dir():
            digest.update(b"<directory>")
        else:
            digest.update(b"<missing>")
    digest.update(sys.version.encode())
    packages = sorted((d.metadata["Name"], d.version) for d in importlib.metadata.distributions())
    digest.update(json.dumps(packages).encode())
    # A new agent tool invocation must not invalidate otherwise identical inputs.
    ignored = {RUN_ENV, "PWD", "OLDPWD", "SHLVL", "_", "PYTEST_CURRENT_TEST", "CODEX_TOOL_CALL_ID"}
    digest.update(json.dumps({k: v for k, v in environment.items() if k not in ignored}, sort_keys=True).encode())
    return digest.hexdigest()


def summary(directory: Path) -> dict:
    launch = read_json(directory / "launch.json")
    if not isinstance(launch, dict):
        raise ValueError("Invalid run identity")
    scope_path = directory / "scope.json"
    receipt_path = directory / "receipt.json"
    receipt = read_json(receipt_path) if receipt_path.exists() else {}
    if receipt_path.exists() and (not isinstance(receipt, dict)
            or receipt.get("run_id") != launch["run_id"]
            or not all(type(receipt.get(key)) is bool for key in (
                "escalated", "cleanup_uncertain", "inputs_changed", "requires_full_rerun", "run_error"))
            or "returncode" not in receipt
            or (receipt.get("returncode") is not None and type(receipt["returncode"]) is not int)):
        raise ValueError("Invalid or foreign controller receipt")
    scope = read_json(scope_path) if scope_path.exists() else None
    if scope is not None and (not isinstance(scope, dict) or scope.get("run_id") != launch["run_id"]
            or scope.get("identity") != launch["identity"]
            or not isinstance(scope.get("nodeids"), list)
            or any(not isinstance(node, str) for node in scope["nodeids"])
            or len(scope["nodeids"]) != len(set(scope["nodeids"]))):
        raise ValueError("Invalid or foreign test scope")
    records = outcomes(directory, scope) if scope else {}
    completed = [node for node, r in records.items() if r["outcome"] in {"passed", "skipped"}]
    failed = [node for node, r in records.items() if r["outcome"] == "failed"]
    pending = [node for node in scope["nodeids"] if node not in completed] if scope else []
    current_path = directory / "current.json"
    current = read_json(current_path) if current_path.exists() else None
    if current:
        end = current.get("updated", time.time()) if current["phase"] == "finished" else time.time()
        current["elapsed_seconds"] = max(0, end - current["started"])
    return {"run_id": launch["run_id"], "directory": str(directory), "scope": scope,
            "completed": completed, "failed": failed, "outstanding": pending,
            "receipt": receipt, "current": current}
