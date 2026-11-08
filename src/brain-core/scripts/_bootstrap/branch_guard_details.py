"""Pure bounded branch-guard diagnostics shared by receipt readers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class GuardRecoveryLocation:
    kind: str
    path: str

    def __post_init__(self):
        if any(not isinstance(value, str) or not value.strip() or len(value) > 2048
               for value in (self.kind, self.path)):
            raise ValueError("guard recovery locations require bounded nonempty text")


@dataclass(frozen=True, slots=True)
class BranchGuardDetails:
    reason: str
    vault: str | None
    repository: str | None
    observed_branch: str | None
    allowed_branches: tuple[str, ...]
    committed_paths: tuple[str, ...] = ()
    recovery_locations: tuple[GuardRecoveryLocation, ...] = ()
    truncated: bool = False
    inspection_command: Literal["vault.read-config"] = "vault.read-config"

    def __post_init__(self):
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("branch guard diagnostic reason must be a nonempty string")
        strings = (self.reason, self.vault, self.repository, self.observed_branch,
                   *self.allowed_branches, *self.committed_paths,
                   *(part for item in self.recovery_locations for part in (item.kind, item.path)))
        if any(value is not None and (not isinstance(value, str) or len(value) > 2048) for value in strings):
            raise ValueError("branch guard diagnostics require bounded text")
        if any(len(values) > 64 for values in (self.allowed_branches, self.committed_paths, self.recovery_locations)):
            raise ValueError("branch guard diagnostics exceed the bounded item count")
        if type(self.truncated) is not bool or self.inspection_command != "vault.read-config":
            raise ValueError("invalid branch guard diagnostic metadata")

    @classmethod
    def from_error(cls, error):
        facts = error.facts
        truncated = False
        def bounded(value):
            nonlocal truncated
            if value is None:
                return None
            text = str(value)
            truncated |= len(text) > 2048
            return text[:2048]
        def items(values):
            nonlocal truncated
            if not isinstance(values, (list, tuple)):
                return ()
            truncated |= len(values) > 64
            return tuple(bounded(value) for value in values[:64] if isinstance(value, str))
        allowed = items(facts.get("allowed_branches", ()))
        committed = items(tuple(dict.fromkeys(
            path for effect in error.completed_effects
            for path in (effect.path, effect.destination) if path is not None)))
        locations = tuple(GuardRecoveryLocation(key, bounded(facts[key])) for key in (
            "retained_archive", "retained_conflict_stage", "retained_previous_conflict",
            "retained_stage", "retained_backup", "retained_folder", "recovery_path", "journal_path",
        ) if facts.get(key) is not None)
        return cls(bounded(error.reason), bounded(facts.get("vault")), bounded(facts.get("repository")),
                   bounded(facts.get("observed_branch")), allowed, committed, locations, truncated)


def decode_branch_guard(value: object) -> BranchGuardDetails | None:
    if value is None:
        return None
    expected = {"reason", "vault", "repository", "observed_branch", "allowed_branches",
                "committed_paths", "recovery_locations", "truncated", "inspection_command"}
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("invalid branch guard receipt diagnostics")
    if any(not isinstance(value[key], (list, tuple)) for key in
           ("allowed_branches", "committed_paths", "recovery_locations")):
        raise ValueError("invalid branch guard receipt diagnostic lists")
    if any(not isinstance(item, dict) or set(item) != {"kind", "path"}
           for item in value["recovery_locations"]):
        raise ValueError("invalid branch guard receipt recovery location")
    if any(not isinstance(item, str) for key in ("allowed_branches", "committed_paths")
           for item in value[key]):
        raise ValueError("invalid branch guard receipt diagnostic text")
    return BranchGuardDetails(value["reason"], value["vault"], value["repository"],
        value["observed_branch"], tuple(value["allowed_branches"]), tuple(value["committed_paths"]),
        tuple(GuardRecoveryLocation(**item) for item in value["recovery_locations"]),
        value["truncated"], value["inspection_command"])
