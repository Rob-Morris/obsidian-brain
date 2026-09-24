"""Candidate identity, ledger topology and deterministic development replay."""
from __future__ import annotations

from pathlib import Path
import release
from . import git
from .model import (
    PromotionError, GitCommit, PushUpdate, parse_trailers, version_tuple, promotion_branch, SOURCE_TRAILER, TIP_TRAILER, ZERO_SHA, SHA_RE, PROMOTION_BRANCH_RE
)

def release_identity(facts: object) -> tuple[object, ...]:
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


def require_same_release_facts(root: Path, *revs: str):
    facts = [release.release_facts(root, rev) for rev in revs]
    if any(not item.coherent for item in facts):
        raise PromotionError("release facts are incoherent at the cut, tip, or ledger")
    if len({release_identity(item) for item in facts}) != 1:
        raise PromotionError(
            "Core, CLI, proxy, install-ref, and changelog-head facts at the cut "
            "and dev tip must still equal the ledger"
        )
    return facts[0]


def content_tail(root: Path, cut: str, tail: list[GitCommit]) -> list[GitCommit]:
    """Return content commits, skipping provenance commits whose tree matches their first parent."""
    previous = cut
    content: list[GitCommit] = []
    for commit in tail:
        if not commit.parents or commit.parents[0] != previous:
            raise PromotionError(f"{commit.sha} does not continue the first-parent tail from {previous}")
        provenance = (
            len(commit.parents) == 2 and commit.tree == git.tree_of(root, commit.parents[0])
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


def resolve_cut(root: Path, dev: str, requested: str | None) -> tuple[str, list[GitCommit]]:
    ancestry = git.ancestry(root, dev)
    cut = dev if requested is None else requested
    if cut not in ancestry:
        raise PromotionError(f"{cut} is not on the first-parent line of dev")
    tail = git.commits_after(root, cut, dev)
    content_tail(root, cut, tail)
    return cut, tail


def replay_trees(root: Path, base_tree: str, content: list[GitCommit]) -> list[str]:
    """Return the replayed tree of each content commit, or refuse."""
    ours = base_tree
    trees: list[str] = []
    for commit in content:
        merged = git.merge_tree(root, commit.parents[0], ours, commit.sha)
        if merged == ours:
            raise PromotionError(f"replaying {commit.sha} would drop that commit")
        trees.append(merged)
        ours = merged
    return trees


def build_replay(root: Path, candidate: GitCommit) -> str:
    """Create the dev tip finish should publish. Author identity comes from the candidate."""
    trailers = parse_trailers(candidate.message)
    cut = trailers.get(SOURCE_TRAILER)
    tip = trailers.get(TIP_TRAILER)
    if cut is None or tip is None or not SHA_RE.fullmatch(cut) or not SHA_RE.fullmatch(tip):
        raise PromotionError("candidate is missing Brain-Dev-Source or Brain-Dev-Tip")
    if len(candidate.parents) != 1:
        raise PromotionError("candidate must have exactly one parent, the ledger tip")
    tail = git.commits_after(root, cut, tip)
    content = content_tail(root, cut, tail)
    version = release.release_facts(root, candidate.sha).core
    if version is None:
        raise PromotionError("candidate tree has no Core version")
    name = git.out(root, "log", "-1", "--format=%an", candidate.sha)
    email = git.out(root, "log", "-1", "--format=%ae", candidate.sha)
    commit_date = git.out(root, "log", "-1", "--format=%cI", candidate.sha)
    commit_env = git.identity_env(commit_date, name, email)
    reconcile = f"WIP: reconcile dev with promoted v{version}\n"
    if not content:
        # No content commits: parent on the tip when provenance remains, else on the cut.
        second = tip if tail else cut
        return git.commit_tree(root, candidate.tree, (candidate.sha, second), reconcile, commit_env)
    parent = git.commit_tree(root, candidate.tree, (candidate.sha, cut), reconcile, commit_env)
    for commit, tree in zip(content, replay_trees(root, candidate.tree, content), strict=True):
        parent = git.commit_tree(root, tree, (parent,), commit.message, git.replay_env(root, commit.sha))
    return git.commit_tree(
        root,
        git.tree_of(root, parent),
        (parent, tip),
        f"WIP: preserve dev history after v{version}\n",
        commit_env,
    )


def assess_push(root: Path, updates: list[PushUpdate]) -> None:
    """Accept ordinary fast-forwards, one unreleased/dev finish, or a main publish."""
    from . import recovery_workflow
    if recovery_workflow.assess_push(root, updates):
        return
    interesting = [
        update
        for update in updates
        if update.remote_ref.startswith("refs/heads/")
    ]
    by_ref = {update.remote_ref: update for update in interesting}
    if len(by_ref) != len(interesting):
        raise PromotionError("a branch appears twice in one push")
    unreleased = by_ref.get("refs/heads/unreleased")
    main_update = by_ref.get("refs/heads/main")
    dev_update = by_ref.get("refs/heads/dev")
    if unreleased is not None:
        if main_update is not None or dev_update is None or set(by_ref) != {
            "refs/heads/unreleased",
            "refs/heads/dev",
        }:
            raise PromotionError("unreleased can move only together with its replayed dev")
        require_unreleased_pair(root, unreleased, dev_update)
        return
    if main_update is not None:
        if set(by_ref) != {"refs/heads/main"}:
            raise PromotionError("main is published on its own")
        require_main_publish(root, main_update)
        return
    for update in interesting:
        require_ordinary_update(root, update)


def on_first_parent_line(root: Path, tip: str, commit: str) -> bool:
    return commit in git.ancestry(root, tip)


def require_version_commit(commit: GitCommit) -> None:
    trailers = parse_trailers(commit.message)
    if len(commit.parents) != 1 or SOURCE_TRAILER not in trailers or TIP_TRAILER not in trailers:
        raise PromotionError(f"{commit.sha} is not a version commit")


def validate_candidate(root: Path, candidate: GitCommit, branch: str | None = None) -> str:
    """Bind the ref, release subject, source tree and replay range to one candidate."""
    require_version_commit(candidate)
    trailers = parse_trailers(candidate.message)
    cut, tip = trailers[SOURCE_TRAILER], trailers[TIP_TRAILER]
    if not SHA_RE.fullmatch(cut) or not SHA_RE.fullmatch(tip):
        raise PromotionError("candidate trailers must contain full commit SHAs")
    ledger = candidate.parents[0]
    if not on_first_parent_line(root, tip, cut) or not on_first_parent_line(root, cut, ledger):
        raise PromotionError("candidate source must follow its ledger on the dev first-parent line")
    previous = require_same_release_facts(root, ledger, cut, tip)
    facts = release.release_facts(root, candidate.sha)
    if not facts.coherent or not facts.core or not previous.core:
        raise PromotionError("candidate release facts are incoherent")
    expected_branch = promotion_branch(facts.core)
    if branch is not None and branch != expected_branch:
        raise PromotionError(f"candidate version requires {expected_branch}, not {branch}")
    if version_tuple(facts.core) <= version_tuple(previous.core):
        raise PromotionError("candidate version must be greater than its ledger parent")
    entry = git.run(root, "show", f"{candidate.sha}:docs/changelog/v{facts.core}.md").stdout
    summary = release.release_summary(entry, facts.core)
    if not summary or candidate.subject != f"{summary} (v{facts.core})":
        raise PromotionError("candidate subject must match its canonical release Summary")
    allowed = {
        release.VERSION_PATH, release.README_PATH, release.UNIX_CLI_PATH,
        release.WINDOWS_CLI_PATH, release.PROXY_PATH, release.CHANGELOG_INDEX_PATH,
        release.FUNCTIONAL_CLI_PATH, release.USER_REFERENCE_PATH,
        release.COMMAND_CATALOGUE_PATH, f"docs/changelog/v{facts.core}.md",
    }
    changed = set(git.out(root, "diff-tree", "--no-commit-id", "--name-only", "-r", cut, candidate.sha).splitlines())
    if changed - allowed:
        raise PromotionError("candidate changes files outside the release bundle: " + ", ".join(sorted(changed - allowed)))
    content_tail(root, cut, git.commits_after(root, cut, tip))
    return expected_branch


def require_unreleased_pair(root: Path, unreleased: PushUpdate, dev_update: PushUpdate) -> None:
    if unreleased.local_sha == ZERO_SHA or dev_update.local_sha == ZERO_SHA:
        raise PromotionError("finish cannot delete unreleased or dev")
    candidate = git.read_commit(root, unreleased.local_sha)
    validate_candidate(root, candidate)
    parent = candidate.parents[0]
    if unreleased.remote_sha == ZERO_SHA:
        if parent != git.rev(root, "refs/remotes/origin/main"):
            raise PromotionError("new unreleased commit must be parented on origin/main")
    elif parent != unreleased.remote_sha:
        raise PromotionError("unreleased must fast-forward by exactly one version commit")
    if parse_trailers(candidate.message).get(TIP_TRAILER) != dev_update.remote_sha:
        raise PromotionError("dev update does not start at the candidate's Brain-Dev-Tip")
    expected_dev = build_replay(root, candidate)
    if dev_update.local_sha != expected_dev:
        raise PromotionError(
            f"dev update {dev_update.local_sha} is not the replayed tip {expected_dev}"
        )
    if not git.is_ancestor(root, dev_update.remote_sha, dev_update.local_sha):
        raise PromotionError("dev promotion update is not a fast-forward")


def require_main_publish(root: Path, update: PushUpdate) -> None:
    if update.local_sha == ZERO_SHA:
        raise PromotionError("publish cannot delete main")
    if update.remote_sha != ZERO_SHA and not git.is_ancestor(root, update.remote_sha, update.local_sha):
        raise PromotionError("main publish is not a fast-forward")
    if not git.rev_exists(root, "refs/remotes/origin/unreleased"):
        raise PromotionError("main can move only to a commit already on unreleased")
    tip = git.rev(root, "refs/remotes/origin/unreleased")
    if not on_first_parent_line(root, tip, update.local_sha):
        raise PromotionError("main can move only to a commit already on unreleased")


def require_ordinary_update(root: Path, update: PushUpdate) -> None:
    name = update.remote_ref.removeprefix("refs/heads/")
    if name.startswith("promotion/"):
        require_candidate_update(root, update)
        return
    if update.local_sha == ZERO_SHA:
        if name == "dev":
            raise PromotionError("cannot delete persistent dev")
        return
    if update.remote_sha != ZERO_SHA and not git.is_ancestor(root, update.remote_sha, update.local_sha):
        raise PromotionError(f"{name} update is not a fast-forward")


def require_candidate_update(root: Path, update: PushUpdate) -> None:
    if not PROMOTION_BRANCH_RE.fullmatch(update.remote_ref.removeprefix("refs/heads/")):
        raise PromotionError(f"unsupported promotion ref {update.remote_ref}")
    if update.local_sha == ZERO_SHA or update.local_sha == update.remote_sha:
        return
    if update.remote_sha != ZERO_SHA:
        raise PromotionError(f"{update.remote_ref} is immutable; discard and recreate it")
    validate_candidate(root, git.read_commit(root, update.local_sha), update.remote_ref.removeprefix("refs/heads/"))


def ledger_tip(root: Path) -> str:
    main = git.rev(root, "refs/remotes/origin/main")
    if git.rev_exists(root, "refs/remotes/origin/unreleased"):
        ledger = git.rev(root, "refs/remotes/origin/unreleased")
        if not on_first_parent_line(root, ledger, main):
            raise PromotionError(
                f"origin/main {main} is outside unreleased {ledger}; reconcile the direct-main "
                "change into the ledger and dev before preparing another candidate"
            )
        return ledger
    return main
