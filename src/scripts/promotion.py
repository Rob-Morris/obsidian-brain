#!/usr/bin/env python3
"""Promote one first-parent cut of dev onto main, then replay any linear tail.

Contributor-only. This does not ship in the Brain CLI. Git refs are the durable
state: the candidate commit carries Brain-Dev-Source and Brain-Dev-Tip, and
finish either fast-forwards both branches or leaves them untouched.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Callable, Mapping


REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_ROOT = REPO_ROOT / "cli"
SCRIPTS_ROOT = Path(__file__).resolve().parent
for _path in (CLI_ROOT, SCRIPTS_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import canary_receipt  # noqa: E402
import check_ci  # noqa: E402
import release  # noqa: E402
from _version_contract import SEMVER_PATTERN, SEMVER_RE, release_summary_problems  # noqa: E402


SOURCE_TRAILER = "Brain-Dev-Source"
TIP_TRAILER = "Brain-Dev-Tip"
ZERO_SHA = "0" * 40
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
PROMOTION_BRANCH_RE = re.compile(rf"^promotion/v{SEMVER_PATTERN}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class PromotionError(RuntimeError):
    """A promotion precondition failed. main and dev are unchanged."""


@dataclass(frozen=True)
class PromotionRequest:
    """One explicit version, narrative, and dev cut."""

    core_version: str
    summary: str
    release_type: str
    changes: tuple[str, ...]
    release_date: str
    body: str
    cli_version: str | None = None
    proxy_version: str | None = None
    cut: str | None = None


@dataclass(frozen=True)
class GitCommit:
    """The commit fields promotion topology needs."""

    sha: str
    tree: str
    parents: tuple[str, ...]
    message: str

    @property
    def subject(self) -> str:
        return self.message.splitlines()[0] if self.message else ""


@dataclass(frozen=True)
class PushUpdate:
    """One pre-push ref update. A missing endpoint is the all-zero SHA."""

    remote_ref: str
    remote_sha: str
    local_sha: str


@dataclass(frozen=True)
class RemoteHeads:
    """Origin's main and dev. ``observed`` is false only when the query failed."""

    main: str | None
    dev: str | None
    observed: bool


CheckRunner = Callable[[Path], None]
CiRunner = Callable[[Path, str, str], Mapping[str, object]]


