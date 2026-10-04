"""Maintenance finding vocabulary, identity and grouping (DD-082, D4, D9). Pure."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from typing import Mapping


class Disposition(str, Enum):
    """What the maintenance pass may do with a finding of this family."""

    AUTOMATIC = "automatic"
    JUDGEMENT = "judgement"
    REPORT_ONLY = "report-only"


class Owner(str, Enum):
    """Which locality owner's pass handles the family."""

    BRAIN = "brain"
    MACHINE = "machine"


KEY_LENGTH = 16


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()[:KEY_LENGTH]


def finding_key(namespace: str, kind: str, subject: Mapping[str, object]) -> str:
    """Identify a finding by its declared discriminator, never by prose."""
    if not namespace.strip() or not kind.strip():
        raise ValueError("finding key requires a namespace and a kind")
    return _digest([namespace, kind, dict(subject)])


def finding_fingerprint(key: str, evidence: Mapping[str, object] | None) -> str:
    """Fingerprint declared evidence; equal to the key when none is declared."""
    if evidence is None:
        return key
    return _digest([key, dict(evidence)])


def family_key(namespace: str, scope: str) -> str:
    return finding_key(namespace, "family", {"scope": scope})


@dataclass(frozen=True, slots=True)
class MaintenanceFinding:
    """One raw check finding with its maintenance disposition and identity.

    ``scope`` names the repair family when the finding has one; ``subject`` is
    the declared discriminator the key was minted from; ``evidence`` is the
    check's declared evidence mapping. A report-only finding has no key: the
    maintenance surface ignores it, and a check may emit several per file.
    """

    check: str
    severity: str
    file: str | None
    message: str
    disposition: Disposition
    code: str | None = None
    scope: str | None = None
    owner: Owner | None = None
    key: str | None = None
    subject: Mapping[str, object] | None = None
    evidence: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.disposition, Disposition):
            raise ValueError(f"unknown maintenance disposition: {self.disposition!r}")
        if self.owner is not None and not isinstance(self.owner, Owner):
            raise ValueError(f"unknown maintenance owner: {self.owner!r}")
        if self.disposition is not Disposition.REPORT_ONLY and (self.key is None or self.owner is None):
            raise ValueError("counted maintenance findings require a key and an owner")


@dataclass(frozen=True, slots=True)
class FindingGroup:
    """What the pass acts on and what decisions attach to: one key, one fingerprint."""

    key: str
    fingerprint: str
    disposition: Disposition
    owner: Owner
    scope: str | None
    members: tuple[MaintenanceFinding, ...]

    @property
    def is_family(self) -> bool:
        return self.scope is not None

    @property
    def check(self) -> str:
        return self.members[0].check

    @property
    def code(self) -> str | None:
        return self.members[0].code

    @property
    def file(self) -> str | None:
        """A per-file group's members share one key, so one file; a family spans several."""
        return self.members[0].file if len(self.members) == 1 or not self.is_family else None

    @property
    def subject(self) -> Mapping[str, object]:
        return self.members[0].subject or {}

    @property
    def message(self) -> str:
        return self.members[0].message


def group_by_family(findings: tuple[MaintenanceFinding, ...]) -> tuple[FindingGroup, ...]:
    """Collapse family findings to one group per family, and per-file findings to one group per key.

    An automatic family's fingerprint is its key. A scope-wide judgement
    family is fingerprinted over its sorted member files, so a changed
    member set reopens a dismissal. A per-file finding is fingerprinted over
    its own declared evidence. Two per-file findings with one key are one
    group, fingerprinted over every member's evidence, so a claim or
    dismissal never names two groups at once.
    """
    families: dict[str, list[MaintenanceFinding]] = {}
    singles: dict[str, list[MaintenanceFinding]] = {}
    for finding in findings:
        if finding.disposition is Disposition.REPORT_ONLY:
            continue
        if finding.scope is not None:
            families.setdefault(finding.key, []).append(finding)
        else:
            singles.setdefault(finding.key, []).append(finding)
    groups = []
    for key, members in families.items():
        first = members[0]
        if first.disposition is Disposition.AUTOMATIC:
            fingerprint = key
        else:
            files = sorted({member.file for member in members if member.file is not None})
            fingerprint = finding_fingerprint(key, {"files": files})
        groups.append(FindingGroup(key, fingerprint, first.disposition, first.owner, first.scope, tuple(members)))
    for key, members in singles.items():
        first = members[0]
        if len(members) == 1:
            fingerprint = finding_fingerprint(key, first.evidence)
        else:
            evidence = sorted((None if member.evidence is None else dict(member.evidence) for member in members),
                              key=_canonical)
            fingerprint = finding_fingerprint(key, {"members": evidence})
        groups.append(FindingGroup(key, fingerprint, first.disposition, first.owner, None, tuple(members)))
    return tuple(groups)
