"""The pass vocabulary, the ``last-pass`` summary and the advisory (DD-082, D3, D17).

One closed outcome vocabulary, one counting rule and one summary format for
both the Brain pass and the machine pass. The summary is a cache of counts
and per-family outcomes for the advisory signal and for ``list``; readers
never recompute, and correctness never depends on it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import json
from pathlib import Path
import socket
from typing import Callable, Mapping

from _bootstrap.maintenance_decisions import DECISIONS_LOCK_NAME, DECISIONS_NAME, DecisionState, ItemState
from _bootstrap.maintenance_findings import Disposition, FindingGroup


SUMMARY_SCHEMA = "brain.maintenance-pass/1"
MAINTENANCE_REL = Path(".brain") / "local" / "maintenance"
LAST_PASS_NAME = "last-pass.json"
PASS_LOCK_NAME = "pass.lock"
PASS_OUTCOMES = ("ok", "partial", "error")
BLOCKED_REASONS = ("decisions_unreadable", "detection_failed")
COUNT_FIELDS = ("needs_person", "claim_expired", "failed", "deferred")


class GroupOutcome(str, Enum):
    """Exactly one outcome per invoked automatic group (D3)."""

    REPAIRED = "repaired"
    ALREADY_CLEAN = "already_clean"
    PARTIAL = "partial"
    DEFERRED = "deferred"
    NEEDS_PERSON = "needs_person"
    FAILED = "failed"
    UNKNOWN = "unknown"


GROUP_OUTCOMES = tuple(item.value for item in GroupOutcome)
# A settled group needs nobody; the list shows the rest of the automatic groups.
SETTLED = frozenset({GroupOutcome.REPAIRED, GroupOutcome.ALREADY_CLEAN})
SHOWN_AUTOMATIC = frozenset({GroupOutcome.FAILED, GroupOutcome.UNKNOWN, GroupOutcome.DEFERRED, GroupOutcome.NEEDS_PERSON})
# The pass is partial after any of these, and an unknown-effects error after an unknown.
UNSUCCESSFUL = frozenset({GroupOutcome.PARTIAL, GroupOutcome.FAILED})
# A group withheld from the pass: a live claim holds it (D5).
WITHHELD = frozenset({ItemState.HELD})


def classify_outcome(
    status: str,
    *,
    committed: bool,
    code: str | None,
    retryable: bool,
    needs_person_codes: frozenset[str] = frozenset({"authority_denied", "authorisation_required"}),
) -> GroupOutcome:
    """Map one sibling result (its status, effects and error code) onto the vocabulary."""
    if status == "ok":
        return GroupOutcome.REPAIRED if committed else GroupOutcome.ALREADY_CLEAN
    if status == "partial":
        return GroupOutcome.PARTIAL
    if code == "conflict" and retryable:
        return GroupOutcome.DEFERRED
    if code in needs_person_codes:
        return GroupOutcome.NEEDS_PERSON
    if code == "command_outcome_unknown":
        return GroupOutcome.UNKNOWN
    return GroupOutcome.FAILED


@dataclass(frozen=True, slots=True)
class Counts:
    """The advisory counts (D17): each item once, never twice."""

    needs_person: int
    claim_expired: int
    failed: int
    deferred: int

    def as_mapping(self) -> dict[str, int]:
        return {name: getattr(self, name) for name in COUNT_FIELDS}


def count_groups(
    groups,
    states: Mapping[str, DecisionState],
    outcome_of: Callable[[FindingGroup], GroupOutcome | None],
    *,
    extra_needs_person: int = 0,
) -> Counts:
    """Judgement items count once by state; automatic groups count by outcome."""
    needs_person = extra_needs_person
    claim_expired = failed = deferred = 0
    for group in groups:
        state = states[group.key].state
        if state is ItemState.CLAIM_EXPIRED:
            claim_expired += 1
        elif group.disposition is Disposition.JUDGEMENT:
            if state is ItemState.OPEN:
                needs_person += 1
        elif group.disposition is Disposition.AUTOMATIC:
            outcome = outcome_of(group)
            if outcome is GroupOutcome.NEEDS_PERSON:
                needs_person += 1
            elif outcome in {GroupOutcome.FAILED, GroupOutcome.UNKNOWN}:
                failed += 1
            elif outcome is GroupOutcome.DEFERRED:
                deferred += 1
    return Counts(needs_person, claim_expired, failed, deferred)


@dataclass(frozen=True, slots=True)
class MaintenancePaths:
    directory: Path
    pass_lock: Path
    last_pass: Path
    decisions: Path
    decisions_lock: Path

    @classmethod
    def under(cls, directory: Path) -> "MaintenancePaths":
        return cls(directory, directory / PASS_LOCK_NAME, directory / LAST_PASS_NAME,
                   directory / DECISIONS_NAME, directory / DECISIONS_LOCK_NAME)


def brain_paths(vault_root: str | Path) -> MaintenancePaths:
    return MaintenancePaths.under(Path(vault_root) / MAINTENANCE_REL)


@dataclass(frozen=True, slots=True)
class LastPassGroup:
    scope: str
    outcome: GroupOutcome


@dataclass(frozen=True, slots=True)
class LastPassSummary:
    pass_id: str
    host: str
    finished_at: str
    outcome: str
    blocked: str | None
    groups: tuple[LastPassGroup, ...]
    counts: Counts

    @classmethod
    def from_document(cls, summary: dict) -> "LastPassSummary":
        return cls(
            summary["pass_id"], summary["host"], summary["finished_at"], summary["outcome"], summary["blocked"],
            tuple(LastPassGroup(scope, GroupOutcome(value)) for scope, value in summary["groups"].items()),
            Counts(**summary["counts"]),
        )


def host_name() -> str:
    return socket.gethostname() or "unknown"


def empty_counts() -> dict[str, int]:
    return {name: 0 for name in COUNT_FIELDS}


def build_summary(
    *,
    pass_id: str,
    host: str,
    finished_at: datetime,
    outcome: str,
    groups: Mapping[str, str],
    counts: Mapping[str, int],
    blocked: str | None = None,
) -> dict:
    """Build one validated summary document."""
    document = {
        "schema": SUMMARY_SCHEMA,
        "pass_id": pass_id,
        "host": host,
        "finished_at": finished_at.isoformat() if isinstance(finished_at, datetime) else finished_at,
        "outcome": outcome,
        "blocked": blocked,
        "groups": dict(sorted(groups.items())),
        "counts": {name: counts.get(name) for name in COUNT_FIELDS},
    }
    problem = _summary_problem(document)
    if problem is not None:
        raise ValueError(f"invalid last-pass summary: {problem}")
    return document


def _summary_problem(raw: object) -> str | None:
    """Name the first thing that makes ``raw`` unusable as a summary, or ``None``."""
    if not isinstance(raw, dict) or raw.get("schema") != SUMMARY_SCHEMA:
        return "schema"
    if not isinstance(raw.get("pass_id"), str) or not raw["pass_id"].strip():
        return "pass_id"
    if not isinstance(raw.get("host"), str) or not raw["host"].strip():
        return "host"
    try:
        finished_at = datetime.fromisoformat(raw.get("finished_at"))
    except (TypeError, ValueError):
        return "finished_at"
    if finished_at.tzinfo is None:
        return "finished_at is not timezone-aware"
    if raw.get("outcome") not in PASS_OUTCOMES:
        return "outcome"
    blocked = raw.get("blocked")
    if blocked is not None and blocked not in BLOCKED_REASONS:
        return "blocked"
    groups = raw.get("groups")
    if not isinstance(groups, dict) or any(
        not isinstance(scope, str) or value not in GROUP_OUTCOMES for scope, value in groups.items()
    ):
        return "groups"
    counts = raw.get("counts")
    if not isinstance(counts, dict) or set(counts) != set(COUNT_FIELDS) or any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counts.values()
    ):
        return "counts"
    if blocked is not None and (groups or any(counts.values())):
        return "a blocked summary carries no groups and zero counts"
    return None


def write_last_pass(path: str | Path, summary: dict) -> None:
    """Write the summary atomically; the caller decides when a write is due."""
    from _common._filesystem import safe_write_json

    problem = _summary_problem(summary)
    if problem is not None:
        raise ValueError(f"invalid last-pass summary: {problem}")
    safe_write_json(Path(path), summary, follow_symlinks=False)


def read_last_pass(path: str | Path) -> dict | None:
    """Return a fully validated summary, or ``None`` for anything unusable.

    The file is a cache that may be synced between hosts; a reader never
    trusts a field it did not check.
    """
    target = Path(path)
    try:
        if target.is_symlink() or not target.is_file():
            return None
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if _summary_problem(raw) is None else None


def read_advisory(path: str | Path, now: datetime) -> dict | None:
    """Return the coarse advisory the bootstrap surfaces carry, or ``None``."""
    if now.tzinfo is None:
        raise ValueError("advisory clock must be timezone-aware")
    summary = read_last_pass(path)
    if summary is None:
        return None
    finished_at = datetime.fromisoformat(summary["finished_at"])
    return {
        **summary["counts"],
        "blocked": summary["blocked"],
        "finished_at": summary["finished_at"],
        "age_seconds": max(0, int((now - finished_at).total_seconds())),
    }
