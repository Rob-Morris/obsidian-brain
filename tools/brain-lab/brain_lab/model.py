from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Iterable, Mapping


SCHEMA_REVISION = 1


class Outcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class EffectCertainty(StrEnum):
    NONE = "none"
    COMMITTED = "committed"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class EvidenceCompleteness(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"


_EFFECT_PRIORITY = {
    EffectCertainty.NONE: 0,
    EffectCertainty.COMMITTED: 1,
    EffectCertainty.PARTIAL: 2,
    EffectCertainty.UNKNOWN: 3,
}


def aggregate_effect_certainty(
    effects: Iterable[EffectCertainty | str],
) -> EffectCertainty:
    """Return the least certain effect reported by a composition."""
    values = (
        effect if isinstance(effect, EffectCertainty) else EffectCertainty(effect)
        for effect in effects
    )
    return max(values, key=_EFFECT_PRIORITY.__getitem__, default=EffectCertainty.NONE)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_id(prefix: str, value: Any) -> str:
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:24]}"


def require_object(value: Any, *, name: str = "request") -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a JSON object")
    return dict(value)


def require_keys(
    value: Mapping[str, Any],
    *,
    required: frozenset[str] = frozenset(),
    optional: frozenset[str] = frozenset(),
    name: str = "request",
) -> None:
    keys = frozenset(value)
    missing = required - keys
    unknown = keys - required - optional
    if missing:
        raise ValueError(f"{name} is missing required fields: {', '.join(sorted(missing))}")
    if unknown:
        raise ValueError(f"{name} has unknown fields: {', '.join(sorted(unknown))}")


def validate_linux_platform(platform: Any) -> str:
    if not isinstance(platform, str):
        raise ValueError("platform must be an explicit linux/* OCI platform")
    parts = platform.split("/")
    if len(parts) not in {2, 3} or parts[0] != "linux" or not all(parts):
        raise ValueError("platform must be an explicit linux/* OCI platform")
    return platform


@dataclass(frozen=True)
class BaseSpec:
    image: str
    platform: str
    resolved_image_id: str
    resolved_digest: str
    dockerfile_sha256: str
    build_inputs_sha256: str
    schema_revision: int = SCHEMA_REVISION

    def identity(self) -> str:
        return content_id("base", asdict(self))


@dataclass(frozen=True)
class SourceSpec:
    tree_sha256: str
    core_version: str
    core_sha256: str
    cli_version: str
    commit: str | None
    platform: str
    selector: Mapping[str, Any]
    schema_revision: int = SCHEMA_REVISION

    def identity(self) -> str:
        return content_id(
            "source",
            {
                "tree_sha256": self.tree_sha256,
                "core_version": self.core_version,
                "core_sha256": self.core_sha256,
                "cli_version": self.cli_version,
                "commit": self.commit,
                "platform": self.platform,
                "schema_revision": self.schema_revision,
            },
        )


@dataclass(frozen=True)
class VaultSeedSpec:
    kind: str
    tree_sha256: str
    core_version: str
    core_sha256: str
    platform: str
    source_id: str | None = None
    imported: bool = False
    schema_revision: int = SCHEMA_REVISION

    def identity(self) -> str:
        return content_id("seed", asdict(self))


@dataclass(frozen=True)
class PreparationSpec:
    mode: str
    adapter_id: str
    adapter_revision: int
    schema_revision: int = SCHEMA_REVISION


@dataclass(frozen=True)
class BaselineRecipe:
    base_id: str
    source_id: str
    seed_id: str
    preparation: PreparationSpec
    schema_revision: int = SCHEMA_REVISION

    def identity(self) -> str:
        return content_id("baseline", asdict(self))


@dataclass(frozen=True)
class RunSpec:
    image: str
    platform: str
    network: str = "none"

    def __post_init__(self) -> None:
        if self.network not in {"none", "bridge"}:
            raise ValueError("run network must be 'none' or 'bridge'")
        validate_linux_platform(self.platform)


@dataclass(frozen=True)
class ExecRequest:
    argv: tuple[str, ...]
    working_directory: str | None = None
    environment: Mapping[str, str] = field(default_factory=dict)
    stdin: str | None = None
    timeout_seconds: float = 300.0

    def __post_init__(self) -> None:
        if not self.argv or not all(isinstance(item, str) and item for item in self.argv):
            raise ValueError("exec argv must be a non-empty array of non-empty strings")
        if self.timeout_seconds <= 0:
            raise ValueError("exec timeout_seconds must be greater than zero")
        if not all(isinstance(key, str) and isinstance(value, str) for key, value in self.environment.items()):
            raise ValueError("exec environment must contain string keys and values")


@dataclass(frozen=True)
class ScenarioAssertion:
    left: Any
    operator: str
    right: Any

    def __post_init__(self) -> None:
        if self.operator not in {"equal", "not_equal"}:
            raise ValueError("scenario assertion operator must be 'equal' or 'not_equal'")

    def evaluate(self, left: Any, right: Any) -> bool:
        return left == right if self.operator == "equal" else left != right


@dataclass(frozen=True)
class OperationResult:
    operation_id: str
    operation: str
    outcome: Outcome
    effect_certainty: EffectCertainty
    evidence_completeness: EvidenceCompleteness
    started_at: str
    finished_at: str
    resource: Mapping[str, Any] | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    evidence_bundle: str | None = None
    errors: tuple[Mapping[str, Any], ...] = ()
    schema: str = "brain-lab.operation-result/1"

    @property
    def ok(self) -> bool:
        return self.outcome is Outcome.SUCCESS

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["outcome"] = self.outcome.value
        value["effect_certainty"] = self.effect_certainty.value
        value["evidence_completeness"] = self.evidence_completeness.value
        return value
