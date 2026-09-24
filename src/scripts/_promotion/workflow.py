"""Prepare, finish, adopt and publish orchestration over Git and CI evidence."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from typing import Callable, Mapping
import canary_receipt
import check_ci
from . import git, candidates
from .recovery_model import github_repository
from .model import (
    PromotionError, PromotionRequest, PushUpdate, RemoteHeads, parse_trailers, version_tuple, candidate_message, ref_label, classify_remote, promotion_branch, TIP_TRAILER, ZERO_SHA, SHA_RE, PROMOTION_BRANCH_RE
)

CheckRunner = Callable[[Path], None]
CiRunner = Callable[[Path, str, str], Mapping[str, object]]

def fetch_matching(root: Path) -> tuple[str, str]:
    """Return the shared ledger tip and dev. Local dev must equal origin."""
    git.fetch_refs(root)
    local_dev = git.rev(root, "refs/heads/dev")
    remote_dev = git.rev(root, "refs/remotes/origin/dev")
    if local_dev != remote_dev:
        raise PromotionError(f"local dev differs from origin: {local_dev} vs {remote_dev}")
    return candidates.ledger_tip(root), local_dev


def apply_release(worktree: Path, request: PromotionRequest) -> set[str]:
    """Execute the cut's release API with its own import closure in a fresh process."""
    if not (worktree / "src/scripts/release.py").is_file():
        raise PromotionError("selected cut does not contain src/scripts/release.py")
    source = """
import json, sys
from pathlib import Path
root = Path.cwd()
sys.path.insert(0, str(root / 'src/scripts'))
import release
changes = release.prepare_release(root, **json.load(sys.stdin))
release.apply_release(root, changes)
print(json.dumps(sorted(changes)))
"""
    payload = {
        "core_version": request.core_version, "summary": request.summary,
        "release_date": request.release_date, "release_type": request.release_type,
        "changes": list(request.changes), "cli_version": request.cli_version,
        "proxy_version": request.proxy_version,
    }
    completed = subprocess.run(
        [sys.executable, "-I", "-c", source], cwd=worktree,
        input=json.dumps(payload), text=True, capture_output=True, check=False,
    )
    if completed.returncode:
        raise PromotionError("cut release preparation failed: " + completed.stderr.strip())
    try:
        changes = json.loads(completed.stdout)
    except ValueError as exc:
        raise PromotionError("cut release preparation returned invalid JSON") from exc
    if not isinstance(changes, list) or not all(isinstance(path, str) for path in changes):
        raise PromotionError("cut release preparation did not return changed paths")
    return set(changes)


def canary_paths(primary: Path) -> tuple[Path, Path]:
    return primary / ".canaries" / "pre-promotion.md", primary / ".canary--pre-promotion"


def check_canary(primary: Path) -> None:
    definition, receipt = canary_paths(primary)
    try:
        canary_receipt.check_files(definition, receipt)
    except canary_receipt.CanaryError as exc:
        raise PromotionError(str(exc)) from exc


def consume_canary(primary: Path) -> None:
    canary_paths(primary)[1].unlink(missing_ok=True)


def default_checks(worktree: Path) -> None:
    """Run the candidate tree's repository contracts, then the serial test suite."""
    # make test creates this link. It is gitignored, so a promotion worktree does not have it.
    catalogue_link = worktree / "template-vault" / ".brain-core"
    if not catalogue_link.exists():
        catalogue_link.symlink_to(Path("..") / "src" / "brain-core")
    checker = worktree / "src" / "scripts" / "check_repository_contracts.py"
    contract = subprocess.run([sys.executable, str(checker)], cwd=worktree)
    if contract.returncode != 0:
        raise PromotionError("candidate repository contracts failed")
    tests = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=worktree)
    if tests.returncode != 0:
        raise PromotionError("candidate tests failed")


def default_ci(root: Path, commit: str, branch: str) -> Mapping[str, object]:
    """Require the exact candidate SHA to have passed on its promotion branch."""
    url = git.out(root, "remote", "get-url", "origin")
    repo = github_repository(url)
    if repo is None:
        raise PromotionError("origin is not a GitHub repository")
    return check_ci.check(repo, commit, branch, "push", wait=False, timeout=30)


