"""Sealed recovery transactions over the exceptional direct-main divergence."""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Callable, Mapping
from uuid import uuid4

import canary_receipt

from . import candidates, git, recovery_plan
from .model import PromotionError, PushUpdate, SHA_RE, SOURCE_TRAILER, ZERO_SHA, parse_trailers, ref_label
from .recovery_model import RecoveryPlan


CiRunner = Callable[[Path, str, str], Mapping[str, object]]
_RESULT_REF = re.compile(r"^refs/heads/recovery/([0-9a-f]{40})/result$")
_PLAN_REF = re.compile(r"^refs/heads/recovery/([0-9a-f]{40})/plan$")


def _require_plan_sha(plan_sha: str) -> None:
    if not SHA_RE.fullmatch(plan_sha):
        raise PromotionError("recover requires a full plan SHA")


def _refs(root: Path, plan: RecoveryPlan, outcome: str) -> dict[str, tuple[str | None, str | None]]:
    """Interpret only the sealed ref grammar, replacing its two SHA placeholders."""
    updates = recovery_plan.resolve_updates(root, plan, outcome)
    return {ref: (value["old"], value["new"]) for ref, value in updates.items()}


def _observe(root: Path, refs: set[str]) -> dict[str, str | None]:
    listed = git.run(root, "ls-remote", "origin", *sorted(refs), check=False)
    if listed.returncode:
        detail = listed.stderr.strip() or listed.stdout.strip() or "no output"
        raise PromotionError(f"cannot observe origin recovery refs: {detail}")
    observed = {ref: None for ref in refs}
    for line in listed.stdout.splitlines():
        sha, ref = line.split()
        if ref in observed:
            observed[ref] = sha
    return observed


def _all_refs(plan: RecoveryPlan) -> set[str]:
    refs = {"refs/heads/main", "refs/heads/unreleased", "refs/heads/dev"}
    for transaction in plan.transactions.values():
        if not isinstance(transaction, dict):
            raise PromotionError("sealed recovery transaction is malformed")
        refs.update(ref.replace("{plan}", plan.sha) for ref in transaction)
    return refs


def _fetch_record(root: Path, plan_sha: str, kind: str, remote_sha: str) -> None:
    ref = f"refs/heads/recovery/{plan_sha}/{kind}"
    tracking = f"refs/remotes/origin/recovery/{plan_sha}/{kind}"
    git.run(root, "fetch", "--refmap=", "origin", f"{ref}:{tracking}")
    if git.rev(root, tracking) != remote_sha:
        raise PromotionError(f"origin {ref} changed while fetching")


def _load(root: Path, plan_sha: str, *, fetch_remote: bool) -> RecoveryPlan:
    _require_plan_sha(plan_sha)
    if fetch_remote:
        ref = recovery_plan.plan_ref(plan_sha)
        observed = _observe(root, {ref})[ref]
        if observed != plan_sha:
            raise PromotionError(f"origin {ref} is {ref_label(observed)}, expected {plan_sha}")
        _fetch_record(root, plan_sha, "plan", plan_sha)
    plan = recovery_plan.load_plan(root, plan_sha)
    if plan.sha != plan_sha:
        raise PromotionError("loaded recovery plan does not match its requested SHA")
    return plan


def _outcome(root: Path, plan: RecoveryPlan, observed: Mapping[str, str | None]) -> str | None:
    result_ref = recovery_plan.result_ref(plan.sha)
    sha = observed[result_ref]
    if sha is None:
        return None
    _fetch_record(root, plan.sha, "result", sha)
    return recovery_plan.validate_result(root, plan, sha)


def _snapshot(plan: RecoveryPlan, observed: Mapping[str, str | None]) -> bool:
    expected = plan.snapshot
    return all((
        observed["refs/heads/main"] == expected["main"],
        observed["refs/heads/unreleased"] == expected["unreleased"],
        observed["refs/heads/dev"] == expected["dev"],
    ))


def _matching(observed: Mapping[str, str | None], updates: Mapping[str, tuple[str | None, str | None]], *, new: bool) -> bool:
    index = 1 if new else 0
    return all(observed[ref] == pair[index] for ref, pair in updates.items())


def _differences(observed: Mapping[str, str | None], updates: Mapping[str, tuple[str | None, str | None]], *, new: bool) -> str:
    index = 1 if new else 0
    return ", ".join(
        f"{ref} observed {ref_label(observed[ref])}, expected {ref_label(pair[index])}"
        for ref, pair in updates.items() if observed[ref] != pair[index]
    )


