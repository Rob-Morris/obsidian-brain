"""Concrete Git operations, worktree safety and expected-SHA cleanup."""
from __future__ import annotations

import fcntl
import os
from pathlib import Path
import subprocess
from typing import Mapping
from .model import (
    PromotionError, GitCommit, RemoteHeads, SHA_RE
)

def run(
    root: Path,
    *args: str,
    input_text: str | None = None,
    check: bool = True,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        input=input_text,
        text=True,
        capture_output=True,
        env=None if env is None else dict(env),
        check=False,
    )
    if check and completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no output"
        raise PromotionError(f"git {' '.join(args)} failed: {detail}")
    return completed


def out(root: Path, *args: str) -> str:
    return run(root, *args).stdout.strip()


def rev(root: Path, ref: str) -> str:
    sha = out(root, "rev-parse", "--verify", f"{ref}^{{commit}}")
    if not SHA_RE.fullmatch(sha):
        raise PromotionError(f"{ref} is not a commit")
    return sha


def read_commit(root: Path, sha: str) -> GitCommit:
    """Read one commit without checking it out."""
    raw = run(root, "cat-file", "-p", sha).stdout
    header, _, message = raw.partition("\n\n")
    tree = ""
    parents: list[str] = []
    for line in header.splitlines():
        kind, _, value = line.partition(" ")
        if kind == "tree":
            tree = value
        elif kind == "parent":
            parents.append(value)
    if not SHA_RE.fullmatch(tree):
        raise PromotionError(f"{sha} has no tree")
    return GitCommit(sha, tree, tuple(parents), message)


def ancestry(root: Path, tip: str) -> list[str]:
    return out(root, "rev-list", "--first-parent", tip).splitlines()


def commits_after(root: Path, start: str, end: str) -> list[GitCommit]:
    if start == end:
        return []
    listed = out(root, "rev-list", "--first-parent", "--reverse", f"{start}..{end}")
    return [read_commit(root, sha) for sha in listed.splitlines() if sha]


def identity_env(commit_date: str, name: str, email: str) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": name,
            "GIT_AUTHOR_EMAIL": email,
            "GIT_AUTHOR_DATE": commit_date,
            "GIT_COMMITTER_NAME": name,
            "GIT_COMMITTER_EMAIL": email,
            "GIT_COMMITTER_DATE": commit_date,
        }
    )
    return env


def replay_env(root: Path, sha: str) -> dict[str, str]:
    """Preserve source author/committer metadata so rebuilding a replay is deterministic."""
    values = out(root, "show", "-s", "--format=%an%x00%ae%x00%aI%x00%cn%x00%ce%x00%cI", sha).split("\0")
    keys = ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_AUTHOR_DATE",
            "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "GIT_COMMITTER_DATE")
    return {**os.environ, **dict(zip(keys, values, strict=True))}


def configured_identity(root: Path) -> tuple[str, str]:
    name = out(root, "config", "user.name")
    email = out(root, "config", "user.email")
    if not name or not email:
        raise PromotionError("git user.name and user.email must be configured")
    return name, email


def commit_tree(
    root: Path,
    tree: str,
    parents: tuple[str, ...],
    message: str,
    env: Mapping[str, str],
) -> str:
    args = ["commit-tree", tree]
    for parent in parents:
        args.extend(["-p", parent])
    sha = run(root, *args, input_text=message, env=env).stdout.strip()
    if not SHA_RE.fullmatch(sha):
        raise PromotionError("git commit-tree did not return a SHA")
    return sha


def merge_tree(root: Path, base: str, ours: str, theirs: str) -> str:
    completed = run(
        root,
        "merge-tree",
        "--write-tree",
        f"--merge-base={base}",
        ours,
        theirs,
        check=False,
    )
    tree = completed.stdout.splitlines()[0].strip() if completed.stdout.splitlines() else ""
    if completed.returncode != 0 or not SHA_RE.fullmatch(tree):
        detail = completed.stdout.strip() or completed.stderr.strip()
        raise PromotionError(f"tail replay conflict for {theirs} onto {ours}:\n{detail}")
    return tree


def tree_of(root: Path, rev: str) -> str:
    return out(root, "rev-parse", f"{rev}^{{tree}}")


