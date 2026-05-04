#!/usr/bin/env python3
"""
evaluate_search.py — benchmark lexical, semantic, and hybrid retrieval quality.

Runs a JSON benchmark fixture against the local retrieval implementation and
reports hit@k metrics plus simple mode comparisons. This is a phase-3
evaluation tool for tuning the inline semantic-search branch work without
changing the underlying search contract.

Usage:
    python3 evaluate_search.py --benchmark path/to/benchmark.json
    python3 evaluate_search.py --benchmark path/to/benchmark.json --mode lexical --mode hybrid
    python3 evaluate_search.py --benchmark path/to/benchmark.json --json
"""

from __future__ import annotations

import json
import os
import sys

import retrieval_embeddings as _retrieval_embeddings
import search_index as si
from _common import find_vault_root


DEFAULT_HIT_KS = (1, 3, 5)
DEFAULT_MODES = ("lexical", "semantic", "hybrid")
VALID_FILTER_KEYS = {"type", "tag", "status"}


def _normalise_hit_ks(hit_ks):
    """Validate and normalise hit@k thresholds."""
    if hit_ks is None:
        return DEFAULT_HIT_KS
    if not isinstance(hit_ks, list) or not hit_ks:
        raise ValueError("benchmark hit_ks must be a non-empty list of positive integers")
    normalised = []
    for value in hit_ks:
        if not isinstance(value, int) or value <= 0:
            raise ValueError("benchmark hit_ks must contain positive integers only")
        if value not in normalised:
            normalised.append(value)
    return tuple(sorted(normalised))


def _normalise_modes(modes):
    """Validate and normalise requested evaluation modes."""
    if not modes:
        return DEFAULT_MODES
    normalised = []
    for mode in modes:
        if mode not in si.SEARCH_MODES:
            valid = ", ".join(sorted(si.SEARCH_MODES))
            raise ValueError(f"unknown mode '{mode}'. Valid modes: {valid}")
        if mode not in normalised:
            normalised.append(mode)
    return tuple(normalised)


def load_benchmark(path):
    """Load and validate a retrieval benchmark fixture."""
    with open(path, "r", encoding="utf-8") as handle:
        benchmark = json.load(handle)
    if not isinstance(benchmark, dict):
        raise ValueError("benchmark file must contain a top-level JSON object")

    cases = benchmark.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("benchmark file must define a non-empty cases list")

    hit_ks = _normalise_hit_ks(benchmark.get("hit_ks"))
    seen_ids = set()
    normalised_cases = []
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("each benchmark case must be a JSON object")
        case_id = case.get("id")
        query = case.get("query")
        relevant_paths = case.get("relevant_paths")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("each benchmark case must include a non-empty string id")
        if case_id in seen_ids:
            raise ValueError(f"duplicate benchmark case id: {case_id}")
        if not isinstance(query, str) or not query:
            raise ValueError(f"benchmark case '{case_id}' must include a non-empty query")
        if (
            not isinstance(relevant_paths, list)
            or not relevant_paths
            or any(not isinstance(path, str) or not path for path in relevant_paths)
        ):
            raise ValueError(
                f"benchmark case '{case_id}' must include a non-empty relevant_paths list"
            )
        filters = case.get("filters", {})
        if filters is None:
            filters = {}
        if not isinstance(filters, dict):
            raise ValueError(f"benchmark case '{case_id}' filters must be an object")
        unknown_filters = sorted(set(filters) - VALID_FILTER_KEYS)
        if unknown_filters:
            joined = ", ".join(unknown_filters)
            raise ValueError(
                f"benchmark case '{case_id}' uses unknown filters: {joined}"
            )
        normalised_case = {
            "id": case_id,
            "query": query,
            "intent": case.get("intent"),
            "notes": case.get("notes"),
            "filters": {
                key: value
                for key, value in filters.items()
                if value is not None
            },
            "relevant_paths": relevant_paths,
        }
        seen_ids.add(case_id)
        normalised_cases.append(normalised_case)

    return {
        "version": benchmark.get("version", 1),
        "description": benchmark.get("description"),
        "hit_ks": hit_ks,
        "cases": normalised_cases,
    }


def _mode_available(
    mode,
    vault_root,
    *,
    config=None,
    doc_embeddings=None,
    embeddings_meta=None,
):
    """Return (available, error_message) for one evaluation mode."""
    try:
        si.resolve_search_mode(
            vault_root,
            mode,
            config=config,
            doc_embeddings=doc_embeddings,
            embeddings_meta=embeddings_meta,
        )
    except si.SearchModeUnavailableError as exc:
        return (False, str(exc))
    return (True, None)