def status_payload(root: Path) -> dict[str, object]:
    git.fetch_refs(root)
    ledger = candidates.ledger_tip(root)
    dev = git.rev(root, "refs/heads/dev")
    if not candidates.on_first_parent_line(root, dev, ledger):
        raise PromotionError(f"ledger {ledger} is not on the first-parent line of dev {dev}")
    commits = git.commits_after(root, ledger, dev)
    cuts = []
    for index, commit in enumerate([git.read_commit(root, ledger), *commits]):
        included = [item.sha for item in commits[:index]]
        excluded = [item.sha for item in commits[index:]]
        cuts.append({"cut": commit.sha, "included": included, "excluded": excluded})
    return {
        "ledger": ledger,
        "dev": dev,
        "commits": [{"sha": commit.sha, "subject": commit.subject} for commit in commits],
        "cuts": cuts,
    }


def status(root: Path, *, as_json: bool = False) -> dict[str, object]:
    """Show the first-parent cuts available on dev. This does not mutate refs."""
    payload = status_payload(root)
    if as_json:
        print(json.dumps(payload, indent=2))
        return payload
    print(f"ledger {payload['ledger']}")
    print(f"dev  {payload['dev']}")
    commits = payload["commits"]
    if not commits:
        print("dev has no commits after the ledger. The only cut is the current tip.")
        return payload
    print("commits after the ledger, oldest first:")
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
    lock = git.lock(root)
    try:
        git.require_checkout(root)
        ledger, dev = fetch_matching(root)
        cut, tail = candidates.resolve_cut(root, dev, request.cut)
        current = candidates.require_same_release_facts(root, ledger, cut, dev)
        if not current.core or version_tuple(request.core_version) <= version_tuple(current.core):
            raise PromotionError(
                f"requested {request.core_version} must be greater than ledger {current.core}"
            )
        branch = promotion_branch(request.core_version)
        worktree = git.worktree_dir(root, branch)
        if worktree.exists():
            git.run(root, "worktree", "remove", str(worktree))
        worktree.parent.mkdir(parents=True, exist_ok=True)
        git.run(root, "worktree", "add", "--detach", str(worktree), ledger)
        try:
            git.run(worktree, "read-tree", "--reset", "-u", cut)
            changes = apply_release(worktree, request)
            git.run(worktree, "add", "--", *sorted(changes))
            tree = git.out(worktree, "write-tree")
            names = {
                line
                for line in git.out(root, "diff-tree", "--name-only", "-r", git.tree_of(root, cut), tree).splitlines()
                if line
            }
            if names != set(changes):
                raise PromotionError(
                    "candidate tree differs from the cut by more than the release bundle: "
                    + ", ".join(sorted(names ^ set(changes)))
                )
            candidates.replay_trees(root, tree, candidates.content_tail(root, cut, tail))
            check_canary(root)
            name, email = git.configured_identity(root)
            commit_date = (
                datetime.strptime(request.release_date, "%Y-%m-%d")
                .replace(tzinfo=timezone.utc)
                .isoformat()
            )
            candidate = git.commit_tree(
                root,
                tree,
                (ledger,),
                candidate_message(request, cut, dev),
                git.identity_env(commit_date, name, email),
            )
            candidates.validate_candidate(root, git.read_commit(root, candidate), branch)
            git.run(worktree, "reset", "--hard", candidate)
            if run_checks is not None:
                run_checks(worktree)
            if git.run(worktree, "status", "--porcelain", "--untracked-files=all").stdout.strip():
                raise PromotionError("candidate checks changed the prepared worktree")
        except Exception:
            if worktree.exists():
                git.run(root, "worktree", "remove", "--force", str(worktree), check=False)
            raise
        existing = git.run(root, "rev-parse", "--verify", f"refs/heads/{branch}", check=False)
        if existing.returncode == 0 and existing.stdout.strip() not in {"", candidate}:
            raise PromotionError(
                f"{branch} already exists at {existing.stdout.strip()}; discard it before recreating"
            )
        git.run(root, "update-ref", f"refs/heads/{branch}", candidate, existing.stdout.strip() or ZERO_SHA)
        remote = git.remote_branch(root, branch)
        if remote not in {None, candidate}:
            raise PromotionError(f"{branch} is immutable at origin {remote}; discard the local candidate")
        if remote is None:
            git.run(root, "push", f"--force-with-lease=refs/heads/{branch}:", "origin", f"{candidate}:refs/heads/{branch}")
        consume_canary(root)
        print(f"prepared {branch} {candidate}")
        return candidate
    finally:
        lock.close()


