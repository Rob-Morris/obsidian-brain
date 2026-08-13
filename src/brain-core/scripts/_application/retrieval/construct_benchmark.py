"""Typed ``retrieval.construct-benchmark`` owner."""

from __future__ import annotations

from .._decoding import reject_unexpected

from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Literal, Mapping

from .._benchmark_support import (
    benchmark_entry,
    optional_brain_path,
    resolve_brain_path,
    validate_count,
)
from .._mutation_support import no_effect_error
from ..context import InvocationContext
from ..receipts import CommittedEffect
from ..results import ErrorCode, Ok


class BenchmarkConstructionStatus(str, Enum):
    PLANNED = "planned"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class BenchmarkConstructionPayload:
    status: BenchmarkConstructionStatus
    dry_run: bool
    fixture_path: str
    audit_path: str
    fixture_case_count: int | None
    semantic_available: bool | None
    semantic_error: str | None
    summary: Mapping[str, object] | None


@dataclass(frozen=True, slots=True)
class RetrievalConstructBenchmarkRequest:
    COMMAND_ID: ClassVar[str] = "retrieval.construct-benchmark"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = BenchmarkConstructionPayload

    fixture_path: str
    audit_path: str | None = None
    target_lexical: int = 8
    target_semantic: int = 8
    target_hybrid: int = 8
    target_cluster: int = 4
    target_filter: int = 4
    semantic_strategy: Literal["local", "assisted-zero-overlap"] = "local"
    semantic_seed_path: str | None = None
    hybrid_seed_path: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.fixture_path, str) or not self.fixture_path.strip():
            raise ValueError("fixture_path must be a non-empty Brain-relative path")
        if self.audit_path is not None and (
            not isinstance(self.audit_path, str) or not self.audit_path.strip()
        ):
            raise ValueError("audit_path must be a non-empty Brain-relative path")
        for field in (
            "target_lexical",
            "target_semantic",
            "target_hybrid",
            "target_cluster",
            "target_filter",
        ):
            validate_count(getattr(self, field), field, default=0)
        if self.semantic_strategy not in {"local", "assisted-zero-overlap"}:
            raise ValueError("semantic_strategy must be 'local' or 'assisted-zero-overlap'")


def execute(context: InvocationContext, request: RetrievalConstructBenchmarkRequest):
    from _common import (
        MutationLockError,
        check_write_allowed,
        public_mutation_error_message,
        vault_mutation_lock,
    )
    from _lifecycle.retrieval_errors import UnreadableRetrievalSourceError
    import construct_benchmark_fixture as constructor

    root = context.selected_brain.vault_root
    try:
        fixture_abs, fixture_rel = resolve_brain_path(
            root,
            request.fixture_path,
            "fixture_path",
        )
        audit_abs, audit_rel = optional_brain_path(root, request.audit_path, "audit_path")
        if audit_abs is None:
            suffix = fixture_abs.name
            audit_name = (
                suffix[:-5] + ".audit.json"
                if suffix.endswith(".json")
                else suffix + ".audit.json"
            )
            audit_abs = fixture_abs.with_name(audit_name)
            audit_rel = audit_abs.relative_to(root.resolve()).as_posix()
        check_write_allowed(fixture_rel)
        check_write_allowed(audit_rel)
        semantic_seed_abs, _semantic_seed_rel = optional_brain_path(
            root,
            request.semantic_seed_path,
            "semantic_seed_path",
        )
        hybrid_seed_abs, _hybrid_seed_rel = optional_brain_path(
            root,
            request.hybrid_seed_path,
            "hybrid_seed_path",
        )
    except ValueError as exc:
        return no_effect_error(type(request), ErrorCode.INVALID_REQUEST, str(exc))

    if fixture_abs == audit_abs:
        return no_effect_error(
            type(request),
            ErrorCode.INVALID_REQUEST,
            "fixture_path and audit_path must identify different files",
        )
    for label, path in (
        ("semantic_seed_path", semantic_seed_abs),
        ("hybrid_seed_path", hybrid_seed_abs),
    ):
        if path is not None and not path.is_file():
            return no_effect_error(
                type(request),
                ErrorCode.NOT_FOUND,
                f"{label} does not exist: {path.relative_to(root.resolve()).as_posix()}",
            )

    if context.dry_run:
        return Ok(
            request.COMMAND_ID,
            request.COMMAND_VERSION,
            BenchmarkConstructionPayload(
                BenchmarkConstructionStatus.PLANNED,
                True,
                fixture_rel,
                audit_rel,
                None,
                None,
                None,
                None,
            ),
        )

    targets = {
        "lexical-expected": request.target_lexical,
        "semantic-expected": request.target_semantic,
        "hybrid-expected": request.target_hybrid,
        "cluster-expected": request.target_cluster,
        "filter-sensitive": request.target_filter,
    }
    try:
        constructor._load_runtime_modules()
        with vault_mutation_lock(root):
            result = constructor.construct_fixture(
                root,
                fixture_out=fixture_abs,
                audit_out=audit_abs,
                targets=targets,
                semantic_strategy=request.semantic_strategy,
                semantic_seed_file=semantic_seed_abs,
                hybrid_seed_file=hybrid_seed_abs,
            )
    except MutationLockError as exc:
        return no_effect_error(
            type(request),
            ErrorCode.CONFLICT,
            public_mutation_error_message(exc),
            retryable=True,
        )
    except (UnreadableRetrievalSourceError, ValueError) as exc:
        return no_effect_error(type(request), ErrorCode.CONFLICT, str(exc))

    payload = BenchmarkConstructionPayload(
        BenchmarkConstructionStatus.COMPLETE,
        False,
        fixture_rel,
        audit_rel,
        int(result["fixture_case_count"]),
        bool(result["semantic_available"]),
        (
            str(result["semantic_error"])
            if result.get("semantic_error") is not None
            else None
        ),
        result.get("summary"),
    )
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        payload,
        committed_effects=(
            CommittedEffect(request.COMMAND_ID, fixture_rel),
            CommittedEffect(request.COMMAND_ID, audit_rel),
        ),
    )


def decode(payload: Mapping[str, object]) -> RetrievalConstructBenchmarkRequest:
    allowed = {
        "fixture_path",
        "audit_path",
        "target_lexical",
        "target_semantic",
        "target_hybrid",
        "target_cluster",
        "target_filter",
        "semantic_strategy",
        "semantic_seed_path",
        "hybrid_seed_path",
    }
    reject_unexpected(payload, allowed)
    fixture_path = payload.get("fixture_path")
    if not isinstance(fixture_path, str):
        raise ValueError("fixture_path must be a string")
    strategy = payload.get("semantic_strategy", "local")
    if not isinstance(strategy, str):
        raise ValueError("semantic_strategy must be a string")
    return RetrievalConstructBenchmarkRequest(
        fixture_path,
        payload.get("audit_path"),
        validate_count(payload.get("target_lexical"), "target_lexical", default=8),
        validate_count(payload.get("target_semantic"), "target_semantic", default=8),
        validate_count(payload.get("target_hybrid"), "target_hybrid", default=8),
        validate_count(payload.get("target_cluster"), "target_cluster", default=4),
        validate_count(payload.get("target_filter"), "target_filter", default=4),
        strategy,
        payload.get("semantic_seed_path"),
        payload.get("hybrid_seed_path"),
    )


def catalogue_entry():
    return benchmark_entry(RetrievalConstructBenchmarkRequest, execute, mutation=True)