def _run_mode_search(
    index,
    query,
    vault_root,
    mode,
    *,
    type_filter=None,
    tag_filter=None,
    status_filter=None,
    top_k,
    doc_embeddings=None,
    embeddings_meta=None,
    query_encoder=None,
):
    """Dispatch one query through the selected retrieval mode."""
    if mode == "lexical":
        return si.search(
            index,
            query,
            vault_root,
            type_filter=type_filter,
            tag_filter=tag_filter,
            status_filter=status_filter,
            top_k=top_k,
        )
    if mode == "semantic":
        return si.search_semantic(
            query,
            vault_root,
            type_filter=type_filter,
            tag_filter=tag_filter,
            status_filter=status_filter,
            top_k=top_k,
            doc_embeddings=doc_embeddings,
            embeddings_meta=embeddings_meta,
            query_encoder=query_encoder,
        )
    return si.search_hybrid(
        index,
        query,
        vault_root,
        type_filter=type_filter,
        tag_filter=tag_filter,
        status_filter=status_filter,
        top_k=top_k,
        doc_embeddings=doc_embeddings,
        embeddings_meta=embeddings_meta,
        query_encoder=query_encoder,
    )


def _case_result(case, results, *, hit_ks):
    """Summarise one benchmark case against a ranked result list."""
    relevant_paths = set(case["relevant_paths"])
    first_relevant_rank = None
    for rank, result in enumerate(results, start=1):
        if result["path"] in relevant_paths:
            first_relevant_rank = rank
            break
    hits = {
        str(hit_k): bool(first_relevant_rank and first_relevant_rank <= hit_k)
        for hit_k in hit_ks
    }
    return {
        "id": case["id"],
        "query": case["query"],
        "intent": case.get("intent"),
        "filters": case.get("filters", {}),
        "relevant_paths": case["relevant_paths"],
        "first_relevant_rank": first_relevant_rank,
        "hits": hits,
        "top_results": [
            {
                "path": result["path"],
                "title": result["title"],
                "score": result["score"],
            }
            for result in results[: max(hit_ks)]
        ],
    }


def _mode_metrics(case_results, *, hit_ks):
    """Aggregate hit metrics for one mode summary."""
    case_count = len(case_results)
    found_ranks = [
        result["first_relevant_rank"]
        for result in case_results
        if result["first_relevant_rank"] is not None
    ]
    hit_rates = {}
    for hit_k in hit_ks:
        hit_count = sum(1 for result in case_results if result["hits"][str(hit_k)])
        hit_rates[str(hit_k)] = round(hit_count / case_count, 4)
    return {
        "case_count": case_count,
        "match_count": len(found_ranks),
        "mean_first_relevant_rank": round(sum(found_ranks) / len(found_ranks), 3)
        if found_ranks
        else None,
        "hit_rates": hit_rates,
    }


def evaluate_mode(
    index,
    vault_root,
    cases,
    mode,
    *,
    hit_ks=DEFAULT_HIT_KS,
    config=None,
    doc_embeddings=None,
    embeddings_meta=None,
    query_encoder=None,
):
    """Run one retrieval mode over the benchmark cases."""
    available, error = _mode_available(
        mode,
        vault_root,
        config=config,
        doc_embeddings=doc_embeddings,
        embeddings_meta=embeddings_meta,
    )
    if not available:
        return {
            "mode": mode,
            "status": "unavailable",
            "error": error,
            "metrics": None,
            "cases": [],
        }

    max_k = max(hit_ks)
    case_results = []
    for case in cases:
        filters = case.get("filters", {})
        results = _run_mode_search(
            index,
            case["query"],
            vault_root,
            mode,
            type_filter=filters.get("type"),
            tag_filter=filters.get("tag"),
            status_filter=filters.get("status"),
            top_k=max_k,
            doc_embeddings=doc_embeddings,
            embeddings_meta=embeddings_meta,
            query_encoder=query_encoder,
        )
        case_results.append(_case_result(case, results, hit_ks=hit_ks))

    return {
        "mode": mode,
        "status": "ok",
        "error": None,
        "metrics": _mode_metrics(case_results, hit_ks=hit_ks),
        "cases": case_results,
    }


def compare_mode_summaries(mode_summaries, *, baseline_mode="lexical"):
    """Compare each successful mode summary against the lexical baseline."""
    summaries = {
        summary["mode"]: summary
        for summary in mode_summaries
        if summary["status"] == "ok"
    }
    baseline = summaries.get(baseline_mode)
    if baseline is None:
        return []
    baseline_cases = {case["id"]: case for case in baseline["cases"]}
    comparisons = []
    for mode, summary in summaries.items():
        if mode == baseline_mode:
            continue
        improved = []
        regressed = []
        unchanged = []
        for case in summary["cases"]:
            baseline_case = baseline_cases.get(case["id"])
            if baseline_case is None:
                continue
            candidate_rank = case["first_relevant_rank"]
            baseline_rank = baseline_case["first_relevant_rank"]
            detail = {
                "id": case["id"],
                "query": case["query"],
                "baseline_rank": baseline_rank,
                "candidate_rank": candidate_rank,
            }
            if candidate_rank == baseline_rank:
                unchanged.append(detail)
            elif candidate_rank is None:
                regressed.append(detail)
            elif baseline_rank is None or candidate_rank < baseline_rank:
                improved.append(detail)
            else:
                regressed.append(detail)
        comparisons.append(
            {
                "mode": mode,
                "against": baseline_mode,
                "improved": improved,
                "regressed": regressed,
                "unchanged": unchanged,
                "counts": {
                    "improved": len(improved),
                    "regressed": len(regressed),
                    "unchanged": len(unchanged),
                },
            }
        )
    return comparisons


