"""Shared contracts for managed retrieval benchmark commands."""

from __future__ import annotations

from .types import InitialAuthorisationClass

from pathlib import Path

from .types import (
    Authority,
    DependencyTier,
    EffectClass,
    Locality,
    Projection,
    ProjectionEligibility,
    RetryClass,
)


MCP_UNSUPPORTED_REASON = (
    "Long-running local benchmark workflows are available through CLI, "
    "direct script and Python only."
)


def benchmark_entry(request_type, executor, *, mutation: bool):
    from .catalogue import ApplicationEntry
    from .retrieval._preparation import BENCHMARK

    return ApplicationEntry(
        initial_class=InitialAuthorisationClass.EXCEPTIONAL,
        preparation=BENCHMARK,
        request_type=request_type,
        executor=executor,
        dependency_tier=DependencyTier.MANAGED,
        locality=Locality.SELECTED_BRAIN_LOCAL,
        required_providers=(),
        optional_providers=(),
        authority=Authority.MAINTAINER,
        effect_class=(
            EffectClass.SELECTED_BRAIN_MUTATION
            if mutation
            else EffectClass.NONE
        ),
        retry_class=RetryClass.RECEIPT_REQUIRED if mutation else RetryClass.SAFE,
        projections=(
            ProjectionEligibility(Projection.MCP, False, MCP_UNSUPPORTED_REASON),
            ProjectionEligibility(Projection.CLI, True),
            ProjectionEligibility(Projection.SCRIPT, True),
            ProjectionEligibility(Projection.PYTHON, True),
        ),
    )


def resolve_brain_path(root: Path, value: object, field: str) -> tuple[Path, str]:
    from _common import resolve_and_check_bounds

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty Brain-relative path")
    candidate = Path(value)
    if candidate.is_absolute():
        raise ValueError(f"{field} must be Brain-relative")
    resolved = Path(resolve_and_check_bounds(root / candidate, root))
    return resolved, resolved.relative_to(root.resolve()).as_posix()


def optional_brain_path(
    root: Path,
    value: str | None,
    field: str,
) -> tuple[Path | None, str | None]:
    if value is None:
        return None, None
    return resolve_brain_path(root, value, field)


def validate_count(value: object, field: str, *, default: int) -> int:
    if value is None:
        return default
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value