def _push(root: Path, updates: Mapping[str, tuple[str | None, str | None]]) -> object:
    args = ["push", "--atomic"]
    for ref, (old, _) in updates.items():
        args.append(f"--force-with-lease={ref}:{old or ''}")
    args.append("origin")
    for ref, (_, new) in updates.items():
        args.append(f"{new or ''}:{ref}")
    return git.run(root, *args, check=False)


def _check_receipt(root: Path, plan_sha: str) -> None:
    brief = root / ".canaries" / "pre-recovery.md"
    receipt = root / ".canary--pre-recovery"
    try:
        canary_receipt.check_files(brief, receipt)
    except canary_receipt.CanaryError as exc:
        raise PromotionError(str(exc)) from exc
    lines = receipt.read_text(encoding="utf-8").splitlines()
    bindings = [line for line in lines if line.startswith("Plan:")]
    if bindings != [f"Plan: {plan_sha}"]:
        raise PromotionError(f"recovery receipt must contain exactly 'Plan: {plan_sha}'")


def _ordinary_ledger_continuation(root: Path, start: str, target: str) -> bool:
    if not candidates.on_first_parent_line(root, target, start):
        return False
    for commit in git.commits_after(root, start, target):
        try:
            candidates.validate_candidate(root, commit)
        except PromotionError:
            return False
    return True


def _applied_chain(root: Path, start: str, target: str) -> tuple[RecoveryPlan, ...]:
    """Find the unique recorded rewrite path; ordinary first-parent finishes fill gaps."""
    if _ordinary_ledger_continuation(root, start, target):
        return ()
    listed = git.run(root, "ls-remote", "origin", "refs/heads/recovery/*/result", check=False)
    if listed.returncode:
        raise PromotionError("cannot enumerate immutable recovery results")
    refs: list[tuple[str, str, str]] = []
    for line in listed.stdout.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        result_sha, ref = parts
        match = _RESULT_REF.fullmatch(ref)
        if match is not None:
            refs.append((result_sha, ref, match.group(1)))
    scratch = f"refs/recovery-inspect/{uuid4().hex}"
    try:
        for offset in range(0, len(refs), 100):
            batch = refs[offset:offset + 100]
            git.run(root, "fetch", "--refmap=", "origin",
                    *(f"{ref}:{scratch}/{offset + index}" for index, (_sha, ref, _plan) in enumerate(batch)))
            for index, (sha, _ref, _plan) in enumerate(batch):
                if git.rev(root, f"{scratch}/{offset + index}") != sha:
                    raise PromotionError("recovery result moved while inspecting retained records")
        # These small fields are only a search index. A chosen path is fully validated below.
        indexed: list[tuple[str, str, str, str]] = []
        for result_sha, _ref, plan_sha in refs:
            result_commit = git.read_commit(root, result_sha)
            if result_commit.parents != (plan_sha,):
                continue
            result_raw = git.run(root, "show", f"{result_sha}:result.json", check=False)
            plan_raw = git.run(root, "show", f"{plan_sha}:manifest.json", check=False)
            if result_raw.returncode or plan_raw.returncode:
                continue
            try:
                result_data = json.loads(result_raw.stdout)
                plan_data = json.loads(plan_raw.stdout)
                snapshot = plan_data["snapshot"]
                source = snapshot["unreleased"] or snapshot["anchor"]
                next_tip = plan_data["target_unreleased"]
            except (ValueError, KeyError, TypeError):
                continue
            if (result_data != {"schema": "brain.promotion-recovery-result/1",
                                "plan": plan_sha, "outcome": "applied"}
                    or not isinstance(source, str) or not SHA_RE.fullmatch(source)
                    or not isinstance(next_tip, str) or not SHA_RE.fullmatch(next_tip)):
                continue
            indexed.append((plan_sha, result_sha, source, next_tip))
        paths: list[tuple[tuple[str, str, str, str], ...]] = []

        def walk(current: str, used: frozenset[str], path: tuple[tuple[str, str, str, str], ...]) -> None:
            try:
                if _ordinary_ledger_continuation(root, current, target):
                    paths.append(path)
                    return
            except PromotionError:
                return
            for item in indexed:
                plan_sha, _result_sha, source, next_tip = item
                if plan_sha in used:
                    continue
                try:
                    connected = _ordinary_ledger_continuation(root, current, source)
                except PromotionError:
                    connected = False
                if connected:
                    walk(next_tip, used | {plan_sha}, (*path, item))

        walk(start, frozenset(), ())
        valid: list[tuple[RecoveryPlan, ...]] = []
        for path in paths:
            chosen: list[RecoveryPlan] = []
            try:
                for plan_sha, result_sha, source, next_tip in path:
                    plan = _load(root, plan_sha, fetch_remote=True)
                    _fetch_record(root, plan_sha, "result", result_sha)
                    if (recovery_plan.validate_result(root, plan, result_sha) != "applied"
                            or (plan.snapshot["unreleased"] or plan.snapshot["anchor"]) != source
                            or plan.target_unreleased != next_tip):
                        raise PromotionError("recovery index differs from its sealed record")
                    chosen.append(plan)
            except PromotionError:
                continue
            valid.append(tuple(chosen))
        if len(valid) != 1 or not valid[0]:
            raise PromotionError("no unique applied recovery chain validates the ledger replacement")
        return valid[0]
    finally:
        for index in range(len(refs)):
            ref = f"{scratch}/{index}"
            if git.rev_exists(root, ref):
                git.run(root, "update-ref", "-d", ref)