def _run(
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


def _out(root: Path, *args: str) -> str:
    return _run(root, *args).stdout.strip()


def _rev(root: Path, ref: str) -> str:
    sha = _out(root, "rev-parse", "--verify", f"{ref}^{{commit}}")
    if not SHA_RE.fullmatch(sha):
        raise PromotionError(f"{ref} is not a commit")
    return sha


def read_commit(root: Path, sha: str) -> GitCommit:
    """Read one commit without checking it out."""
    raw = _run(root, "cat-file", "-p", sha).stdout
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


def parse_trailers(message: str) -> dict[str, str]:
    """Read the uninterrupted trailer block at the end of a commit message."""
    trailers: dict[str, str] = {}
    for line in reversed(message.strip().splitlines()):
        if not line.strip():
            break
        key, separator, value = line.partition(": ")
        if not separator or not key or " " in key:
            break
        trailers[key] = value
    return trailers


def version_tuple(version: str) -> tuple[int, int, int]:
    if not SEMVER_RE.fullmatch(version):
        raise PromotionError(f"invalid semantic version: {version!r}")
    return tuple(int(part) for part in version.split("."))  # type: ignore[return-value]


def load_request(payload: Mapping[str, object]) -> PromotionRequest:
    """Validate a promotion request. The caller chooses the version and the cut."""
    required = ("core_version", "summary", "release_type", "changes", "date", "body")
    missing = [key for key in required if key not in payload]
    if missing:
        raise PromotionError(f"promotion request is missing {', '.join(missing)}")
    core = payload["core_version"]
    summary = payload["summary"]
    release_type = payload["release_type"]
    changes = payload["changes"]
    release_date = payload["date"]
    body = payload["body"]
    if not isinstance(core, str) or not SEMVER_RE.fullmatch(core):
        raise PromotionError("core_version must be a semantic version")
    if not isinstance(summary, str) or not summary.strip():
        raise PromotionError("summary must be a non-empty string")
    summary = summary.strip()
    if release_summary_problems(summary):
        raise PromotionError("summary must omit a trailing period and any version suffix")
    if not isinstance(release_type, str) or not release_type.strip():
        raise PromotionError("release_type must be a non-empty string")
    if (
        not isinstance(changes, list)
        or not changes
        or any(not isinstance(item, str) or not item.strip() for item in changes)
    ):
        raise PromotionError("changes must be a non-empty list of strings")
    if not isinstance(release_date, str) or not DATE_RE.fullmatch(release_date):
        raise PromotionError("date must be YYYY-MM-DD")
    try:
        datetime.strptime(release_date, "%Y-%m-%d")
    except ValueError as exc:
        raise PromotionError("date must be a real YYYY-MM-DD") from exc
    if not isinstance(body, str) or not body.strip():
        raise PromotionError("body must be a non-empty string")
    if SOURCE_TRAILER in body or TIP_TRAILER in body:
        raise PromotionError("body must not contain promotion trailers")
    cut = payload.get("cut")
    if cut is not None and (not isinstance(cut, str) or not SHA_RE.fullmatch(cut)):
        raise PromotionError("cut must be a full SHA or omitted")
    cli_version = payload.get("cli_version")
    proxy_version = payload.get("proxy_version")
    for name, value in (("cli_version", cli_version), ("proxy_version", proxy_version)):
        if value is not None and (not isinstance(value, str) or not SEMVER_RE.fullmatch(value)):
            raise PromotionError(f"{name} must be a semantic version or null")
    return PromotionRequest(
        core,
        summary,
        release_type.strip(),
        tuple(changes),
        release_date,
        body if body.endswith("\n") else body + "\n",
        cli_version if isinstance(cli_version, str) else None,
        proxy_version if isinstance(proxy_version, str) else None,
        cut,
    )


def _release_identity(facts: object) -> tuple[object, ...]:
    return (
        facts.coherent,
        facts.core,
        facts.cli_unix,
        facts.cli_windows,
        facts.proxy,
        facts.install_ref_unix,
        facts.install_ref_windows,
        facts.changelog_head,
    )


def _require_same_release_facts(root: Path, *revs: str):
    facts = [release.release_facts(root, rev) for rev in revs]
    if any(not item.coherent for item in facts):
        raise PromotionError("release facts are incoherent at the cut, tip, or main")
    if len({_release_identity(item) for item in facts}) != 1:
        raise PromotionError(
            "Core, CLI, proxy, install-ref, and changelog-head facts at the cut "
            "and dev tip must still equal main"
        )
    return facts[0]


def _ancestry(root: Path, tip: str) -> list[str]:
    return _out(root, "rev-list", "--first-parent", tip).splitlines()


def _commits_after(root: Path, start: str, end: str) -> list[GitCommit]:
    if start == end:
        return []
    listed = _out(root, "rev-list", "--first-parent", "--reverse", f"{start}..{end}")
    return [read_commit(root, sha) for sha in listed.splitlines() if sha]


def _content_tail(root: Path, cut: str, tail: list[GitCommit]) -> list[GitCommit]:
    """Return content commits, skipping provenance commits whose tree matches their first parent."""
    previous = cut
    content: list[GitCommit] = []
    for commit in tail:
        if not commit.parents or commit.parents[0] != previous:
            raise PromotionError(f"{commit.sha} does not continue the first-parent tail from {previous}")
        provenance = (
            len(commit.parents) == 2 and commit.tree == _tree_of(root, commit.parents[0])
        )
        if len(commit.parents) == 1:
            content.append(commit)
        elif not provenance:
            raise PromotionError(
                f"{commit.sha} is not a single-parent commit; merge it before the cut "
                "or choose a later cut"
            )
        previous = commit.sha
    return content


def _resolve_cut(root: Path, dev: str, requested: str | None) -> tuple[str, list[GitCommit]]:
    ancestry = _ancestry(root, dev)
    cut = dev if requested is None else requested
    if cut not in ancestry:
        raise PromotionError(f"{cut} is not on the first-parent line of dev")
    tail = _commits_after(root, cut, dev)
    _content_tail(root, cut, tail)
    return cut, tail


def _candidate_message(request: PromotionRequest, cut: str, tip: str) -> str:
    return (
        f"{request.summary} (v{request.core_version})\n\n"
        f"{request.body.rstrip()}\n\n"
        f"{SOURCE_TRAILER}: {cut}\n"
        f"{TIP_TRAILER}: {tip}\n"
    )


def _identity_env(commit_date: str, name: str, email: str) -> dict[str, str]:
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


def _configured_identity(root: Path) -> tuple[str, str]:
    name = _out(root, "config", "user.name")
    email = _out(root, "config", "user.email")
    if not name or not email:
        raise PromotionError("git user.name and user.email must be configured")
    return name, email


def _commit_tree(
    root: Path,
    tree: str,
    parents: tuple[str, ...],
    message: str,
    env: Mapping[str, str],
) -> str:
    args = ["commit-tree", tree]
    for parent in parents:
        args.extend(["-p", parent])
    sha = _run(root, *args, input_text=message, env=env).stdout.strip()
    if not SHA_RE.fullmatch(sha):
        raise PromotionError("git commit-tree did not return a SHA")
    return sha


def _merge_tree(root: Path, base: str, ours: str, theirs: str) -> str:
    completed = _run(
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


def _tree_of(root: Path, rev: str) -> str:
    return _out(root, "rev-parse", f"{rev}^{{tree}}")


def replay_trees(root: Path, base_tree: str, content: list[GitCommit]) -> list[str]:
    """Return the replayed tree of each content commit, or refuse."""
    ours = base_tree
    trees: list[str] = []
    for commit in content:
        merged = _merge_tree(root, commit.parents[0], ours, commit.sha)
        if merged == ours:
            raise PromotionError(f"replaying {commit.sha} would drop that commit")
        trees.append(merged)
        ours = merged
    return trees


def _ref_label(sha: str | None) -> str:
    return "absent" if sha is None else sha


def build_replay(root: Path, candidate: GitCommit) -> str:
    """Create the dev tip finish should publish. Author identity comes from the candidate."""
    trailers = parse_trailers(candidate.message)
    cut = trailers.get(SOURCE_TRAILER)
    tip = trailers.get(TIP_TRAILER)
    if cut is None or tip is None or not SHA_RE.fullmatch(cut) or not SHA_RE.fullmatch(tip):
        raise PromotionError("candidate is missing Brain-Dev-Source or Brain-Dev-Tip")
    if len(candidate.parents) != 1:
        raise PromotionError("candidate must have exactly one parent, the previous main")
    tail = _commits_after(root, cut, tip)
    content = _content_tail(root, cut, tail)
    version = release.release_facts(root, candidate.sha).core
    if version is None:
        raise PromotionError("candidate tree has no Core version")
    name = _out(root, "log", "-1", "--format=%an", candidate.sha)
    email = _out(root, "log", "-1", "--format=%ae", candidate.sha)
    commit_date = _out(root, "log", "-1", "--format=%cI", candidate.sha)
    commit_env = _identity_env(commit_date, name, email)
    reconcile = f"WIP: reconcile dev with promoted v{version}\n"
    if not content:
        # No content commits: parent on the tip when provenance remains, else on the cut.
        second = tip if tail else cut
        return _commit_tree(root, candidate.tree, (candidate.sha, second), reconcile, commit_env)
    parent = _commit_tree(root, candidate.tree, (candidate.sha, cut), reconcile, commit_env)
    for commit, tree in zip(content, replay_trees(root, candidate.tree, content), strict=True):
        parent = _commit_tree(root, tree, (parent,), commit.message, commit_env)
    return _commit_tree(
        root,
        _tree_of(root, parent),
        (parent, tip),
        f"WIP: preserve dev history after v{version}\n",
        commit_env,
    )


def classify_remote(
    old_main: str,
    old_dev: str,
    new_main: str,
    new_dev: str,
    seen_main: str | None,
    seen_dev: str | None,
    *,
    observed: bool,
) -> str:
    """Classify an atomic push as unchanged, settled, diverged, or unobserved.

    ``observed`` is false only when listing origin failed. An absent ref was
    seen, and is diverged rather than unobserved.
    """
    if not observed:
        return "unobserved"
    if seen_main is None or seen_dev is None:
        return "diverged"
    if (seen_main, seen_dev) == (old_main, old_dev):
        return "unchanged"
    if (seen_main, seen_dev) == (new_main, new_dev):
        return "settled"
    return "diverged"


def assess_push(root: Path, updates: list[PushUpdate]) -> None:
    """Accept ordinary dev fast-forwards, or one exact promotion update. Refuse the rest."""
    interesting = [
        update
        for update in updates
        if update.remote_ref.startswith("refs/heads/")
    ]
    by_ref = {update.remote_ref: update for update in interesting}
    if len(by_ref) != len(interesting):
        raise PromotionError("a branch appears twice in one push")
    main_update = by_ref.get("refs/heads/main")
    if main_update is None:
        for update in interesting:
            _require_ordinary_update(root, update)
        return
    dev_update = by_ref.get("refs/heads/dev")
    if dev_update is None or set(by_ref) != {"refs/heads/main", "refs/heads/dev"}:
        raise PromotionError("main can move only together with its matching dev fast-forward")
    if main_update.local_sha == ZERO_SHA or dev_update.local_sha == ZERO_SHA:
        raise PromotionError("promotion cannot delete main or dev")
    candidate = read_commit(root, main_update.local_sha)
    if candidate.parents != (main_update.remote_sha,):
        raise PromotionError("main must fast-forward by exactly the candidate commit")
    trailers = parse_trailers(candidate.message)
    if trailers.get(TIP_TRAILER) != dev_update.remote_sha:
        raise PromotionError("dev update does not start at the candidate's Brain-Dev-Tip")
    expected_dev = build_replay(root, candidate)
    if dev_update.local_sha != expected_dev:
        raise PromotionError(
            f"dev update {dev_update.local_sha} is not the replayed tip {expected_dev}"
        )
    if not _is_ancestor(root, dev_update.remote_sha, dev_update.local_sha):
        raise PromotionError("dev promotion update is not a fast-forward")


def _require_ordinary_update(root: Path, update: PushUpdate) -> None:
    name = update.remote_ref.removeprefix("refs/heads/")
    if name.startswith("promotion/"):
        _require_candidate_update(update)
        return
    if update.local_sha == ZERO_SHA:
        return
    if update.remote_sha != ZERO_SHA and not _is_ancestor(root, update.remote_sha, update.local_sha):
        raise PromotionError(f"{name} update is not a fast-forward")


def _require_candidate_update(update: PushUpdate) -> None:
    if not PROMOTION_BRANCH_RE.fullmatch(update.remote_ref.removeprefix("refs/heads/")):
        raise PromotionError(f"unsupported promotion ref {update.remote_ref}")
    if update.local_sha == ZERO_SHA or update.local_sha == update.remote_sha:
        return
    if update.remote_sha != ZERO_SHA:
        raise PromotionError(f"{update.remote_ref} is immutable; discard and recreate it")


def _is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    if ancestor == descendant:
        return True
    completed = _run(root, "merge-base", "--is-ancestor", ancestor, descendant, check=False)
    if completed.returncode == 0:
        return True
    if completed.returncode == 1:
        return False
    detail = completed.stderr.strip() or completed.stdout.strip() or "no output"
    raise PromotionError(f"git merge-base --is-ancestor failed: {detail}")


def _worktrees(root: Path) -> list[tuple[str, str | None]]:
    raw = _run(root, "worktree", "list", "--porcelain").stdout
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


def _require_checkout(root: Path) -> None:
    completed = _run(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    if completed.returncode != 0 or completed.stdout.strip() != "dev":
        raise PromotionError("the primary checkout must be on dev")
    status = _run(root, "status", "--porcelain", "--untracked-files=all").stdout
    if status.strip():
        raise PromotionError("the dev checkout must be clean")
    counts = {"refs/heads/dev": 0, "refs/heads/main": 0}
    for _path, branch in _worktrees(root):
        if branch in counts:
            counts[branch] += 1
    if counts["refs/heads/dev"] > 1 or counts["refs/heads/main"]:
        raise PromotionError("dev may be checked out once, and main not at all")


def _lock(root: Path):
    common = Path(_out(root, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = root / common
    handle = (common / "promotion.lock").open("a", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise PromotionError("another promotion holds the local lock") from exc
    return handle


def _fetch_matching(root: Path) -> tuple[str, str]:
    _run(root, "fetch", "origin", "main", "dev")
    local_main = _rev(root, "refs/heads/main")
    local_dev = _rev(root, "refs/heads/dev")
    remote_main = _rev(root, "refs/remotes/origin/main")
    remote_dev = _rev(root, "refs/remotes/origin/dev")
    if local_main != remote_main or local_dev != remote_dev:
        raise PromotionError(
            "local main/dev differ from origin: "
            f"main {local_main} vs {remote_main}; dev {local_dev} vs {remote_dev}"
        )
    return local_main, local_dev


def _observe_remote(root: Path) -> RemoteHeads:
    completed = _run(root, "ls-remote", "origin", "refs/heads/main", "refs/heads/dev", check=False)
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
    )


def _worktree_dir(root: Path, branch: str) -> Path:
    slug = branch.removeprefix("promotion/")
    return root / ".worktrees" / slug


def _promotion_branch(version: str) -> str:
    branch = f"promotion/v{version}"
    if not PROMOTION_BRANCH_RE.fullmatch(branch):
        raise PromotionError(f"invalid promotion branch {branch}")
    return branch


def _cut_release(worktree: Path):
    """Load release preparation from the cut when that tree contains it."""
    path = worktree / "src" / "scripts" / "release.py"
    if not path.is_file():
        return release
    spec = importlib.util.spec_from_file_location("promotion_cut_release", path)
    if spec is None or spec.loader is None:
        raise PromotionError(f"cannot load release preparation from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "prepare_release"):
        return release
    return module


def _apply_release(worktree: Path, request: PromotionRequest) -> dict[str, str]:
    module = _cut_release(worktree)
    try:
        changes = module.prepare_release(
            worktree,
            core_version=request.core_version,
            summary=request.summary,
            release_date=request.release_date,
            release_type=request.release_type,
            changes=list(request.changes),
            cli_version=request.cli_version,
            proxy_version=request.proxy_version,
        )
        module.apply_release(worktree, changes)
    except module.ReleaseError as exc:
        raise PromotionError(str(exc)) from exc
    return changes


def _canary_paths(primary: Path) -> tuple[Path, Path]:
    return primary / ".canaries" / "pre-promotion.md", primary / ".canary--pre-promotion"


def _check_canary(primary: Path) -> None:
    definition, receipt = _canary_paths(primary)
    try:
        canary_receipt.check_files(definition, receipt)
    except canary_receipt.CanaryError as exc:
        raise PromotionError(str(exc)) from exc


def _consume_canary(primary: Path) -> None:
    _canary_paths(primary)[1].unlink(missing_ok=True)


def default_checks(worktree: Path) -> None:
    """Run the candidate tree's repository contracts, then the serial test suite."""
    checker = worktree / "src" / "scripts" / "check_repository_contracts.py"
    contract = subprocess.run([sys.executable, str(checker)], cwd=worktree)
    if contract.returncode != 0:
        raise PromotionError("candidate repository contracts failed")
    tests = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=worktree)
    if tests.returncode != 0:
        raise PromotionError("candidate tests failed")


def default_ci(root: Path, commit: str, branch: str) -> Mapping[str, object]:
    """Require the exact candidate SHA to have passed on its promotion branch."""
    url = _out(root, "remote", "get-url", "origin")
    match = re.search(r"github\.com[:/](?P<owner>[^/]+)/(?P<name>[^/]+?)(?:\.git)?$", url)
    if match is None:
        raise PromotionError(f"origin is not a GitHub repository: {url}")
    repo = f"{match.group('owner')}/{match.group('name')}"
    return check_ci.check(repo, commit, branch, "push", wait=False, timeout=30)


def _status_payload(root: Path) -> dict[str, object]:
    main = _rev(root, "refs/heads/main")
    dev = _rev(root, "refs/heads/dev")
    if not _is_ancestor(root, main, dev):
        raise PromotionError(f"main {main} is not an ancestor of dev {dev}")
    commits = _commits_after(root, main, dev)
    cuts = []
    for index, commit in enumerate([read_commit(root, main), *commits]):
        included = [item.sha for item in commits[:index]]
        excluded = [item.sha for item in commits[index:]]
        cuts.append({"cut": commit.sha, "included": included, "excluded": excluded})
    return {
        "main": main,
        "dev": dev,
        "commits": [{"sha": commit.sha, "subject": commit.subject} for commit in commits],
        "cuts": cuts,
    }


def status(root: Path, *, as_json: bool = False) -> dict[str, object]:
    """Show the first-parent cuts available on dev. This does not mutate refs."""
    payload = _status_payload(root)
    if as_json:
        print(json.dumps(payload, indent=2))
        return payload
    print(f"main {payload['main']}")
    print(f"dev  {payload['dev']}")
    commits = payload["commits"]
    if not commits:
        print("dev has no commits after main. The only cut is the current tip.")
        return payload
    print("commits after main, oldest first:")
    for commit in commits:
        print(f"  {commit['sha']} {commit['subject']}")
    print("The default cut is the dev tip. Name an earlier SHA to leave a tail.")
    return payload


def prepare(
    root: Path,
    request: PromotionRequest,
    *,
    run_checks: CheckRunner | None = default_checks,
) -> str:
    """Create and push one immutable candidate. Do not move main or dev."""
    lock = _lock(root)
    try:
        _require_checkout(root)
        main, dev = _fetch_matching(root)
        cut, tail = _resolve_cut(root, dev, request.cut)
        current = _require_same_release_facts(root, main, cut, dev)
        if not current.core or version_tuple(request.core_version) <= version_tuple(current.core):
            raise PromotionError(
                f"requested {request.core_version} must be greater than main {current.core}"
            )
        branch = _promotion_branch(request.core_version)
        worktree = _worktree_dir(root, branch)
        if worktree.exists():
            _run(root, "worktree", "remove", "--force", str(worktree))
        worktree.parent.mkdir(parents=True, exist_ok=True)
        _run(root, "worktree", "add", "--detach", str(worktree), main)
        try:
            _run(worktree, "read-tree", "--reset", "-u", cut)
            changes = _apply_release(worktree, request)
            _run(worktree, "add", "-A")
            tree = _out(worktree, "write-tree")
            names = {
                line
                for line in _out(root, "diff-tree", "--name-only", "-r", _tree_of(root, cut), tree).splitlines()
                if line
            }
            if names != set(changes):
                raise PromotionError(
                    "candidate tree differs from the cut by more than the release bundle: "
                    + ", ".join(sorted(names ^ set(changes)))
                )
            replay_trees(root, tree, _content_tail(root, cut, tail))
            _check_canary(root)
            if run_checks is not None:
                run_checks(worktree)
            name, email = _configured_identity(root)
            commit_date = (
                datetime.strptime(request.release_date, "%Y-%m-%d")
                .replace(tzinfo=timezone.utc)
                .isoformat()
            )
            candidate = _commit_tree(
                root,
                tree,
                (main,),
                _candidate_message(request, cut, dev),
                _identity_env(commit_date, name, email),
            )
        except Exception:
            if worktree.exists():
                _run(root, "worktree", "remove", "--force", str(worktree), check=False)
            raise
        existing = _run(root, "rev-parse", "--verify", f"refs/heads/{branch}", check=False)
        if existing.returncode == 0 and existing.stdout.strip() not in {"", candidate}:
            raise PromotionError(
                f"{branch} already exists at {existing.stdout.strip()}; discard it before recreating"
            )
        _run(root, "update-ref", f"refs/heads/{branch}", candidate, existing.stdout.strip() or ZERO_SHA)
        _run(root, "push", "origin", f"refs/heads/{branch}:refs/heads/{branch}")
        _consume_canary(root)
        print(f"prepared {branch} {candidate}")
        return candidate
    finally:
        lock.close()


def _rev_exists(root: Path, ref: str) -> bool:
    completed = _run(root, "rev-parse", "--verify", "--quiet", ref, check=False)
    if completed.returncode == 0:
        return True
    if completed.returncode == 1:
        return False
    detail = completed.stderr.strip() or completed.stdout.strip() or "no output"
    raise PromotionError(f"git rev-parse --verify failed: {detail}")


def _settle_local(root: Path, main: str, dev: str, old_main: str, old_dev: str) -> None:
    script = (
        f"update refs/heads/main {main} {old_main}\n"
        f"update refs/heads/dev {dev} {old_dev}\n"
    )
    detached = _run(root, "symbolic-ref", "--quiet", "HEAD", check=False).returncode != 0
    if not detached:
        _run(root, "switch", "--detach", old_dev)
    _run(root, "update-ref", "--stdin", input_text=script)
    _run(root, "switch", "--detach", dev)
    _run(root, "switch", "dev")


def _remote_branch(root: Path, branch: str) -> str | None:
    ref = f"refs/heads/{branch}"
    completed = _run(root, "ls-remote", "origin", ref, check=False)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no output"
        raise PromotionError(f"cannot observe origin {branch}: {detail}")
    for line in completed.stdout.splitlines():
        sha, found = line.split()
        if found == ref:
            return sha
    return None


def _cleanup(root: Path, branch: str) -> None:
    problems: list[str] = []
    worktree = _worktree_dir(root, branch)
    if worktree.exists():
        removed = _run(root, "worktree", "remove", "--force", str(worktree), check=False)
        if removed.returncode != 0:
            problems.append(removed.stderr.strip() or f"could not remove {worktree}")
    if _rev_exists(root, f"refs/heads/{branch}"):
        deleted = _run(root, "branch", "-D", branch, check=False)
        if deleted.returncode != 0:
            problems.append(deleted.stderr.strip() or f"could not delete local {branch}")
    _run(root, "push", "origin", f":refs/heads/{branch}", check=False)
    try:
        remote = _remote_branch(root, branch)
    except PromotionError as exc:
        problems.append(str(exc))
    else:
        if remote is not None:
            problems.append(f"{branch} is still present on origin")
    if problems:
        raise PromotionError("cleanup failed: " + "; ".join(problems))


def _remote_state(
    old_main: str,
    old_dev: str,
    new_main: str,
    new_dev: str,
    seen: RemoteHeads,
) -> str:
    return classify_remote(
        old_main,
        old_dev,
        new_main,
        new_dev,
        seen.main,
        seen.dev,
        observed=seen.observed,
    )


def _remote_description(seen: RemoteHeads, new_main: str, new_dev: str) -> str:
    return (
        f"main {_ref_label(seen.main)}, dev {_ref_label(seen.dev)}; "
        f"expected {new_main} and {new_dev}"
    )


def finish(
    root: Path,
    branch: str,
    *,
    ci: CiRunner = default_ci,
) -> str:
    """Fast-forward main to the candidate and dev to the replayed tip, atomically."""
    if not PROMOTION_BRANCH_RE.fullmatch(branch):
        raise PromotionError(f"{branch} is not a promotion branch")
    lock = _lock(root)
    try:
        candidate_sha = _rev(root, f"refs/heads/{branch}")
        candidate = read_commit(root, candidate_sha)
        new_dev = build_replay(root, candidate)
        old_main = candidate.parents[0]
        old_dev = parse_trailers(candidate.message)[TIP_TRAILER]
        seen = _observe_remote(root)
        state = _remote_state(old_main, old_dev, candidate.sha, new_dev, seen)
        if state == "diverged":
            raise PromotionError(
                "remote main/dev are neither unchanged nor the expected promotion: "
                + _remote_description(seen, candidate.sha, new_dev)
            )
        if state == "unobserved":
            raise PromotionError("cannot observe origin; local refs were left unchanged")
        if state == "unchanged":
            _require_checkout(root)
            local_main, local_dev = _fetch_matching(root)
            if local_main != old_main or local_dev != old_dev:
                raise PromotionError("local main/dev moved after the candidate was prepared")
            result = ci(root, candidate.sha, branch)
            if result.get("state") != "passed":
                raise PromotionError(f"candidate CI is {result.get('state')}, not passed")
            push = _run(
                root,
                "push",
                "--atomic",
                "origin",
                f"{candidate.sha}:refs/heads/main",
                f"{new_dev}:refs/heads/dev",
                check=False,
            )
            if push.returncode != 0:
                seen = _observe_remote(root)
                state = _remote_state(old_main, old_dev, candidate.sha, new_dev, seen)
                if state == "unchanged":
                    raise PromotionError(f"atomic push failed; origin is unchanged:\n{push.stderr.strip()}")
                if state != "settled":
                    raise PromotionError(
                        "atomic push outcome is uncertain: "
                        + _remote_description(seen, candidate.sha, new_dev)
                    )
        local_main = _rev(root, "refs/heads/main")
        local_dev = _rev(root, "refs/heads/dev")
        if (local_main, local_dev) == (old_main, old_dev):
            _settle_local(root, candidate.sha, new_dev, old_main, old_dev)
        elif (local_main, local_dev) != (candidate.sha, new_dev):
            raise PromotionError(
                "local main/dev match neither the old base nor the promoted result: "
                f"main {local_main}, dev {local_dev}"
            )
        else:
            completed = _run(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
            if completed.stdout.strip() != "dev":
                _run(root, "switch", "dev")
        try:
            _cleanup(root, branch)
        except PromotionError as exc:
            raise PromotionError(
                f"promoted {branch} -> main {candidate.sha}; dev {new_dev}; {exc}"
            ) from exc
        print(f"promoted {branch} -> main {candidate.sha}; dev {new_dev}")
        return new_dev
    finally:
        lock.close()


def discard(root: Path, branch: str) -> None:
    """Delete one unpromoted candidate. Do not move main or dev."""
    if not PROMOTION_BRANCH_RE.fullmatch(branch):
        raise PromotionError(f"{branch} is not a promotion branch")
    lock = _lock(root)
    try:
        main_before = _rev(root, "refs/heads/main")
        dev_before = _rev(root, "refs/heads/dev")
        _cleanup(root, branch)
        if _rev(root, "refs/heads/main") != main_before or _rev(root, "refs/heads/dev") != dev_before:
            raise PromotionError("discard moved main or dev")
        print(f"discarded {branch}")
    finally:
        lock.close()


def pre_push(root: Path, lines: list[str]) -> None:
    """Validate ref updates from a pre-push hook. Stdin lines are local/remote SHAs."""
    updates: list[PushUpdate] = []
    for line in lines:
        if not line.strip():
            continue
        local_ref, local_sha, remote_ref, remote_sha = line.split()
        del local_ref
        updates.append(PushUpdate(remote_ref, remote_sha, local_sha))
    assess_push(root, updates)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=REPO_ROOT)
    sub = parser.add_subparsers(dest="command", required=True)
    status_parser = sub.add_parser("status", help="list first-parent cuts on dev")
    status_parser.add_argument("--json", action="store_true")
    prepare_parser = sub.add_parser("prepare", help="build and push one candidate")
    prepare_parser.add_argument("--input", type=Path, required=True)
    finish_parser = sub.add_parser("finish", help="publish one candidate to main and dev")
    finish_parser.add_argument("branch")
    discard_parser = sub.add_parser("discard", help="delete one unpromoted candidate")
    discard_parser.add_argument("branch")
    sub.add_parser("pre-push", help="validate ref updates on stdin")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    root = args.repo.resolve()
    try:
        if args.command == "status":
            status(root, as_json=args.json)
        elif args.command == "prepare":
            payload = json.loads(args.input.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise PromotionError("promotion request must be a JSON object")
            prepare(root, load_request(payload))
        elif args.command == "finish":
            finish(root, args.branch)
        elif args.command == "discard":
            discard(root, args.branch)
        elif args.command == "pre-push":
            pre_push(root, sys.stdin.read().splitlines())
        else:
            parser.error(f"unknown command {args.command}")
    except (OSError, PromotionError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(f"promotion: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
