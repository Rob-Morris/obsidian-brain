"""Tests for evaluate_search.py — retrieval benchmark evaluation."""

import json
from pathlib import Path

import pytest

import build_index as bi
import evaluate_search as es


FIXTURE_BENCHMARK = Path(__file__).parent / "fixtures" / "inline_search_benchmark.json"


@pytest.fixture
def vault(tmp_path):
    """Create a vault with searchable content."""
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("1.0.0\n")
    (bc / "session-core.md").write_text("# Session Core\n")
    (tmp_path / "_Config").mkdir()

    wiki = tmp_path / "Wiki"
    wiki.mkdir()
    (wiki / "python-basics.md").write_text(
        "---\ntype: living/wiki\ntags: [python, programming]\nstatus: active\n---\n\n"
        "# Python Basics\n\nPython is a versatile programming language. "
        "Python supports object-oriented programming and functional programming. "
        "Python is widely used in data science and web development.\n"
    )
    (wiki / "rust-ownership.md").write_text(
        "---\ntype: living/wiki\ntags: [rust, systems]\nstatus: active\n---\n\n"
        "# Rust Ownership\n\nRust uses an ownership system to manage memory. "
        "The borrow checker enforces ownership rules at compile time. "
        "Rust prevents data races through its type system.\n"
    )
    (wiki / "javascript-async.md").write_text(
        "---\ntype: living/wiki\ntags: [javascript, web]\nstatus: draft\n---\n\n"
        "# JavaScript Async\n\nJavaScript uses promises and async/await for asynchronous programming. "
        "The event loop processes callbacks. Node.js is a JavaScript runtime.\n"
    )

    designs = tmp_path / "Designs"
    designs.mkdir()
    (designs / "brain-tooling.md").write_text(
        "---\ntype: living/design\ntags: [brain-core, tooling]\nstatus: active\n---\n\n"
        "# Brain Tooling Design\n\nThe brain-core tooling architecture uses Python scripts. "
        "Each script is self-contained with no external dependencies. "
        "The compiled router is the central configuration interface.\n"
    )

    temporal = tmp_path / "_Temporal"
    temporal.mkdir()
    logs = temporal / "Logs"
    logs.mkdir()
    month = logs / "2026-03"
    month.mkdir()
    (month / "20260315-python-log.md").write_text(
        "---\ntype: temporal/logs\ntags: [python, log]\nstatus: done\n---\n\n"
        "# Python Research Log\n\nResearched Python packaging tools. "
        "Compared pip, poetry, and pdm. Python packaging is evolving rapidly.\n"
    )

    return tmp_path


@pytest.fixture
def index(vault):
    """Build and return an index for the test vault."""
    return bi.build_index(vault)


class TestLoadBenchmark:
    def test_loads_fixture(self):
        benchmark = es.load_benchmark(FIXTURE_BENCHMARK)
        assert benchmark["hit_ks"] == (1, 3, 5)
        assert len(benchmark["cases"]) == 5
        assert benchmark["cases"][0]["id"] == "python-basics"

    def test_rejects_empty_cases(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text(json.dumps({"version": 1, "cases": []}))

        with pytest.raises(ValueError, match="non-empty cases list"):
            es.load_benchmark(path)


class TestEvaluateMode:
    def test_lexical_mode_reports_hits(self, index, vault):
        benchmark = es.load_benchmark(FIXTURE_BENCHMARK)

        summary = es.evaluate_mode(
            index,
            vault,
            benchmark["cases"],
            "lexical",
            hit_ks=benchmark["hit_ks"],
        )

        assert summary["status"] == "ok"
        assert summary["metrics"]["case_count"] == 5
        case_map = {case["id"]: case for case in summary["cases"]}
        assert case_map["python-basics"]["hits"]["1"] is True
        assert case_map["python-log"]["hits"]["5"] is True

    def test_semantic_mode_marks_unavailable_when_flag_disabled(self, index, vault):
        benchmark = es.load_benchmark(FIXTURE_BENCHMARK)
        cfg = {"defaults": {"flags": {"semantic_retrieval": False}}}

        summary = es.evaluate_mode(
            index,
            vault,
            benchmark["cases"],
            "semantic",
            hit_ks=benchmark["hit_ks"],
            config=cfg,
        )

        assert summary["status"] == "unavailable"
        assert "disabled" in summary["error"]


class TestBuildReport:
    def test_compares_modes_against_lexical(self, tmp_path, monkeypatch):
        benchmark_path = tmp_path / "benchmark.json"
        benchmark_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "hit_ks": [1, 3, 5],
                    "cases": [
                        {
                            "id": "improves",
                            "query": "q1",
                            "relevant_paths": ["A.md"],
                        },
                        {
                            "id": "regresses",
                            "query": "q2",
                            "relevant_paths": ["C.md"],
                        },
                    ],
                }
            )
        )

        monkeypatch.setattr(es.si, "load_index", lambda _vault: {"documents": []})
        monkeypatch.setattr(
            es._retrieval_embeddings,
            "load_config_best_effort",
            lambda _vault: {"defaults": {"flags": {"semantic_retrieval": True}}},
        )
        monkeypatch.setattr(es, "_mode_available", lambda *_args, **_kwargs: (True, None))
        monkeypatch.setattr(es._retrieval_embeddings, "get_query_encoder", lambda: object())

        responses = {
            ("lexical", "q1"): [
                {"path": "B.md", "title": "B", "score": 2.0},
                {"path": "A.md", "title": "A", "score": 1.0},
            ],
            ("lexical", "q2"): [
                {"path": "C.md", "title": "C", "score": 2.0},
            ],
            ("hybrid", "q1"): [
                {"path": "A.md", "title": "A", "score": 3.0},
            ],
            ("hybrid", "q2"): [
                {"path": "D.md", "title": "D", "score": 3.0},
            ],
        }

        def fake_run(_index, query, _vault, mode, **_kwargs):
            return responses[(mode, query)]

        monkeypatch.setattr(es, "_run_mode_search", fake_run)

        report = es.build_report(tmp_path, benchmark_path, modes=["lexical", "hybrid"])

        comparison = report["comparisons"][0]
        assert comparison["mode"] == "hybrid"
        assert [case["id"] for case in comparison["improved"]] == ["improves"]
        assert [case["id"] for case in comparison["regressed"]] == ["regresses"]
        assert comparison["counts"]["unchanged"] == 0

    def test_parse_args_collects_modes(self):
        benchmark, vault_root, modes, json_mode = es.parse_args(
            [
                "evaluate_search.py",
                "--benchmark",
                "bench.json",
                "--mode",
                "lexical",
                "--mode",
                "hybrid",
                "--json",
            ]
        )

        assert benchmark == "bench.json"
        assert vault_root is None
        assert modes == ["lexical", "hybrid"]
        assert json_mode is True
