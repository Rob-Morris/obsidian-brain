"""Vault-local branch eligibility and sampled operation continuity.

Effect owners call checkpoints explicitly. This module neither intercepts file
I/O nor coordinates arbitrary Git writers; a switch between samples (including
away-and-back) is outside its guarantee.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Iterator

from .local_config import LocalConfigError, patch_local_config, read_local_config

CONTEXT_ENV = "BRAIN_BRANCH_GUARD_CONTEXT"
_active: ContextVar[dict[str, "Operation"]] = ContextVar("brain_branch_operations", default={})


class BranchGuardError(RuntimeError):
    """A protected effect was refused; diagnostics contain no raw settings/stderr."""

    @dataclass(frozen=True, slots=True)
    class CompletedEffect:
        kind: str
        path: str
        destination: str | None = None

    def __init__(self, reason: str, facts: dict | None = None):
        self.reason = reason
        self.facts = dict(facts or {}, reason=reason)
        super().__init__(
            f"Vault branch guard refused the protected mutation ({reason}). "
            "Inspect vault.read-config or Doctor; select the intended checkout "
            "or explicitly configure its branch policy before retrying."
        )

    @property
    def completed_effects(self) -> tuple[CompletedEffect, ...]:
        """JSON-shaped facts survive fresh-interpreter refusal handoff."""
        records = self.facts.get("completed_effects", ())
        if not isinstance(records, (list, tuple)):
            return ()
        return tuple(self.CompletedEffect(item["kind"], item["path"], item.get("destination"))
                     for item in records if isinstance(item, dict)
                     and item.get("kind") in ("write", "delete", "move")
                     and isinstance(item.get("path"), str)
                     and (item.get("destination") is None or isinstance(item["destination"], str)))

    def record_completed(self, kind: str, path: str, *, destination: str | None = None) -> None:
        """Merge an outer phase's known effects without replacing inner evidence."""
        effect = self.CompletedEffect(kind, path, destination)
        effects = self.completed_effects
        if effect not in effects:
            self.facts["completed_effects"] = [
                {"kind": item.kind, "path": item.path, "destination": item.destination}
                for item in (*effects, effect)
            ]


@dataclass(frozen=True, slots=True)
class GitState:
    repository: str | None
    branch: str | None


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    # A selected vault must not be redirected by caller Git environment state.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(LC_ALL="C", GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args], env=env, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=5, check=False,
        )
    except FileNotFoundError as exc:
        raise BranchGuardError("git-unavailable") from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BranchGuardError("inspection-failed") from exc


def observe_git(vault_root: str | Path) -> GitState:
    root = Path(vault_root).resolve()
    result = _git(root, "rev-parse", "--show-toplevel")
    if result.returncode:
        # Ordinary absence can be distinguished from damaged/inaccessible repo
        # metadata without treating arbitrary Git errors as an unguarded vault.
        try:
            has_marker = any(os.path.lexists(p / ".git") for p in (root, *root.parents))
        except OSError as exc:
            raise BranchGuardError("inspection-failed") from exc
        if result.returncode == 128 and not has_marker and "not a git repository" in result.stderr:
            return GitState(None, None)
        raise BranchGuardError("inspection-failed")
    repository = result.stdout.rstrip("\r\n")
    if not repository:
        raise BranchGuardError("inspection-failed")
    head = _git(root, "symbolic-ref", "--quiet", "HEAD")
    if head.returncode == 1:
        return GitState(repository, None)
    if head.returncode or not head.stdout.startswith("refs/heads/"):
        raise BranchGuardError("inspection-failed")
    return GitState(repository, head.stdout.rstrip("\r\n")[len("refs/heads/"):])


def _valid_branch(name: object) -> bool:
    # The literal refs/heads/<name> rules from git-check-ref-format. No shorthand
    # expansion, patterns, revision expressions or existence requirement.
    return (
        isinstance(name, str) and bool(name) and name != "HEAD"
        and not name.startswith(("/", "-")) and not name.endswith(("/", "."))
        and not any(token in name for token in ("..", "@{", "//"))
        and re.search(r"[\x00-\x20\x7f~^:?*\[\\]", name) is None
        and all(part and not part.startswith(".") and not part.endswith(".lock")
                for part in name.split("/"))
    )


