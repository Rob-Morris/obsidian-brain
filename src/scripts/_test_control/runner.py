"""Supervise one serial pytest process outside its termination group."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

from .records import RUN_ENV, atomic_json, input_identity, read_json, summary


REPOSITORY = Path(__file__).resolve().parents[3]
STOP_GRACE = 5.0
TERMINATE_GRACE = 3.0


def child_environment(root: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PYTEST_CURRENT_TEST", None)
    env["PYTHONPATH"] = os.pathsep.join((str(REPOSITORY / "src/scripts"),
        str(REPOSITORY / "tests"), str(root), env.get("PYTHONPATH", "")))
    return env


def request(directory: Path, action: str, reason: str) -> dict:
    """Publish a command bound to the observed run and unique test attempt."""
    if os.name != "posix" or action not in {"finish", "interrupt"}:
        raise ValueError("Unsupported control action or host")
    import fcntl
    with (directory / "request.lock").open("a") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another control request is being published") from None
        return _publish_request(directory, action, reason)


def _publish_request(directory: Path, action: str, reason: str) -> dict:
    if (directory / "receipt.json").exists():
        raise ValueError("Run already finished; use status or follow-up")
    current = read_json(directory / "current.json")
    if current["phase"] == "finished":
        raise ValueError("Observed attempt already finished; inspect current status")
    if current.get("interrupted"):
        raise ValueError("Wrap-up has begun; inspect status rather than replacing its request")
    existing = directory / "request.json"
    if existing.exists():
        prior = read_json(existing)
        if prior.get("attempt_id") == current["attempt_id"] and prior.get("action") == "interrupt":
            raise ValueError("Interruption already requested; its deadline is not renewed")
    if not reason.strip() or len(reason) > 1024:
        raise ValueError("Supply a non-empty reason of at most 1024 characters")
    command = {k: current[k] for k in ("run_id", "attempt_id", "nodeid")}
    command.update(action=action, reason=reason, request_id=uuid.uuid4().hex)
    atomic_json(directory / "request.json", command)
    return command


def combined(directory: Path) -> dict:
    """Combine applicable development coverage, retaining original attempt files."""
    chain, seen = [], set()
    current = directory.resolve()
    while current is not None:
        if current in seen or len(seen) >= 100:
            raise ValueError("Invalid or excessive follow-up chain")
        seen.add(current)
        result = summary(current)
        launch = read_json(current / "launch.json")
        chain.append((result, launch))
        previous = launch.get("previous")
        current = Path(previous).resolve() if previous else None
    identity = chain[0][1]["identity"]
    if any(launch["identity"] != identity for _, launch in chain):
        raise ValueError("Follow-up input identity mismatch")
    ancestors_valid = all(result["receipt"] and not any(result["receipt"][flag] for flag in (
        "inputs_changed", "requires_full_rerun", "run_error", "cleanup_uncertain", "escalated"))
        for result, _ in chain)
    oldest = chain[-1][0]
    selected = oldest["scope"]["nodeids"] if oldest["scope"] else []
    completed, failures = set(), set()
    for result, _ in reversed(chain):
        completed.update(result["completed"])
        failures.update(result["failed"])
    receipt = chain[0][0]["receipt"]
    outstanding = [node for node in selected if node not in completed]
    return {"run_id": chain[0][0]["run_id"], "runs": len(chain), "selected": len(selected),
            "completed": len(completed), "outstanding": outstanding, "failed": sorted(failures),
            "complete": bool(selected) and not outstanding and not failures and ancestors_valid
                and receipt.get("returncode") == 0 and not receipt.get("inputs_changed")
                and not receipt.get("requires_full_rerun"),
            "kind": "combined development evidence" if len(chain) > 1 else "development evidence",
            "history_parent": chain[0][1].get("followed_from"),
            "release_certification": False}


def stop_process(child) -> dict:
    """Bound escalation for the owned process group; never resume this pytest."""
    facts = {"escalated": True, "cleanup_uncertain": True, "reaped": False}
    try:
        os.killpg(child.pid, signal.SIGINT)
    except ProcessLookupError:
        pass
    try:
        child.wait(timeout=TERMINATE_GRACE)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            child.wait(timeout=TERMINATE_GRACE)
        except subprocess.TimeoutExpired:
            return facts
    # Also stop descendants retaining descriptors after their parent exited.
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    facts["reaped"] = child.poll() is not None
    return facts


def run(root: Path, directory: Path | None, pytest_args: list[str], inputs: list[str],
        *, previous: Path | None = None) -> tuple[int, dict]:
    if os.name != "posix":
        raise ValueError("The opt-in test controller currently supports POSIX developer hosts only")
    root = root.resolve()
    inputs = [str((root / value).resolve()) for value in inputs]
    env = child_environment(root)
    identity = input_identity(root, inputs, env)
    only = None
    followed_from = str(previous.resolve()) if previous else None
    if previous:
        previous = previous.resolve()
        old = read_json(previous / "launch.json")
        if old["identity"] != identity:
            raise ValueError("Inputs changed: reassess verification; prior passes cannot be combined")
        prior = summary(previous)
        if not prior["receipt"]:
            raise ValueError("No terminal controller receipt; confirm the original run has stopped")
        if prior["receipt"].get("returncode") is None or prior["receipt"].get("reaped") is False:
            raise ValueError("Owned run is not confirmed stopped; inspect cleanup before fresh verification")
        if prior["receipt"].get("inputs_changed"):
            raise ValueError("Inputs changed during the original run; start fresh verification")
        prior_combined = combined(previous)
        if prior_combined["failed"]:
            raise ValueError("Genuine test failures require diagnosis and fresh verification, not automatic follow-up")
        if prior["receipt"].get("requires_full_rerun") or prior["scope"] is None:
            previous = None
        else:
            only = prior_combined["outstanding"]
            if not only:
                raise ValueError("No outstanding tests")
    run_id = uuid.uuid4().hex
    directory = (directory or root / ".pytest_cache" / "controlled" / run_id).resolve()
    if directory == root or root.is_relative_to(directory):
        raise ValueError("State directory must not contain the source root")
    if directory.is_relative_to(root) and not directory.is_relative_to(root / ".pytest_cache"):
        raise ValueError("Put run state outside source inputs or under .pytest_cache")
    if any(Path(value).is_dir() and directory.is_relative_to(Path(value)) for value in inputs):
        raise ValueError("Run state must be outside declared input directories")
    directory.mkdir(parents=True, mode=0o700)
    launch = {"run_id": run_id, "root": str(root), "identity": identity,
              "pytest_args": pytest_args, "inputs": inputs, "only": only,
              "followed_from": followed_from,
              "previous": str(previous) if previous else None}
    atomic_json(directory / "launch.json", launch)
    env[RUN_ENV] = str(directory)
    # Contributor-only script imports the canonical managed Python launch owner.
    scripts = str(REPOSITORY / "src/brain-core/scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from _common._venv import managed_command, ROLE_CLI
    command = managed_command([sys.executable, "-m", "_test_control.pytest_run", *pytest_args],
                              role=ROLE_CLI, env=env)
    receipt = {"run_id": run_id, "returncode": None, "escalated": False, "cleanup_uncertain": False}
    child = None
    previous_term = signal.getsignal(signal.SIGTERM)
    def terminate(signum, frame):
        raise KeyboardInterrupt("Controller termination requested")
    signal.signal(signal.SIGTERM, terminate)
    print(json.dumps({"run_directory": str(directory), "run_id": run_id}), flush=True)
    try:
        with (directory / "stdout.log").open("wb") as stdout, (directory / "stderr.log").open("wb") as stderr:
            child = command.popen(cwd=root, stdout=stdout, stderr=stderr, start_new_session=True)
            atomic_json(directory / "process.json", {"controller_pid": os.getpid(), "pytest_pid": child.pid})
            observed_request = None
            deadline = None
            while child.poll() is None:
                current_path, request_path = directory / "current.json", directory / "request.json"
                if current_path.exists() and request_path.exists():
                    current, control = read_json(current_path), read_json(request_path)
                    matches = all(control.get(k) == current.get(k) for k in ("run_id", "attempt_id", "nodeid"))
                    if (matches and (current["phase"] != "finished" or current.get("stop_pending"))
                            and control.get("action") == "interrupt"):
                        if current["attempt_id"] != observed_request:
                            observed_request = current["attempt_id"]
                            deadline = time.monotonic() + STOP_GRACE
                            receipt["reason"] = control["reason"]
                        if time.monotonic() >= deadline:
                            receipt.update(stop_process(child))
                            break
                    else:
                        deadline = None
                time.sleep(.05)
            receipt["returncode"] = child.poll()
    except BaseException:
        if child is not None:
            receipt.update(stop_process(child))
            receipt["returncode"] = child.poll()
        raise
    finally:
        try:
            receipt["inputs_changed"] = input_identity(root, inputs, env) != identity
            state = summary(directory)
            session_path = directory / "session.json"
            session = read_json(session_path) if session_path.exists() else {}
            request_path = directory / "request.json"
            control = read_json(request_path) if request_path.exists() else {}
            intentional_finish = (receipt.get("returncode") == 2 and state["current"]
                and state["current"]["phase"] == "finished" and control.get("action") == "finish"
                and control.get("attempt_id") == state["current"]["attempt_id"])
            records = list(_records(directory, state))
            known_incomplete = any(r["outcome"] == "interrupted" for r in records)
            normal_exit = (isinstance(session, dict) and session.get("run_id") == run_id
                and type(session.get("exitstatus")) is int
                and session["exitstatus"] == receipt.get("returncode"))
            receipt["run_error"] = (not normal_exit or (directory / "run-error.json").exists()
                or (receipt.get("returncode") == 1 and not state["failed"] and not known_incomplete))
            receipt["requires_full_rerun"] = (receipt["run_error"] or receipt["cleanup_uncertain"] or state["scope"] is None
                or (receipt.get("returncode") not in (0, 1) and not intentional_finish)
                or any(r["outcome"] == "unresolved" or
                    (r["outcome"] == "interrupted" and not r["cleanup_confirmed"])
                    for r in records))
            receipt["session_exitstatus"] = session.get("exitstatus") if isinstance(session, dict) else None
        except (ValueError, OSError, KeyError):
            receipt.update(inputs_changed=True, requires_full_rerun=True, evidence_error=True)
        finally:
            atomic_json(directory / "receipt.json", receipt)
            signal.signal(signal.SIGTERM, previous_term)
    result = combined(directory)
    result["run_directory"] = str(directory)
    return (0 if result["complete"] else 1), result


def _records(directory, state):
    from .records import outcomes
    return outcomes(directory, state["scope"]).values() if state["scope"] else ()