def _diagnose_applied(root: Path, plan: RecoveryPlan, observed: Mapping[str, str | None]) -> None:
    """An applied result is final; inspect later shared work before recommending adopt."""
    names = ("main", "unreleased", "dev")
    heads = {name: observed[f"refs/heads/{name}"] for name in names}
    if any(value is None for value in heads.values()):
        raise PromotionError(f"applied recovery {plan.sha}; diagnosis required: a shared ref is absent")
    scratch = f"refs/recovery-inspect/{uuid4().hex}"
    refs = {name: f"{scratch}/{name}" for name in names}
    try:
        git.run(root, "fetch", "--refmap=", "origin",
                *(f"refs/heads/{name}:{refs[name]}" for name in names))
        if any(git.rev(root, refs[name]) != heads[name] for name in names):
            raise PromotionError(f"applied recovery {plan.sha}; diagnosis required: shared refs moved during observation")
        main, ledger, dev = (str(heads[name]) for name in names)
        try:
            chain = _applied_chain(root, plan.target_unreleased, ledger)
        except PromotionError as exc:
            raise PromotionError(f"applied recovery {plan.sha}; diagnosis required: {exc}") from exc
        if not candidates.on_first_parent_line(root, ledger, main):
            raise PromotionError(f"applied recovery {plan.sha}; diagnosis required: main is outside unreleased")
        if (not git.is_ancestor(root, plan.target_dev, dev)
                or not candidates.on_first_parent_line(root, dev, ledger)):
            raise PromotionError(f"applied recovery {plan.sha}; diagnosis required: dev no longer contains the recovered work")
        for entry in plan.entries:
            ref = "refs/heads/" + entry.new_ref
            candidate = observed[ref]
            expected = entry.new_candidate
            for later in chain:
                update = _refs(root, later, "stage").get(ref)
                if update is not None:
                    if update[0] != expected:
                        raise PromotionError(f"applied recovery {plan.sha}; diagnosis required: later candidate ownership is inconsistent")
                    if update[1] is None:
                        raise PromotionError(f"applied recovery {plan.sha}; diagnosis required: later candidate was deleted")
                    expected = update[1]
            if candidate == expected:
                continue
            if candidate is None and git.is_ancestor(root, expected, main):
                continue
            raise PromotionError(
                f"applied recovery {plan.sha}; diagnosis required: {ref} is {ref_label(candidate)}, "
                f"expected {expected} or published cleanup"
            )
        if _observe(root, set(observed)) != dict(observed):
            raise PromotionError(f"applied recovery {plan.sha}; diagnosis required: remote refs moved during inspection")
    finally:
        for ref in refs.values():
            if git.rev_exists(root, ref):
                git.run(root, "update-ref", "-d", ref)