def is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    if ancestor == descendant:
        return True
    completed = run(root, "merge-base", "--is-ancestor", ancestor, descendant, check=False)
    if completed.returncode == 0:
        return True
    if completed.returncode == 1:
        return False
    detail = completed.stderr.strip() or completed.stdout.strip() or "no output"
    raise PromotionError(f"git merge-base --is-ancestor failed: {detail}")


def worktrees(root: Path) -> list[tuple[str, str | None]]:
    raw = run(root, "worktree", "list", "--porcelain").stdout
    found: list[tuple[str, str | None]] = []
    current_path = ""
    branch: str | None = None
    for line in raw.splitlines() + [""]:
        if not line:
            if current_path:
                found.append((current_path, branch))
            current_path = ""
            branch = None
            continue
        if line.startswith("worktree "):
            current_path = line.partition(" ")[2]
        elif line.startswith("branch "):
            branch = line.partition(" ")[2]
        elif line == "detached":
            branch = None
    return found


def require_checkout(root: Path) -> None:
    completed = run(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    if completed.returncode != 0 or completed.stdout.strip() != "dev":
        raise PromotionError("the primary checkout must be on dev")
    status = run(root, "status", "--porcelain", "--untracked-files=all").stdout
    if status.strip():
        raise PromotionError("the dev checkout must be clean")
    counts = {"refs/heads/dev": 0, "refs/heads/main": 0}
    for _path, branch in worktrees(root):
        if branch in counts:
            counts[branch] += 1
    if counts["refs/heads/dev"] > 1 or counts["refs/heads/main"]:
        raise PromotionError("dev may be checked out once, and main not at all")


def lock(root: Path):
    common = Path(out(root, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = root / common
    handle = (common / "promotion.lock").open("a", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise PromotionError("another promotion holds the local lock") from exc
    return handle


def missing_remote_ref(detail: str) -> bool:
    return "couldn't find remote ref" in detail


def fetch_refs(root: Path) -> None:
    run(root, "fetch", "origin", "refs/heads/main:refs/remotes/origin/main",
        "refs/heads/dev:refs/remotes/origin/dev")
    completed = run(root, "fetch", "origin", "refs/heads/unreleased:refs/remotes/origin/unreleased", check=False)
    if completed.returncode == 0:
        return
    detail = completed.stderr.strip() or completed.stdout.strip() or "no output"
    if missing_remote_ref(detail):
        if rev_exists(root, "refs/remotes/origin/unreleased"):
            run(root, "update-ref", "-d", "refs/remotes/origin/unreleased")
        return
    raise PromotionError(f"could not fetch origin/unreleased: {detail}")


def observe_remote(root: Path) -> RemoteHeads:
    completed = run(
        root,
        "ls-remote",
        "origin",
        "refs/heads/main",
        "refs/heads/dev",
        "refs/heads/unreleased",
        check=False,
    )
    if completed.returncode != 0:
        return RemoteHeads(None, None, False)
    found: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        sha, ref = line.split()
        found[ref] = sha
    return RemoteHeads(
        found.get("refs/heads/main"),
        found.get("refs/heads/dev"),
        True,
        found.get("refs/heads/unreleased"),
    )


def worktree_dir(root: Path, branch: str) -> Path:
    slug = branch.removeprefix("promotion/")
    return root / ".worktrees" / slug


def rev_exists(root: Path, ref: str) -> bool:
    completed = run(root, "rev-parse", "--verify", "--quiet", ref, check=False)
    if completed.returncode == 0:
        return True
    if completed.returncode == 1:
        return False
    detail = completed.stderr.strip() or completed.stdout.strip() or "no output"
    raise PromotionError(f"git rev-parse --verify failed: {detail}")


def remote_branch(root: Path, branch: str) -> str | None:
    ref = f"refs/heads/{branch}"
    completed = run(root, "ls-remote", "origin", ref, check=False)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no output"
        raise PromotionError(f"cannot observe origin {branch}: {detail}")
    for line in completed.stdout.splitlines():
        sha, found = line.split()
        if found == ref:
            return sha
    return None


def local_cleanup_problem(root: Path, branch: str, candidate: str) -> str | None:
    """Explain why a candidate's local state cannot safely be removed."""
    worktree = worktree_dir(root, branch)
    registered = {Path(path): ref for path, ref in worktrees(root)}
    for path, ref in registered.items():
        if ref == f"refs/heads/{branch}" and path != worktree:
            return f"candidate is checked out outside its owned worktree: {path}"
    if worktree.is_symlink():
        return f"candidate worktree is a symlink: {worktree}"
    if worktree.exists():
        if worktree not in registered:
            return f"candidate worktree is not registered: {worktree}"
        if registered[worktree] not in {None, f"refs/heads/{branch}"}:
            return f"candidate worktree has a foreign branch: {worktree}"
        if rev(worktree, "HEAD") != candidate:
            return f"candidate worktree HEAD differs from {candidate}: {worktree}"
        if run(worktree, "status", "--porcelain", "--untracked-files=all").stdout.strip():
            return f"candidate worktree is dirty: {worktree}"
    elif worktree in registered:
        return f"candidate worktree is missing; inspect its registration: {worktree}"
    ref = f"refs/heads/{branch}"
    if rev_exists(root, ref) and rev(root, ref) != candidate:
        return f"local {branch} no longer identifies {candidate}"
    return None


def cleanup_local(root: Path, branch: str, candidate: str | None = None) -> None:
    """Remove only the expected clean checkout/ref; retain recovery state on failure."""
    worktree = worktree_dir(root, branch)
    ref = f"refs/heads/{branch}"
    if candidate is None:
        if rev_exists(root, ref):
            candidate = rev(root, ref)
        elif worktree.exists():
            raise PromotionError(f"candidate worktree has no ownership ref: {worktree}")
        else:
            return
    problem = local_cleanup_problem(root, branch, candidate)
    if problem:
        raise PromotionError(problem)
    if worktree.exists():
        run(root, "worktree", "remove", str(worktree))
    problem = local_cleanup_problem(root, branch, candidate)
    if problem:
        raise PromotionError(problem)
    if rev_exists(root, ref):
        run(root, "update-ref", "-d", ref, candidate)


def discard_owned(root: Path, branch: str, candidate: str) -> str | None:
    """A same-name remote replacement belongs to someone else; never delete it."""
    problem = local_cleanup_problem(root, branch, candidate)
    if problem:
        raise PromotionError(problem)
    remote = remote_branch(root, branch)
    if remote == candidate:
        result = run(root, "push", f"--force-with-lease=refs/heads/{branch}:{candidate}",
                      "origin", f":refs/heads/{branch}", check=False)
        remote = remote_branch(root, branch)
        if remote == candidate:
            raise PromotionError(f"could not delete {branch}: {result.stderr.strip()}")
    cleanup_local(root, branch, candidate)
    tracking = f"refs/remotes/origin/{branch}"
    if remote is None and rev_exists(root, tracking) and rev(root, tracking) == candidate:
        run(root, "update-ref", "-d", tracking, candidate)
    return remote


def promotion_refs(root: Path) -> list[tuple[str, str]]:
    listed = run(root, "ls-remote", "origin", "refs/heads/promotion/*", check=False)
    if listed.returncode != 0:
        detail = listed.stderr.strip() or listed.stdout.strip() or "no output"
        raise PromotionError(f"could not list promotion refs: {detail}")
    found = []
    for line in listed.stdout.splitlines():
        sha, ref = line.split()
        if ref.startswith("refs/heads/promotion/"):
            found.append((sha, ref))
    return found


def delete_remote_refs(root: Path, refs: Mapping[str, str]) -> None:
    """Delete one observed batch atomically, and verify absence after any transport result."""
    if not refs:
        return
    deleted = run(root, "push", "--atomic",
                  *(f"--force-with-lease={ref}:{sha}" for ref, sha in refs.items()),
                  "origin", *(f":{ref}" for ref in refs), check=False)
    remaining = {ref: sha for sha, ref in promotion_refs(root) if ref in refs}
    if remaining:
        detail = deleted.stderr.strip() or deleted.stdout.strip() or "no output"
        raise PromotionError(f"promotion cleanup not settled; retained refs {remaining}: {detail}")