def build_report(
    vault_root,
    benchmark_path,
    *,
    modes=None,
    config=None,
    index=None,
    doc_embeddings=None,
    embeddings_meta=None,
    query_encoder=None,
):
    """Build a full benchmark report for one vault and benchmark fixture."""
    benchmark = load_benchmark(benchmark_path)
    modes = _normalise_modes(modes)
    if index is None:
        index = si.load_index(vault_root)
    if config is None:
        config = _retrieval_embeddings.load_config_best_effort(vault_root)

    needs_semantic = any(mode in {"semantic", "hybrid"} for mode in modes)
    if needs_semantic and (doc_embeddings is None or embeddings_meta is None):
        doc_embeddings, embeddings_meta = _retrieval_embeddings.load_doc_embeddings(
            vault_root
        )
    if needs_semantic and query_encoder is None:
        available, _error = _mode_available(
            "semantic",
            vault_root,
            config=config,
            doc_embeddings=doc_embeddings,
            embeddings_meta=embeddings_meta,
        )
        if available:
            query_encoder = _retrieval_embeddings.get_query_encoder()

    mode_summaries = []
    for mode in modes:
        mode_summaries.append(
            evaluate_mode(
                index,
                vault_root,
                benchmark["cases"],
                mode,
                hit_ks=benchmark["hit_ks"],
                config=config,
                doc_embeddings=doc_embeddings,
                embeddings_meta=embeddings_meta,
                query_encoder=query_encoder,
            )
        )

    return {
        "benchmark": {
            "path": os.path.abspath(benchmark_path),
            "description": benchmark.get("description"),
            "case_count": len(benchmark["cases"]),
            "hit_ks": list(benchmark["hit_ks"]),
        },
        "modes": mode_summaries,
        "comparisons": compare_mode_summaries(mode_summaries),
    }


def format_report(report):
    """Render a human-readable summary of one benchmark report."""
    benchmark = report["benchmark"]
    hit_ks = benchmark["hit_ks"]
    lines = [
        f"Benchmark: {benchmark['path']}",
        f"Cases: {benchmark['case_count']}",
    ]
    if benchmark.get("description"):
        lines.append(f"Description: {benchmark['description']}")
    for summary in report["modes"]:
        mode = summary["mode"]
        if summary["status"] != "ok":
            lines.append(f"\n{mode}: unavailable")
            lines.append(f"  {summary['error']}")
            continue
        metrics = summary["metrics"]
        hit_parts = [
            f"hit@{hit_k} {metrics['hit_rates'][str(hit_k)] * 100:.1f}%"
            for hit_k in hit_ks
        ]
        lines.append(f"\n{mode}: {', '.join(hit_parts)}")
        lines.append(
            f"  matches {metrics['match_count']}/{metrics['case_count']}, "
            f"mean first relevant rank {metrics['mean_first_relevant_rank']}"
        )

    if report["comparisons"]:
        lines.append("\nComparisons against lexical:")
    for comparison in report["comparisons"]:
        counts = comparison["counts"]
        lines.append(
            f"- {comparison['mode']}: improved {counts['improved']}, "
            f"regressed {counts['regressed']}, unchanged {counts['unchanged']}"
        )
        for label in ("improved", "regressed"):
            for case in comparison[label]:
                lines.append(
                    f"  {label}: {case['id']} "
                    f"(lexical={case['baseline_rank']}, {comparison['mode']}={case['candidate_rank']})"
                )
    return "\n".join(lines)


def parse_args(argv):
    """Parse CLI arguments."""
    benchmark_path = None
    vault_root = None
    modes = []
    json_mode = False

    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg == "--benchmark" and i + 1 < len(argv):
            benchmark_path = argv[i + 1]
            i += 2
        elif arg == "--vault" and i + 1 < len(argv):
            vault_root = argv[i + 1]
            i += 2
        elif arg == "--mode" and i + 1 < len(argv):
            modes.append(argv[i + 1])
            i += 2
        elif arg == "--json":
            json_mode = True
            i += 1
        else:
            i += 1

    return benchmark_path, vault_root, modes, json_mode


def main():
    benchmark_path, vault_override, modes, json_mode = parse_args(sys.argv)
    if not benchmark_path:
        print(
            "Usage: evaluate_search.py --benchmark PATH [--vault PATH] "
            "[--mode lexical|semantic|hybrid]... [--json]",
            file=sys.stderr,
        )
        sys.exit(1)

    vault_root = vault_override or find_vault_root()
    try:
        report = build_report(vault_root, benchmark_path, modes=modes)
    except (
        OSError,
        ValueError,
        si.SearchModeUnavailableError,
        _retrieval_embeddings.SemanticConfigLoadError,
    ) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if json_mode:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(format_report(report))


if __name__ == "__main__":
    main()