def plan(root: Path, *, version_map: Mapping[str, Mapping[str, str]] | None = None, run_checks=None) -> RecoveryPlan:
    """Build and display a local, pinned plan without touching shared refs."""
    from . import workflow
    built = recovery_plan.build_plan(root, version_map=version_map,
                                     run_checks=workflow.default_checks if run_checks is None else run_checks)
    preview = built.manifest
    print(json.dumps(preview, indent=2, sort_keys=True))
    def show_diff(label: str, before: str, after: str) -> None:
        print(f"\n{label}: {before} -> {after}")
        diff = git.run(root, "diff", "--no-ext-diff", "--no-textconv", before, after).stdout
        print(diff.rstrip() if diff else "(no tree difference)")

    show_diff("main from published anchor", str(built.snapshot["anchor"]), str(built.snapshot["main"]))
    for entry in built.entries:
        print(f"replacement v{entry.old_version} {entry.old_candidate} -> v{entry.new_version} {entry.new_candidate}")
        show_diff(f"v{entry.old_version} old/new candidate", entry.old_candidate, entry.new_candidate)
    show_diff("old uncut shared tail", str(built.snapshot["unreleased"] or built.snapshot["anchor"]),
              str(built.snapshot["dev"]))
    show_diff("rebuilt shared tail", built.target_unreleased, built.target_dev)
    print(f"recovery plan {built.sha}")
    return built


def stage(root: Path, plan_sha: str) -> str:
    _require_plan_sha(plan_sha)
    lock = git.lock(root)
    try:
        plan = _load(root, plan_sha, fetch_remote=False)
        observed = _observe(root, _all_refs(plan))
        result = _outcome(root, plan, observed)
        if result:
            raise PromotionError(f"recovery {plan_sha} is already {result}")
        updates = _refs(root, plan, "stage")
        plan_ref = recovery_plan.plan_ref(plan_sha)
        if observed[plan_ref] == plan_sha:
            if not _matching(observed, updates, new=True):
                raise PromotionError("staged plan has foreign candidate refs: " + _differences(observed, updates, new=True))
            if not _snapshot(plan, observed):
                raise PromotionError("recovery is staged but its shared snapshot is stale; abort owned staging and replan")
            print(f"already staged recovery {plan_sha}")
            return "staged"
        if observed[plan_ref] is not None:
            raise PromotionError(f"foreign recovery plan ref {plan_ref}: {observed[plan_ref]}")
        if not _snapshot(plan, observed):
            raise PromotionError("recovery plan snapshot is stale")
        if not _matching(observed, updates, new=False):
            raise PromotionError("candidate ownership changed: " + _differences(observed, updates, new=False))
        _check_receipt(root, plan_sha)
        pushed = _push(root, updates)
        after = _observe(root, _all_refs(plan))
        if after[plan_ref] == plan_sha and _matching(after, updates, new=True):
            (root / ".canary--pre-recovery").unlink(missing_ok=True)
            if not _snapshot(plan, after):
                raise PromotionError(f"staged recovery {plan_sha}, but shared refs changed; abort and replan")
            print(f"staged recovery {plan_sha}")
            return "staged"
        if pushed.returncode or not _matching(after, updates, new=False):
            raise PromotionError("atomic stage did not settle; " + _differences(after, updates, new=False) + f"; push: {pushed.stderr.strip()}")
        raise PromotionError("atomic stage was rejected with unchanged refs")
    finally:
        lock.close()


def status(root: Path, plan_sha: str, *, as_json: bool = False) -> dict[str, object]:
    _require_plan_sha(plan_sha)
    remote_plan = _observe(root, {recovery_plan.plan_ref(plan_sha)})[recovery_plan.plan_ref(plan_sha)]
    plan = _load(root, plan_sha, fetch_remote=remote_plan == plan_sha)
    observed = _observe(root, _all_refs(plan))
    outcome = _outcome(root, plan, observed)
    stage_updates = _refs(root, plan, "stage")
    if outcome:
        state = outcome
    elif observed[recovery_plan.plan_ref(plan_sha)] == plan_sha and _matching(observed, stage_updates, new=True):
        state = "staged" if _snapshot(plan, observed) else "stale"
    elif observed[recovery_plan.plan_ref(plan_sha)] is None and _matching(observed, stage_updates, new=False):
        state = "local"
    else:
        state = "interfered"
    payload = {"plan": plan_sha, "state": state, "snapshot_matches": _snapshot(plan, observed),
               "observed": observed}
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"recovery {plan_sha}: {state}")
        for ref, sha in sorted(observed.items()):
            print(f"  {ref} {ref_label(sha)}")
    return payload