def _allowed(document: dict) -> tuple[str, ...] | None:
    if "branch_guard" not in document:
        return None
    guard = document["branch_guard"]
    if not isinstance(guard, dict) or set(guard) != {"allowed_branches"}:
        raise BranchGuardError("invalid-policy")
    return validate_allowed_branches(guard["allowed_branches"])


def validate_allowed_branches(branches: object) -> tuple[str, ...]:
    """Validate literal branch names without Git, expansion or existence checks."""
    if (not isinstance(branches, list) or not branches
            or not all(_valid_branch(name) for name in branches)
            or len(set(branches)) != len(branches)):
        raise BranchGuardError("invalid-policy")
    return tuple(branches)


@dataclass(frozen=True, slots=True)
class Policy:
    branches: tuple[str, ...] | None
    digest: str
    error: BranchGuardError | None = None


def _policy(vault_root: str | Path) -> Policy:
    try:
        document = read_local_config(vault_root).document
    except LocalConfigError as exc:
        raise BranchGuardError("unreadable-policy") from exc
    error = None
    try:
        branches = _allowed(document)
        logical = None if branches is None else sorted(branches)
    except BranchGuardError as exc:
        # A readable invalid section still has an applicability decision. Keep
        # its diagnostic separate from the policy's presence and continuity.
        branches, logical, error = (), document["branch_guard"], exc
    # Ordering of an allowlist is not a policy change. Unrelated local feature
    # and provisioning-marker writes must not invalidate their own operation.
    digest = hashlib.sha256(json.dumps(
        {"present": "branch_guard" in document, "value": logical},
        sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()
    return Policy(branches, digest, error)


@dataclass(slots=True)
class Operation:
    root: Path
    policy_digest: str | None
    branch: str | None
    initial_error: BranchGuardError | None = None
    lost: bool = False

    def evidence(self) -> dict:
        return {"version": 1, "vault_root": str(self.root),
                "policy_digest": self.policy_digest, "branch": self.branch,
                "lost": self.lost}

    def check(self) -> None:
        if self.lost:
            raise BranchGuardError("unverified-recovery-context", {"vault": str(self.root)})
        branches = None
        try:
            if self.initial_error:
                raise self.initial_error
            policy = _policy(self.root)
            branches = policy.branches
            git = observe_git(self.root) if branches is not None else GitState(None, None)
            if branches is None and policy.digest != self.policy_digest:
                # Explicitly removing a dormant setting must not impose a
                # branch restriction on a vault proven to be outside Git.
                git = observe_git(self.root)
                if git.repository is None:
                    return
            facts = {"vault": str(self.root), "repository": git.repository,
                     "observed_branch": git.branch, "allowed_branches": list(branches or ())}
            if branches is not None and git.repository is None:
                return  # Stored policy remains dormant outside Git.
            if policy.digest != self.policy_digest:
                raise BranchGuardError("policy-changed", facts)
            if branches is None:
                return
            if policy.error is not None:
                policy.error.facts.update(facts)
                raise policy.error
            if git.branch != self.branch:
                raise BranchGuardError("branch-changed", facts)
            if git.branch is None:
                raise BranchGuardError("detached-HEAD", facts)
            if git.branch not in branches:
                raise BranchGuardError("branch-not-allowed", facts)
        except BranchGuardError as exc:
            exc.facts.setdefault("vault", str(self.root))
            if branches is not None:
                exc.facts.setdefault("allowed_branches", list(branches))
            self.lost = True
            raise


def _capture(root: Path, recovery: dict | None = None) -> Operation:
    branches = None
    try:
        policy = _policy(root)
        branches, digest = policy.branches, policy.digest
        git = observe_git(root) if branches is not None else GitState(None, None)
        current = Operation(root, digest, git.branch)
        if recovery is not None:
            _validate_recovery(current, recovery, guarded=git.repository is not None and branches is not None)
        return current
    except BranchGuardError as exc:
        exc.facts.setdefault("vault", str(root))
        if branches is not None:
            exc.facts.setdefault("allowed_branches", list(branches))
        return Operation(root, None, None, exc)


def _validate_recovery(current: Operation, recovery: dict, *, guarded: bool) -> None:
    # Legacy evidence is acceptable only while no policy has ever established
    # an operation constraint. Removing a policy cannot validate old evidence.
    if not recovery and not guarded:
        return
    if (recovery.get("version") != 1 or recovery.get("vault_root") != str(current.root)
            or recovery.get("lost") is not False
            or recovery.get("policy_digest") != current.policy_digest
            or recovery.get("branch") != current.branch):
        current.initial_error = BranchGuardError("unverified-recovery-context")


@contextmanager
def operation(vault_root: str | Path, *, recovery: dict | None = None) -> Iterator[Operation]:
    """Carry one starting branch through nested owner phases, without gating reads."""
    root = Path(vault_root).resolve()
    key = str(root)
    existing = _active.get().get(key)
    if existing is not None:
        if recovery is not None:
            try:
                policy = _policy(root)
                applicable = policy.branches is not None and observe_git(root).repository is not None
                _validate_recovery(existing, recovery, guarded=applicable)
            except BranchGuardError as exc:
                existing.initial_error = exc
        yield existing
        return
    inherited = os.environ.get(CONTEXT_ENV)
    if recovery is None and inherited:
        try:
            recovery = json.loads(inherited)
            if not isinstance(recovery, dict):
                raise ValueError("invalid context")
        except (ValueError, TypeError):
            recovery = {}  # Refuse at the first protected effect, not read time.
    current = _capture(root, recovery)
    token = _active.set(dict(_active.get(), **{key: current}))
    try:
        yield current
    finally:
        _active.reset(token)


def checkpoint(vault_root: str | Path) -> None:
    with operation(vault_root) as current:
        current.check()


def export_context(vault_root: str | Path) -> str:
    with operation(vault_root) as current:
        return json.dumps(current.evidence(), separators=(",", ":"))


def retain_context_loss(vault_root: str | Path) -> None:
    """Carry a child's observed refusal back into its enclosing operation."""
    current = _active.get().get(str(Path(vault_root).resolve()))
    if current is not None:
        current.lost = True


def inspect_guard(vault_root: str | Path) -> dict:
    """Bounded inspection, including malformed guard sections and whole-file CAS."""
    root = Path(vault_root).resolve()
    result = {"status": "unconfigured", "local_config_revision": None,
              "allowed_branches": [], "repository": None, "observed_branch": None,
              "reason": None}
    configured = False
    try:
        local = read_local_config(root)
        result["local_config_revision"] = local.revision
        configured = "branch_guard" in local.document
        try:
            branches = _allowed(local.document)
        except BranchGuardError as exc:
            result.update(status="invalid", reason=exc.reason)
            branches = None
        if branches is not None:
            result["allowed_branches"] = list(branches)
        git = observe_git(root)
        result.update(repository=git.repository, observed_branch=git.branch)
        if result["status"] == "invalid":
            if git.repository is None:
                result["status"] = "dormant"
            return result
        if branches is None:
            return result
        result["status"] = "dormant" if git.repository is None else "eligible"
        if git.repository is not None and (git.branch is None or git.branch not in branches):
            result.update(status="blocked", reason="detached-HEAD" if git.branch is None else "branch-not-allowed")
    except LocalConfigError:
        result.update(status="invalid", reason="unreadable-policy")
    except BranchGuardError as exc:
        if result["status"] != "invalid":
            result.update(status="unavailable" if configured else "unconfigured", reason=exc.reason)
    return result


def configure_guard(vault_root: str | Path, allowed_branches: list[str] | None,
                    expected_revision: str) -> dict:
    """The sole explicit policy recovery write; unrelated settings stay intact."""
    if not isinstance(expected_revision, str) or not expected_revision:
        raise ValueError("expected_revision is required")
    def require_applicability():
        if observe_git(vault_root).repository is None:
            raise BranchGuardError("outside-git")
    if allowed_branches is not None:
        _allowed({"branch_guard": {"allowed_branches": allowed_branches}})
        require_applicability()
    def update(document):
        if allowed_branches is None:
            document.pop("branch_guard", None)
        else:
            document["branch_guard"] = {"allowed_branches": list(allowed_branches)}
    after = patch_local_config(
        vault_root, update, expected_revision=expected_revision,
        before_write=require_applicability if allowed_branches is not None else None,
    )
    return {"changed": expected_revision != after.revision,
            "local_config_revision": after.revision,
            "allowed_branches": [] if allowed_branches is None else list(allowed_branches)}