def seen_unreleased(seen: RemoteHeads) -> str:
    return seen.unreleased if seen.unreleased is not None else ZERO_SHA


def settle_finish(root: Path, candidate: str, new_dev: str, old_dev: str) -> None:
    git.require_checkout(root)
    git.run(root, "update-ref", "refs/remotes/origin/unreleased", candidate)
    local_dev = git.rev(root, "refs/heads/dev")
    if local_dev == new_dev:
        return
    if local_dev != old_dev:
        raise PromotionError(
            f"local dev is {local_dev}, neither {old_dev} nor the replay {new_dev}; "
            "origin already has the cut"
        )
    git.run(root, "reset", "--keep", new_dev)


def finish(
    root: Path,
    branch: str,
    *,
    ci: CiRunner = default_ci,
) -> str:
    """Fast-forward unreleased to the candidate and dev to the replay. Do not push main."""
    if not PROMOTION_BRANCH_RE.fullmatch(branch):
        raise PromotionError(f"{branch} is not a promotion branch")
    lock = git.lock(root)
    try:
        if not git.rev_exists(root, f"refs/heads/{branch}"):
            git.run(root, "fetch", "origin", f"refs/heads/{branch}:refs/remotes/origin/{branch}")
            candidate_sha = git.rev(root, f"refs/remotes/origin/{branch}")
        else:
            candidate_sha = git.rev(root, f"refs/heads/{branch}")
        candidate = git.read_commit(root, candidate_sha)
        candidates.validate_candidate(root, candidate, branch)
        new_dev = candidates.build_replay(root, candidate)
        old_ledger = candidate.parents[0]
        old_dev = parse_trailers(candidate.message)[TIP_TRAILER]
        seen = git.observe_remote(root)
        old_unreleased = seen_unreleased(seen)
        state = classify_remote(
            old_unreleased,
            old_dev,
            candidate.sha,
            new_dev,
            seen_unreleased(seen),
            seen.dev,
            observed=seen.observed,
        )
        if state == "diverged":
            raise PromotionError(
                "remote unreleased/dev are neither unchanged nor the expected cut: "
                f"unreleased {ref_label(seen.unreleased)}, dev {ref_label(seen.dev)}; "
                f"expected {candidate.sha} and {new_dev}"
            )
        if state == "unobserved":
            raise PromotionError("cannot observe origin; local refs were left unchanged")
        if state == "unchanged":
            git.require_checkout(root)
            ledger, local_dev = fetch_matching(root)
            if ledger != old_ledger or local_dev != old_dev:
                raise PromotionError("ledger or dev moved after the candidate was prepared")
            if old_unreleased not in {ZERO_SHA, old_ledger}:
                raise PromotionError("unreleased is neither absent nor the candidate parent")
            result = ci(root, candidate_sha, branch)
            if result.get("state") != "passed":
                raise PromotionError(f"candidate CI is {result.get('state')}, not passed")
            remote_candidate = git.remote_branch(root, branch)
            if remote_candidate != candidate_sha:
                raise PromotionError(f"origin {branch} is {ref_label(remote_candidate)}, expected {candidate_sha}")
            observed_main = git.remote_branch(root, "main")
            if observed_main not in git.ancestry(root, old_ledger):
                raise PromotionError(
                    f"origin/main changed to {ref_label(observed_main)} outside the ledger; "
                    "reconcile the direct-main change before finishing"
                )
            push = git.run(
                root,
                "push",
                "--atomic",
                f"--force-with-lease=refs/heads/unreleased:{'' if old_unreleased == ZERO_SHA else old_unreleased}",
                f"--force-with-lease=refs/heads/dev:{old_dev}",
                "origin",
                f"{candidate_sha}:refs/heads/unreleased",
                f"{new_dev}:refs/heads/dev",
                check=False,
            )
            if push.returncode != 0:
                seen = git.observe_remote(root)
                state = classify_remote(
                    old_unreleased,
                    old_dev,
                    candidate.sha,
                    new_dev,
                    seen_unreleased(seen),
                    seen.dev,
                    observed=seen.observed,
                )
                if state == "unchanged":
                    raise PromotionError(f"atomic push failed; origin is unchanged:\n{push.stderr.strip()}")
                if state != "settled":
                    raise PromotionError(
                        "atomic push outcome is uncertain: "
                        f"unreleased {ref_label(seen.unreleased)}, dev {ref_label(seen.dev)}; "
                        f"expected {candidate.sha} and {new_dev}"
                    )
        settle_finish(root, candidate.sha, new_dev, old_dev)
        observed_main = git.remote_branch(root, "main")
        if observed_main not in git.ancestry(root, candidate.sha):
            raise PromotionError(
                f"recorded {candidate.sha} on unreleased and dev {new_dev}, but origin/main "
                f"is now {ref_label(observed_main)} outside the ledger; reconciliation is required"
            )
        try:
            git.cleanup_local(root, branch)
        except PromotionError as exc:
            raise PromotionError(
                f"recorded {branch} on unreleased {candidate.sha}; dev {new_dev}; {exc}"
            ) from exc
        print(f"recorded {branch} on unreleased {candidate.sha}; dev {new_dev}")
        return new_dev
    finally:
        lock.close()