def apply(root: Path, plan_sha: str, *, ci: CiRunner | None = None) -> str:
    _require_plan_sha(plan_sha)
    from . import workflow
    ci = workflow.default_ci if ci is None else ci
    lock = git.lock(root)
    try:
        plan = _load(root, plan_sha, fetch_remote=True)
        before = _observe(root, _all_refs(plan))
        outcome = _outcome(root, plan, before)
        if outcome == "applied":
            _diagnose_applied(root, plan, before)
            print(f"already applied recovery {plan_sha}; adopt shared dev to settle this checkout")
            return "applied"
        if outcome == "aborted":
            raise PromotionError(f"recovery {plan_sha} was aborted")
        if before[recovery_plan.plan_ref(plan_sha)] != plan_sha or not _matching(before, _refs(root, plan, "stage"), new=True):
            raise PromotionError("recovery candidate refs are not wholly owned by the staged plan")
        if not _snapshot(plan, before):
            raise PromotionError("staged recovery snapshot is stale; abort owned staging and replan")
        for entry in plan.entries:
            branch = entry.new_ref.removeprefix("refs/heads/")
            result = ci(root, entry.new_candidate, branch)
            if result.get("state") != "passed":
                raise PromotionError(f"{branch} {entry.new_candidate} CI is {result.get('state')}, not passed")
        before = _observe(root, _all_refs(plan))
        if not _snapshot(plan, before) or not _matching(before, _refs(root, plan, "stage"), new=True) or before[recovery_plan.result_ref(plan_sha)] is not None:
            raise PromotionError("recovery refs changed during CI; no application was pushed")
        updates = _refs(root, plan, "apply")
        pushed = _push(root, updates)
        after = _observe(root, _all_refs(plan))
        outcome = _outcome(root, plan, after)
        if outcome == "applied":
            _diagnose_applied(root, plan, after)
            print(f"applied recovery {plan_sha}; adopt shared dev to settle this checkout")
            return "applied"
        if outcome == "aborted":
            raise PromotionError(f"recovery {plan_sha} was aborted by a competing transaction")
        if pushed.returncode or not _matching(after, updates, new=False):
            raise PromotionError(f"atomic apply outcome unresolved; result absent; push: {pushed.stderr.strip()}; " + _differences(after, updates, new=False))
        raise PromotionError("atomic apply was rejected with unchanged refs")
    finally:
        lock.close()


def abort(root: Path, plan_sha: str) -> str:
    _require_plan_sha(plan_sha)
    lock = git.lock(root)
    try:
        plan = _load(root, plan_sha, fetch_remote=True)
        before = _observe(root, _all_refs(plan))
        outcome = _outcome(root, plan, before)
        if outcome == "aborted":
            print(f"already aborted recovery {plan_sha}")
            return "aborted"
        if outcome == "applied":
            raise PromotionError(f"recovery {plan_sha} has applied and cannot be aborted")
        if before[recovery_plan.plan_ref(plan_sha)] != plan_sha:
            raise PromotionError("recovery plan is not staged")
        updates = _refs(root, plan, "abort")
        if not _matching(before, updates, new=False):
            raise PromotionError("cannot abort foreign candidate refs: " + _differences(before, updates, new=False))
        pushed = _push(root, updates)
        after = _observe(root, _all_refs(plan))
        outcome = _outcome(root, plan, after)
        if outcome == "aborted":
            print(f"aborted recovery {plan_sha}")
            return "aborted"
        if outcome == "applied":
            raise PromotionError(f"recovery {plan_sha} applied in a competing transaction")
        if pushed.returncode or not _matching(after, updates, new=False):
            raise PromotionError(f"atomic abort outcome unresolved; result absent; push: {pushed.stderr.strip()}; " + _differences(after, updates, new=False))
        raise PromotionError("atomic abort was rejected with unchanged refs")
    finally:
        lock.close()


def _prior_ledger_from_dev(root: Path) -> str:
    """Recover the last local shared version when a narrow clone has no tracker."""
    old_dev = git.rev(root, "refs/remotes/origin/dev")
    for sha in git.ancestry(root, old_dev):
        commit = git.read_commit(root, sha)
        if len(commit.parents) == 1 and SOURCE_TRAILER in parse_trailers(commit.message):
            candidates.validate_candidate(root, commit)
            return sha
    main = git.rev(root, "refs/remotes/origin/main")
    if not candidates.on_first_parent_line(root, old_dev, main):
        raise PromotionError("narrow clone has no unreleased tracker or known published base on dev")
    return main


