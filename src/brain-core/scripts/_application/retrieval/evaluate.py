"""Typed ``retrieval.evaluate`` owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._benchmark_support import benchmark_entry, resolve_brain_path
from .._mutation_support import no_effect_error
from ..context import InvocationContext
from ..results import ErrorCode, Ok


@dataclass(frozen=True, slots=True)
class RetrievalEvaluationPayload:
    benchmark_path: str
    case_count: int
    hit_ks: tuple[int, ...]
    modes: tuple[str, ...]
    report: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class RetrievalEvaluateRequest:
    COMMAND_ID: ClassVar[str] = "retrieval.evaluate"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = RetrievalEvaluationPayload

    benchmark_path: str
    modes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.benchmark_path, str) or not self.benchmark_path.strip():
            raise ValueError("benchmark_path must be a non-empty Brain-relative path")
        if any(mode not in {"lexical", "semantic", "hybrid"} for mode in self.modes):
            raise ValueError("modes must contain only lexical, semantic or hybrid")
        if len(self.modes) != len(set(self.modes)):
            raise ValueError("modes must not contain duplicates")


def execute(context: InvocationContext, request: RetrievalEvaluateRequest):
    import evaluate_search

    root = context.selected_brain.vault_root
    try:
        benchmark_abs, benchmark_rel = resolve_brain_path(
            root,
            request.benchmark_path,
            "benchmark_path",
        )
    except ValueError as exc:
        return no_effect_error(type(request), ErrorCode.INVALID_REQUEST, str(exc))
    if not benchmark_abs.is_file():
        return no_effect_error(
            type(request),
            ErrorCode.NOT_FOUND,
            f"Benchmark file not found: {benchmark_rel}",
        )

    try:
        evaluate_search._load_runtime_modules()
        report = evaluate_search.build_report(
            root,
            benchmark_abs,
            modes=list(request.modes) or None,
        )
    except (OSError, ValueError) as exc:
        return no_effect_error(type(request), ErrorCode.INVALID_REQUEST, str(exc))

    benchmark = report["benchmark"]
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        RetrievalEvaluationPayload(
            benchmark_rel,
            int(benchmark["case_count"]),
            tuple(int(value) for value in benchmark["hit_ks"]),
            tuple(summary["mode"] for summary in report["modes"]),
            report,
        ),
    )


def decode(payload: Mapping[str, object]) -> RetrievalEvaluateRequest:
    unexpected = sorted(set(payload) - {"benchmark_path", "modes"})
    if unexpected:
        raise ValueError(f"unexpected fields: {', '.join(unexpected)}")
    benchmark_path = payload.get("benchmark_path")
    if not isinstance(benchmark_path, str):
        raise ValueError("benchmark_path must be a string")
    raw_modes = payload.get("modes", ())
    if not isinstance(raw_modes, (list, tuple)) or any(
        not isinstance(mode, str) for mode in raw_modes
    ):
        raise ValueError("modes must be a list of strings")
    return RetrievalEvaluateRequest(benchmark_path, tuple(raw_modes))


def catalogue_entry():
    return benchmark_entry(RetrievalEvaluateRequest, execute, mutation=False)


def resolver_entry():
    from ..resolver import ResolverEntry

    return ResolverEntry(RetrievalEvaluateRequest, decode)