def discard(root: Path, branch: str) -> None:
    """Delete one unpromoted candidate. Do not move main or dev."""
    if not PROMOTION_BRANCH_RE.fullmatch(branch):
        raise PromotionError(f"{branch} is not a promotion branch")
    lock = git.lock(root)
    try:
        if git.rev_exists(root, f"refs/heads/{branch}"):
            local_sha = git.rev(root, f"refs/heads/{branch}")
            git.fetch_refs(root)
            if git.is_ancestor(root, local_sha, candidates.ledger_tip(root)):
                raise PromotionError("candidate is already on the ledger; publish owns its remote cleanup")
            git.discard_owned(root, branch, local_sha)
        else:
            git.cleanup_local(root, branch)
        print(f"discarded {branch}")
    finally:
        lock.close()


def publish(root: Path, sha: str, *, ci: CiRunner = default_ci) -> str:
    """Fast-forward origin/main to a version commit already on unreleased."""
    if not SHA_RE.fullmatch(sha):
        raise PromotionError("publish requires a full commit SHA")
    lock = git.lock(root)
    try:
        if any(branch == "refs/heads/main" for _path, branch in git.worktrees(root)):
            raise PromotionError("main is checked out in a worktree; switch it away before publishing")
        git.fetch_refs(root)
        if not git.rev_exists(root, "refs/remotes/origin/unreleased"):
            raise PromotionError("main can move only to a commit already on unreleased")
        ledger = git.rev(root, "refs/remotes/origin/unreleased")
        origin_main = git.rev(root, "refs/remotes/origin/main")
        if sha == origin_main:
            delete_published_candidates(root, sha)
            print(f"already published {sha}")
            return sha
        if not git.is_ancestor(root, origin_main, sha) or not candidates.on_first_parent_line(root, ledger, sha):
            raise PromotionError(f"{sha} is not a later commit on the unreleased first-parent line")
        branch = candidates.validate_candidate(root, git.read_commit(root, sha))
        result = ci(root, sha, branch)
        if result.get("state") != "passed":
            raise PromotionError(f"candidate CI is {result.get('state')}, not passed")
        git.run(root, "push", f"--force-with-lease=refs/heads/main:{origin_main}", "origin", f"{sha}:refs/heads/main")
        git.run(root, "update-ref", "refs/remotes/origin/main", sha)
        if git.rev_exists(root, "refs/heads/main") and git.rev(root, "refs/heads/main") == origin_main:
            git.run(root, "update-ref", "refs/heads/main", sha, origin_main)
        delete_published_candidates(root, sha)
        print(f"published {sha}")
        return sha
    finally:
        lock.close()