def fetch_for_adoption(root: Path) -> None:
    """Validate the applied-record chain before replacing a rewritten ledger tracker."""
    tracking = "refs/remotes/origin/unreleased"
    had_tracking = git.rev_exists(root, tracking)
    old = git.rev(root, tracking) if had_tracking else _prior_ledger_from_dev(root)
    heads = _observe(root, {"refs/heads/main", "refs/heads/unreleased", "refs/heads/dev"})
    remote_unreleased = heads["refs/heads/unreleased"]
    if remote_unreleased is None:
        return
    scratch = f"refs/recovery-inspect/{uuid4().hex}"
    scratch_unreleased = scratch + "/unreleased"
    scratch_dev = scratch + "/dev"
    try:
        git.run(root, "fetch", "--refmap=", "origin",
                f"refs/heads/unreleased:{scratch_unreleased}",
                f"refs/heads/dev:{scratch_dev}")
        if (git.rev(root, scratch_unreleased) != remote_unreleased
                or git.rev(root, scratch_dev) != heads["refs/heads/dev"]):
            raise PromotionError("shared refs moved while inspecting adoption")
        if _ordinary_ledger_continuation(root, old, remote_unreleased):
            return
        try:
            chain = _applied_chain(root, old, remote_unreleased)
        except PromotionError as exc:
            raise PromotionError(
                f"origin/unreleased moved non-fast-forward from {old} to {remote_unreleased}: {exc}"
            ) from exc
        if heads["refs/heads/main"] not in git.ancestry(root, remote_unreleased):
            raise PromotionError("recovered ledger does not contain current main on its first-parent line")
        remote_dev = heads["refs/heads/dev"]
        if (remote_dev is None
                or not candidates.on_first_parent_line(root, remote_dev, remote_unreleased)
                or not git.is_ancestor(root, git.rev(root, "refs/remotes/origin/dev"), remote_dev)):
            raise PromotionError("recovered dev does not safely continue the local shared dev")
        if _observe(root, {"refs/heads/main", "refs/heads/unreleased", "refs/heads/dev"}) != heads:
            raise PromotionError("shared refs moved while validating recovered ledger")
        git.run(root, "update-ref", tracking, remote_unreleased,
                old if had_tracking else ZERO_SHA)
        print(f"accepted applied recovery chain {' -> '.join(plan.sha for plan in chain)} for origin/unreleased")
    finally:
        for ref in (scratch_unreleased, scratch_dev):
            if git.rev_exists(root, ref):
                git.run(root, "update-ref", "-d", ref)


def assess_push(root: Path, updates: list[PushUpdate]) -> bool:
    """Accept only a complete transaction encoded in one sealed plan."""
    record = [u for u in updates if _PLAN_REF.fullmatch(u.remote_ref) or _RESULT_REF.fullmatch(u.remote_ref)]
    if not record:
        if any(u.remote_ref.startswith("refs/heads/recovery/") for u in updates):
            raise PromotionError("unsupported recovery ref write")
        return False
    if len(record) != 1:
        raise PromotionError("one recovery transaction may write only one record ref")
    match = _PLAN_REF.fullmatch(record[0].remote_ref) or _RESULT_REF.fullmatch(record[0].remote_ref)
    assert match is not None
    plan = _load(root, match.group(1), fetch_remote=False)
    choices = ("stage",) if _PLAN_REF.fullmatch(record[0].remote_ref) else ("apply", "abort")
    incoming = {u.remote_ref: (None if u.remote_sha == ZERO_SHA else u.remote_sha,
                               None if u.local_sha == ZERO_SHA else u.local_sha) for u in updates}
    if len(incoming) != len(updates):
        raise PromotionError("a branch appears twice in one push")
    for choice in choices:
        expected = _refs(root, plan, choice)
        if incoming == expected:
            for entry in plan.entries:
                if choice == "stage":
                    candidates.validate_candidate(root, git.read_commit(root, entry.new_candidate),
                                                  entry.new_ref.removeprefix("refs/heads/"))
            if choice == "apply" and not git.is_ancestor(root, plan.snapshot["dev"], plan.target_dev):
                raise PromotionError("recovery dev is not a fast-forward")
            return True
    raise PromotionError("outgoing recovery refs do not match a sealed stage/apply/abort transaction")
