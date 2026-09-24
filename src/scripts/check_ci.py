"""Read-only, exact-commit GitHub CI verification for repository contributors."""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import time
from urllib.parse import urlencode


REQUIRED_WORKFLOWS = (
    ".github/workflows/linux-test.yml",
    ".github/workflows/windows-smoke.yml",
    ".github/workflows/dependency-certification.yml",
)
EXIT_CODES = {"passed": 0, "failed": 1, "pending": 2, "missing": 2, "unavailable": 3}
DELETION_RUN_TITLE = "Brain branch deletion (no CI)"


def read_runs(repo: str, commit: str, branch: str, event: str, timeout: float) -> list[dict]:
    """Fetch every matching run; incomplete or malformed evidence cannot certify CI."""
    query = urlencode({"head_sha": commit, "branch": branch, "event": event, "per_page": 100})
    response = subprocess.run(
        ["gh", "api", "--hostname", "github.com", "--method", "GET",
         f"repos/{repo}/actions/runs?{query}", "--paginate", "--slurp"],
        capture_output=True, text=True, timeout=timeout, check=False,
    )
    if response.returncode:
        raise ValueError(f"GitHub query failed: {response.stderr.strip()}")
    pages = json.loads(response.stdout)
    if not isinstance(pages, list) or not pages:
        raise ValueError("GitHub returned no page envelope")
    runs = []
    totals = set()
    for page in pages:
        if (not isinstance(page, dict) or type(page.get("total_count")) is not int
                or page["total_count"] < 0 or not isinstance(page.get("workflow_runs"), list)):
            raise ValueError("Malformed GitHub run page")
        totals.add(page["total_count"])
        runs.extend(page["workflow_runs"])
    ids = []
    for run in runs:
        if not isinstance(run, dict) or type(run.get("id")) is not int or run["id"] <= 0:
            raise ValueError("Malformed GitHub run identity")
        ids.append(run["id"])
    if totals != {len(runs)} or len(set(ids)) != len(ids):
        raise ValueError("Incomplete or changing GitHub run inventory; check again")
    return runs


def _is_branch_deletion(run: dict) -> bool:
    """Only the workflow's explicit push-deletion marker proves this is not an attempt."""
    return run.get("event") == "push" and run.get("display_title") == DELETION_RUN_TITLE


def _workflow_record(path: str, run: dict, state: str) -> dict:
    return {
        "workflow": path,
        "state": state,
        "run_id": run["id"],
        "attempt": run["run_attempt"],
        "status": run["status"],
        "conclusion": run.get("conclusion"),
        "url": run["html_url"],
    }


def evaluate_runs(runs: list[dict], commit: str, branch: str, event: str) -> dict:
    """Require the newest real attempt of each required workflow.

    A positively identified branch-deletion run is not an attempt. Every other
    newer run, including a skipped run, supersedes earlier evidence.
    """
    grouped: dict[str, list[dict]] = {path: [] for path in REQUIRED_WORKFLOWS}
    for run in runs:
        if (run.get("head_sha"), run.get("head_branch"), run.get("event")) != (commit, branch, event):
            raise ValueError("GitHub returned a run outside the requested commit/branch/event")
        path = run.get("path")
        if not isinstance(path, str):
            raise ValueError("GitHub run is missing its workflow path")
        path = path.split("@", 1)[0]
        if path not in REQUIRED_WORKFLOWS:
            continue
        if (type(run.get("id")) is not int or run["id"] <= 0
                or type(run.get("run_attempt")) is not int or run["run_attempt"] <= 0
                or not isinstance(run.get("html_url"), str) or not run["html_url"]):
            raise ValueError("GitHub run is missing identity, attempt or URL evidence")
        if run.get("status") not in {"queued", "in_progress", "completed", "waiting", "requested", "pending"}:
            raise ValueError("Unrecognised GitHub run status")
        if run["status"] == "completed" and not isinstance(run.get("conclusion"), str):
            raise ValueError("Completed GitHub run has no conclusion")
        if _is_branch_deletion(run):
            continue
        grouped[path].append(run)
    workflows = []
    for path in REQUIRED_WORKFLOWS:
        choices = sorted(grouped[path], key=lambda item: (item["id"], item["run_attempt"]), reverse=True)
        if not choices:
            workflows.append({"workflow": path, "state": "missing"})
            continue
        run = choices[0]
        state = "pending" if run["status"] != "completed" else (
            "passed" if run["conclusion"] == "success" else "failed"
        )
        workflows.append(_workflow_record(path, run, state))
    states = {item["state"] for item in workflows}
    state = next(item for item in ("failed", "missing", "pending", "passed") if item in states)
    return {"state": state, "workflows": workflows}


def check(repo: str, commit: str, branch: str, event: str, *, wait: bool, timeout: float) -> dict:
    """Observe once, or wait within a fixed budget; never rerun, dispatch or mutate CI."""
    deadline = time.monotonic() + timeout
    identity = {"repository": repo, "commit": commit, "branch": branch, "event": event}
    while True:
        try:
            remaining = max(0.001, deadline - time.monotonic())
            runs = read_runs(repo, commit, branch, event, min(30, remaining))
            result = {**identity, **evaluate_runs(runs, commit, branch, event)}
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            return {**identity, "state": "unavailable", "workflows": [], "error": str(exc)}
        if not wait or result["state"] in {"passed", "failed"}:
            return result
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {**result, "wait_expired": True}
        time.sleep(min(10, remaining))
        if time.monotonic() >= deadline:
            return {**result, "wait_expired": True}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="Explicit GitHub.com OWNER/REPO")
    parser.add_argument("--commit", required=True, help="Full 40-character pushed commit SHA")
    parser.add_argument("--branch", required=True, help="Branch used for the push or dispatch")
    parser.add_argument("--event", choices=("push", "workflow_dispatch"), default="push")
    parser.add_argument("--wait", action="store_true", help="Poll pending/missing runs within --timeout")
    parser.add_argument("--timeout", type=float, default=300, help="Wait budget in seconds (default 300); each query capped at 30")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo):
        parser.error("--repo must be OWNER/REPO")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", args.commit):
        parser.error("--commit must be a full 40-character SHA")
    if not args.branch.strip():
        parser.error("--branch cannot be empty")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be a finite positive number")
    result = check(args.repo, args.commit.lower(), args.branch, args.event,
                   wait=args.wait, timeout=args.timeout)
    if args.json:
        print(json.dumps(result))
    else:
        print(f"CI {result['state']}: {result['repository']} {result['commit']} ({result['branch']}, {result['event']})")
        for workflow in result["workflows"]:
            print(f"  {workflow['workflow']}: {workflow['state']}"
                  f" (attempt {workflow.get('attempt', '-')}, {workflow.get('conclusion') or '-'})"
                  f" {workflow.get('url', '')}")
        if result.get("error"):
            print(result["error"])
        if result.get("wait_expired"):
            print("Wait budget expired; CI remains unverified. Check again before propagation.")
    return EXIT_CODES[result["state"]]


if __name__ == "__main__":
    raise SystemExit(main())