def delete_published_candidates(root: Path, main_sha: str) -> None:
    """Delete promotion refs whose commits are contained in the published main."""
    listed = git.promotion_refs(root)
    # Membership needs only main's reachable objects, not foreign sibling candidates.
    published = set(git.out(root, "rev-list", main_sha).splitlines())
    owned = [(sha, ref) for sha, ref in listed if sha in published]
    refs = [ref for _sha, ref in owned]
    if not refs:
        return
    deleted = git.run(
        root,
        "push",
        "--atomic",
        *(f"--force-with-lease={ref}:{sha}" for sha, ref in owned),
        "origin",
        *(f":{ref}" for ref in refs),
        check=False,
    )
    if deleted.returncode != 0:
        detail = deleted.stderr.strip() or deleted.stdout.strip() or "no output"
        names = ", ".join(ref.removeprefix("refs/heads/") for ref in refs)
        raise PromotionError(f"could not delete published promotion refs {names}: {detail}")
    still = [ref for _sha, ref in git.promotion_refs(root) if ref in set(refs)]
    if still:
        names = ", ".join(ref.removeprefix("refs/heads/") for ref in still)
        raise PromotionError(f"promotion refs still present: {names}")


def adopt(root: Path, branch: str | None = None) -> str:
    """Align dev with the winner and replay only this checkout's extra commits."""
    lock = git.lock(root)
    try:
        git.require_checkout(root)
        from . import recovery_workflow
        recovery_workflow.fetch_for_adoption(root)
        git.fetch_refs(root)
        ledger = candidates.ledger_tip(root)
        previous = git.rev(root, "refs/heads/dev")
        origin_dev = git.rev(root, "refs/remotes/origin/dev")
        candidate = None
        if branch is not None:
            if not PROMOTION_BRANCH_RE.fullmatch(branch):
                raise PromotionError(f"{branch} is not a promotion branch")
            candidate = git.read_commit(root, git.rev(root, f"refs/heads/{branch}"))
            candidates.validate_candidate(root, candidate, branch)
            if git.is_ancestor(root, candidate.sha, ledger):
                raise PromotionError("candidate is already on the ledger; adopt without a candidate to align dev")
            if candidate.parents[0] == ledger and parse_trailers(candidate.message)[TIP_TRAILER] == origin_dev:
                raise PromotionError("candidate can still finish; wait for CI or explicitly discard it")
        if not candidates.on_first_parent_line(root, origin_dev, ledger):
            raise PromotionError("origin/dev does not contain the shared ledger on its first-parent line")
        if git.is_ancestor(root, origin_dev, previous):
            parent = previous
            tail = []
        else:
            reachable = set(git.out(root, "rev-list", origin_dev).splitlines())
            base = next((sha for sha in git.ancestry(root, previous) if sha in reachable), None)
            if base is None:
                raise PromotionError("local dev and origin/dev have no shared history")
            tail = candidates.content_tail(root, base, git.commits_after(root, base, previous))
            parent = origin_dev
            trees = candidates.replay_trees(root, git.tree_of(root, origin_dev), tail)
            for commit, tree in zip(tail, trees, strict=True):
                parent = git.commit_tree(root, tree, (parent,), commit.message, git.replay_env(root, commit.sha))
        # Finish all conflict-prone construction before changing the worktree or refs.
        git.require_checkout(root)
        if git.rev(root, "refs/heads/dev") != previous:
            raise PromotionError("local dev moved during adoption")
        if parent != previous:
            git.run(root, "reset", "--keep", parent)
        if candidate is not None:
            git.discard_owned(root, branch, candidate.sha)
        remaining = [
            commit for commit in git.commits_after(root, ledger, parent)
            if not (len(commit.parents) == 2 and commit.tree == git.tree_of(root, commit.parents[0]))
        ]
        if not remaining:
            print("nothing left to cut")
        else:
            print(f"remaining dev range {ledger}..{parent} ({len(remaining)} content commit(s))")
        if parent != origin_dev:
            print(f"replayed {len(tail)} commit(s); local dev {parent}; push dev before preparing")
        return parent
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
    candidates.assess_push(root, updates)
