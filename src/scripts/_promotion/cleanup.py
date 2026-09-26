"""Inventory and explicit cleanup of disposable promotion state."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
from pathlib import Path

from . import candidates, git
from .model import PROMOTION_BRANCH_RE, PromotionError


@dataclass(frozen=True)
class CleanupItem:
    """One observed ownership claim, with its safe action and explanation."""

    scope: str
    branch: str
    sha: str | None
    state: str
    action: str
    reason: str
    worktree: str | None = None


def _local_refs(root: Path, prefix: str) -> dict[str, str]:
    raw = git.out(root, "for-each-ref", "--format=%(refname) %(objectname)", prefix)
    return {ref.removeprefix(prefix): sha for ref, sha in
            (line.split() for line in raw.splitlines())}


def inventory(root: Path) -> list[CleanupItem]:
    """Refresh shared refs, then classify without deleting any candidate state."""
    git.fetch_refs(root)
    main = set(git.ancestry(root, git.rev(root, "refs/remotes/origin/main")))
    ledger = set(git.ancestry(root, candidates.ledger_tip(root)))
    remote = {ref.removeprefix("refs/heads/"): sha for sha, ref in git.promotion_refs(root)}
    local = _local_refs(root, "refs/heads/")
    tracking = _local_refs(root, "refs/remotes/origin/")
    local = {name: sha for name, sha in local.items() if name.startswith("promotion/")}
    tracking = {name: sha for name, sha in tracking.items() if name.startswith("promotion/")}
    worktree_names = set()
    owned_parent = root / ".worktrees"
    for path, _ref in git.worktrees(root):
        path = Path(path)
        if path.parent == owned_parent and PROMOTION_BRANCH_RE.fullmatch(f"promotion/{path.name}"):
            worktree_names.add(f"promotion/{path.name}")
    if owned_parent.is_dir() and not owned_parent.is_symlink():
        worktree_names.update(f"promotion/{path.name}" for path in owned_parent.iterdir()
                             if PROMOTION_BRANCH_RE.fullmatch(f"promotion/{path.name}"))
    validated: dict[tuple[str, str], tuple[str, str]] = {}

    def classify(branch: str, sha: str | None) -> tuple[str, str]:
        if sha is None or not PROMOTION_BRANCH_RE.fullmatch(branch):
            return "unknown", "no canonical candidate identity; preserve"
        if (branch, sha) in validated:
            return validated[branch, sha]
        if sha not in main and sha not in ledger:
            return "unfinished", "not on the shared ledger; explicit discard required"
        try:
            candidates.validate_candidate(root, git.read_commit(root, sha), branch)
        except PromotionError as exc:
            result = ("unknown", f"candidate validation failed; preserve: {exc}")
        else:
            result = ("published", "reachable on published main") if sha in main else (
                "finished", "on unreleased; retain the remote candidate until publication")
        validated[branch, sha] = result
        return result

    items = []
    registered = {Path(path) for path, _ref in git.worktrees(root)}
    for branch in sorted(set(local) | worktree_names):
        worktree = git.worktree_dir(root, branch)
        has_worktree = worktree.exists() or worktree.is_symlink() or worktree in registered
        sha = local.get(branch)
        if sha is None and worktree in registered and worktree.is_dir() and not worktree.is_symlink():
            sha = git.rev(worktree, "HEAD")
        state, reason = classify(branch, sha)
        action = "remove" if state in {"published", "finished"} else "keep"
        if sha is not None:
            problem = git.local_cleanup_problem(root, branch, sha)
            if problem:
                action, reason = "keep", problem
        items.append(CleanupItem("local", branch, sha, state, action, reason,
                                 str(worktree) if has_worktree else None))
    for branch, sha in sorted(remote.items()):
        state, reason = classify(branch, sha)
        items.append(CleanupItem("remote", branch, sha, state,
                                 "remove" if state == "published" else "keep", reason))
    for branch, sha in sorted(tracking.items()):
        state, reason = classify(branch, sha)
        disposable = state in {"published", "finished"} and (
            branch not in remote or (state == "published" and remote[branch] == sha))
        items.append(CleanupItem("tracking", branch, sha, state,
                                 "remove" if disposable else "keep", reason))
    return items


def _apply(root: Path, items: list[CleanupItem]) -> tuple[list[CleanupItem], list[str]]:
    results, errors = [], []
    remote_error = None
    remote = [item for item in items if item.scope == "remote" and item.action == "remove"]
    if remote:
        try:
            git.delete_remote_refs(root, {f"refs/heads/{item.branch}": item.sha for item in remote})
        except PromotionError as exc:
            remote_error = str(exc)
            errors.append(remote_error)
    for item in items:
        if item.action == "keep":
            results.append(item)
            continue
        try:
            if item.scope == "remote":
                if remote_error is not None:
                    results.append(replace(item, action="unconfirmed", reason=remote_error))
                    continue
            elif item.scope == "local":
                git.cleanup_local(root, item.branch, item.sha)
            else:
                if git.remote_branch(root, item.branch) is not None:
                    results.append(replace(item, action="keep", reason="remote branch still exists; retain tracker"))
                    continue
                ref = f"refs/remotes/origin/{item.branch}"
                if git.rev_exists(root, ref):
                    git.run(root, "update-ref", "-d", ref, item.sha)
            results.append(replace(item, action="removed"))
        except PromotionError as exc:
            errors.append(str(exc))
            results.append(replace(item, action="failed", reason=str(exc)))
    return results, errors


def run(root: Path, *, apply: bool = False, as_json: bool = False) -> dict[str, object]:
    """Preview by default; apply only freshly verified, exact-SHA removals."""
    lock = git.lock(root)
    try:
        items = inventory(root)
        errors = []
        if apply:
            items, errors = _apply(root, items)
        result = {"applied": apply, "items": [asdict(item) for item in items], "errors": errors}
        if as_json:
            print(json.dumps(result, indent=2))
        else:
            print("promotion cleanup " + ("apply" if apply else "preview (use --apply to remove)"))
            for item in items:
                print(f"{item.scope} {item.branch} {item.sha or '-'}: {item.action} ({item.state}); {item.reason}")
            if not items:
                print("no promotion state to clean up")
            for error in errors:
                print(f"cleanup incomplete: {error}")
        return result
    finally:
        lock.close()
