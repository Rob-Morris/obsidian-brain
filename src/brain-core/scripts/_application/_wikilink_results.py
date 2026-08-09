"""Bounded structural wikilink result values shared by mutations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class WikilinkFindingStatus(str, Enum):
    BROKEN = "broken"
    AMBIGUOUS = "ambiguous"
    RESOLVABLE = "resolvable"


@dataclass(frozen=True, slots=True)
class WikilinkFinding:
    stem: str
    status: WikilinkFindingStatus
    resolved_to: str | None
    strategy: str
    candidates: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WikilinkFix:
    target: str
    resolved_to: str
    strategy: str


def wikilink_result_values(result) -> tuple[
    tuple[WikilinkFinding, ...], tuple[WikilinkFix, ...], int
]:
    findings = tuple(
        WikilinkFinding(
            stem=item["stem"],
            status=WikilinkFindingStatus(item["status"]),
            resolved_to=item.get("resolved_to"),
            strategy=item["strategy"],
            candidates=tuple(item.get("candidates") or ()),
        )
        for item in result.get("wikilink_warnings") or ()
    )
    fix_summary = result.get("wikilink_fixes") or {}
    fixes = tuple(
        WikilinkFix(
            target=item["target"],
            resolved_to=item["resolved_to"],
            strategy=item["strategy"],
        )
        for item in fix_summary.get("fixes") or ()
    )
    return findings, fixes, int(fix_summary.get("applied", 0))
