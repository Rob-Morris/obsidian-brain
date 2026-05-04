"""Tests for construct_benchmark_fixture.py."""

import json
from pathlib import Path

import construct_benchmark_fixture as cbf
import pytest


class TestParseArgs:
    def test_collects_paths_targets_and_json_mode(self):
        (
            fixture_out,
            audit_out,
            vault_arg,
            targets,
            json_mode,
            semantic_strategy,
            semantic_seed_file,
            hybrid_seed_file,
        ) = cbf.parse_args(
            [
                "construct_benchmark_fixture.py",
                "--fixture-out",
                "fixture.json",
                "--audit-out",
                "audit.json",
                "--vault",
                "/tmp/vault",
                "--target-lexical",
                "4",
                "--target-hybrid",
                "9",
                "--semantic-strategy",
                "assisted-zero-overlap",
                "--semantic-seed-file",
                "semantic-seeds.json",
                "--hybrid-seed-file",
                "hybrid-seeds.json",
                "--json",
            ]
        )

        assert fixture_out == "fixture.json"
        assert audit_out == "audit.json"
        assert vault_arg == "/tmp/vault"
        assert targets["lexical-expected"] == 4
        assert targets["hybrid-expected"] == 9
        assert targets["semantic-expected"] == cbf.DEFAULT_TARGETS["semantic-expected"]
        assert json_mode is True
        assert semantic_strategy == cbf.SEMANTIC_STRATEGY_ASSISTED_ZERO_OVERLAP
        assert semantic_seed_file == "semantic-seeds.json"
        assert hybrid_seed_file == "hybrid-seeds.json"


class TestSelection:
    def test_select_cases_respects_targets_and_shortfall(self):
        audits = [
            {
                "id": "lex-1",
                "bucket": "lexical-expected",
                "query": "A-1",
                "relevant_paths": ["A.md"],
                "filters": {},
                "admitted": True,
                "admission_reason": "ok",
                "ranks": {"lexical": 1, "semantic": None, "hybrid": 1},
            },
            {
                "id": "lex-2",
                "bucket": "lexical-expected",
                "query": "A-2",
                "relevant_paths": ["B.md"],
                "filters": {},
                "admitted": False,
                "admission_reason": "nope",
                "ranks": {"lexical": 2, "semantic": None, "hybrid": 2},
            },
            {
                "id": "sem-1",
                "bucket": "semantic-expected",
                "query": "concept query",
                "relevant_paths": ["C.md"],
                "filters": {},
                "admitted": True,
                "admission_reason": "ok",
                "ranks": {"lexical": None, "semantic": 1, "hybrid": None},
                "title_overlap_ratio": 0.1,
            },
        ]

        cases, summary = cbf._select_cases(
            audits,
            {
                "lexical-expected": 2,
                "semantic-expected": 1,
                "hybrid-expected": 0,
                "cluster-expected": 0,
                "filter-sensitive": 0,
            },
        )

        assert [case["id"] for case in cases] == ["lex-1", "sem-1"]
        assert summary["lexical-expected"]["admitted"] == 1
        assert summary["lexical-expected"]["shortfall"] == 1
        assert summary["semantic-expected"]["admitted"] == 1
        assert summary["semantic-expected"]["shortfall"] == 0

    def test_select_cases_prefers_distinct_lexical_families(self):
        audits = [
            {
                "id": "lex-alpha-1",
                "bucket": "lexical-expected",
                "query": "ALX-17",
                "relevant_paths": ["A.md"],
                "filters": {},
                "source_path": "Wiki/A.md",
                "admitted": True,
                "admission_reason": "ok",
                "ranks": {"lexical": 1, "semantic": None, "hybrid": 1},
            },
            {
                "id": "lex-alpha-2",
                "bucket": "lexical-expected",
                "query": "ALX-23",
                "relevant_paths": ["B.md"],
                "filters": {},
                "source_path": "Wiki/B.md",
                "admitted": True,
                "admission_reason": "ok",
                "ranks": {"lexical": 1, "semantic": None, "hybrid": 1},
            },
            {
                "id": "lex-bravo-1",
                "bucket": "lexical-expected",
                "query": "BMR-17",
                "relevant_paths": ["C.md"],
                "filters": {},
                "source_path": "Wiki/C.md",
                "admitted": True,
                "admission_reason": "ok",
                "ranks": {"lexical": 1, "semantic": None, "hybrid": 1},
            },
        ]

        cases, summary = cbf._select_cases(
            audits,
            {
                "lexical-expected": 2,
                "semantic-expected": 0,
                "hybrid-expected": 0,
                "cluster-expected": 0,
                "filter-sensitive": 0,
            },
        )

        assert [case["id"] for case in cases] == ["lex-alpha-1", "lex-bravo-1"]
        assert summary["lexical-expected"]["admitted"] == 2

    def test_prune_candidates_for_audit_caps_bucket_and_prefers_distinct_sources(self):
        candidates = []
        for i in range(cbf.MAX_AUDIT_CANDIDATES["semantic-expected"] + 3):
            candidates.append(
                {
                    "id": f"sem-{i}",
                    "bucket": "semantic-expected",
                    "query": f"semantic query {i}",
                    "relevant_paths": [f"S{i}.md"],
                    "filters": {},
                    "source_path": f"Doc {i // 2}.md",
                    "title_overlap_ratio": i / 1000,
                }
            )

        pruned = cbf._prune_candidates_for_audit(candidates)
        semantic = [candidate for candidate in pruned if candidate["bucket"] == "semantic-expected"]

        assert len(semantic) == cbf.MAX_AUDIT_CANDIDATES["semantic-expected"]
        assert len({candidate["source_path"] for candidate in semantic[:5]}) == 5

    def test_prune_candidates_for_audit_keeps_multiple_semantic_variants_per_source(self):
        candidates = [
            {
                "id": "sem-1",
                "bucket": "semantic-expected",
                "query": "when did i first meet my partner?",
                "relevant_paths": ["People/A.md"],
                "filters": {},
                "source_path": "People/A.md",
                "query_style": "question",
                "title_overlap_ratio": 0.0,
            },
            {
                "id": "sem-2",
                "bucket": "semantic-expected",
                "query": "where did i first meet my partner?",
                "relevant_paths": ["People/A.md"],
                "filters": {},
                "source_path": "People/A.md",
                "query_style": "question",
                "title_overlap_ratio": 0.0,
            },
            {
                "id": "sem-3",
                "bucket": "semantic-expected",
                "query": "when did i first meet my gf?",
                "relevant_paths": ["People/A.md"],
                "filters": {},
                "source_path": "People/A.md",
                "query_style": "question",
                "title_overlap_ratio": 0.0,
            },
            {
                "id": "sem-4",
                "bucket": "semantic-expected",
                "query": "what tool gives coding agents persistent memory?",
                "relevant_paths": ["People/A.md"],
                "filters": {},
                "source_path": "People/A.md",
                "query_style": "question",
                "title_overlap_ratio": 0.0,
            },
        ]

        pruned = cbf._prune_candidates_for_audit(candidates)

        assert len(pruned) == 3
        assert {candidate["id"] for candidate in pruned} == {"sem-2", "sem-3", "sem-4"}

    def test_prune_candidates_for_audit_caps_hybrid_variants_per_source(self):
        candidates = [
            {
                "id": "hyb-1",
                "bucket": "hybrid-expected",
                "query": "design system migration strategy",
                "relevant_paths": ["Designs/A.md"],
                "filters": {},
                "source_path": "Designs/A.md",
                "query_style": cbf.QUERY_STYLE_SEEDED,
                "title_overlap_ratio": 0.4,
            },
            {
                "id": "hyb-2",
                "bucket": "hybrid-expected",
                "query": "system migration strategy",
                "relevant_paths": ["Designs/A.md"],
                "filters": {},
                "source_path": "Designs/A.md",
                "query_style": cbf.QUERY_STYLE_HYBRID_REWRITE,
                "title_overlap_ratio": 0.2,
            },
            {
                "id": "hyb-3",
                "bucket": "hybrid-expected",
                "query": "migration strategy",
                "relevant_paths": ["Designs/A.md"],
                "filters": {},
                "source_path": "Designs/A.md",
                "query_style": cbf.QUERY_STYLE_HYBRID_REWRITE,
                "title_overlap_ratio": 0.1,
            },
        ]

        pruned = cbf._prune_candidates_for_audit(candidates)

        assert len(pruned) == 2
        assert {candidate["id"] for candidate in pruned} == {"hyb-1", "hyb-3"}

    def test_annotate_semantic_variant_diagnostics_marks_best_and_near_pure(self):
        audits = [
            {
                "id": "sem-best",
                "bucket": "semantic-expected",
                "query": "when did i first meet my gf?",
                "source_path": "People/Casey Rowan.md",
                "ranks": {"lexical": 2, "semantic": 1, "hybrid": 1},
                "admitted": False,
                "title_overlap_ratio": 0.0,
            },
            {
                "id": "sem-worse",
                "bucket": "semantic-expected",
                "query": "when did i first meet my girlfriend?",
                "source_path": "People/Casey Rowan.md",
                "ranks": {"lexical": 5, "semantic": 3, "hybrid": 3},
                "admitted": False,
                "title_overlap_ratio": 0.1,
            },
        ]

        cbf._annotate_semantic_variant_diagnostics(audits)

        assert audits[0]["near_pure_semantic"] is True
        assert audits[0]["best_source_variant"] is True
        assert audits[1]["near_pure_semantic"] is False
        assert audits[1]["best_source_variant"] is False

    def test_admit_hybrid_requires_hybrid_to_win_and_pure_modes_not_both_rank_one(self):
        admitted, reason = cbf._admit_hybrid(
            {
                "ranks": {"lexical": 1, "semantic": 4, "hybrid": 1},
            }
        )

        assert admitted is True
        assert "hybrid rank 1" in reason

        admitted, reason = cbf._admit_hybrid(
            {
                "ranks": {"lexical": 1, "semantic": 1, "hybrid": 1},
            }
        )

        assert admitted is False
        assert "at least one pure mode weaker than rank 1" in reason


class TestMiningHeuristics:
    def test_should_skip_benchmark_like_paths(self):
        assert cbf._should_skip_doc({"path": "Wiki/Semantic Benchmark/Pure Lexical/Foo.md"}) is True
        assert cbf._should_skip_doc({"path": "Designs/Useful Real Design.md"}) is False

    def test_should_skip_low_signal_semantic_types_for_semantic_buckets(self):
        doc = {"path": "Daily Notes/2026-03-13 Fri.md", "type": "living/daily-note"}

        assert cbf._should_skip_doc(doc, bucket="semantic-expected") is True
        assert cbf._should_skip_doc(doc, bucket="hybrid-expected") is True
        assert cbf._should_skip_doc(doc, bucket="lexical-expected") is False

    def test_build_filtered_corpus_vocab_excludes_benchmark_paths(self):
        index = {
            "documents": [
                {
                    "path": "Wiki/Real Note.md",
                    "tf": {"meaning": 1},
                    "title_tf": {"lookup": 1},
                },
                {
                    "path": "Wiki/Semantic Benchmark/Fake.md",
                    "tf": {"synthetic": 1},
                    "title_tf": {"fixture": 1},
                },
            ]
        }

        vocab = cbf._build_filtered_corpus_vocab(index)

        assert "meaning" in vocab
        assert "lookup" in vocab
        assert "synthetic" not in vocab
        assert "fixture" not in vocab

    def test_relationship_event_queries_do_not_infer_relationship_start(self):
        body = """
## Relationship

My girlfriend. First met 12 March 2024 at Riverside Cafe.
"""

        queries = cbf._relationship_event_queries(body)

        assert "when did i first meet my partner?" in queries
        assert all("relationship with my" not in query for query in queries)

    def test_relationship_event_queries_include_explicit_relationship_start(self):
        body = """
## Relationship

My girlfriend. We started dating on 15 January 2024.
"""

        queries = cbf._relationship_event_queries(body)

        assert "when did my relationship with my partner begin?" in queries
        assert "when did i first meet my partner?" not in queries

    def test_zero_overlap_rewrites_reduce_overlap(self):
        corpus_vocab = {
            "tool",
            "gives",
            "coding",
            "agents",
            "persistent",
            "memory",
            "between",
            "sessions",
        }

        rewrites = cbf._zero_overlap_rewrites(
            "what tool gives coding agents persistent memory between sessions?",
            corpus_vocab,
        )

        assert rewrites
        original_overlap = len(
            cbf._query_overlap_tokens(
                "what tool gives coding agents persistent memory between sessions?",
                corpus_vocab,
            )
        )
        assert min(len(cbf._query_overlap_tokens(rewrite, corpus_vocab)) for rewrite in rewrites) < original_overlap

    def test_load_semantic_seed_candidates_accepts_target_path(self, tmp_path):
        seed_file = tmp_path / "semantic-seeds.json"
        seed_file.write_text(
            json.dumps(
                [
                    {
                        "query": "when did i first meet my gf?",
                        "target_path": "People/Casey Rowan.md",
                        "rationale": "Relationship-start semantic probe",
                    }
                ]
            ),
            encoding="utf-8",
        )

        candidates = cbf._load_semantic_seed_candidates(seed_file)

        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate["bucket"] == "semantic-expected"
        assert candidate["query_style"] == cbf.QUERY_STYLE_SEEDED
        assert candidate["semantic_strategy"] == cbf.SEMANTIC_STRATEGY_SEED_FILE
        assert candidate["relevant_paths"] == ["People/Casey Rowan.md"]
        assert candidate["notes"] == "Relationship-start semantic probe"

    def test_load_semantic_seed_candidates_rejects_benchmark_like_target_path(self, tmp_path):
        seed_file = tmp_path / "semantic-seeds.json"
        seed_file.write_text(
            json.dumps(
                [
                    {
                        "query": "pure semantic benchmark note",
                        "target_path": "Wiki/Semantic Benchmark/Pure Semantic.md",
                    }
                ]
            ),
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="targets a benchmark-like path"):
            cbf._load_semantic_seed_candidates(seed_file)

    def test_load_semantic_seed_candidates_rejects_source_path_outside_relevant_paths(self, tmp_path):
        seed_file = tmp_path / "semantic-seeds.json"
        seed_file.write_text(
            json.dumps(
                [
                    {
                        "query": "when did i first meet my gf?",
                        "relevant_paths": ["People/Casey Rowan.md"],
                        "source_path": "People/Jordan Vale.md",
                    }
                ]
            ),
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="source_path must be one of relevant_paths"):
            cbf._load_semantic_seed_candidates(seed_file)

    def test_load_hybrid_seed_candidates_accepts_target_path(self, tmp_path):
        seed_file = tmp_path / "hybrid-seeds.json"
        seed_file.write_text(
            json.dumps(
                [
                    {
                        "query": "memory tool for coding agents between sessions",
                        "target_path": "_Temporal/Research/2026-03/20260327-research~Beads Memory Management For Agents.md",
                        "rationale": "Grey-area hybrid probe",
                    }
                ]
            ),
            encoding="utf-8",
        )

        candidates = cbf._load_hybrid_seed_candidates(seed_file)

        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate["bucket"] == "hybrid-expected"
        assert candidate["query_style"] == cbf.QUERY_STYLE_SEEDED
        assert candidate["relevant_paths"] == [
            "_Temporal/Research/2026-03/20260327-research~Beads Memory Management For Agents.md"
        ]
        assert candidate["notes"] == "Grey-area hybrid probe"

    def test_load_hybrid_seed_candidates_accepts_matching_source_path(self, tmp_path):
        seed_file = tmp_path / "hybrid-seeds.json"
        seed_file.write_text(
            json.dumps(
                [
                    {
                        "query": "memory tool for coding agents between sessions",
                        "relevant_paths": [
                            "_Temporal/Research/2026-03/20260327-research~Beads Memory Management For Agents.md"
                        ],
                        "source_path": "_Temporal/Research/2026-03/20260327-research~Beads Memory Management For Agents.md",
                    }
                ]
            ),
            encoding="utf-8",
        )

        candidates = cbf._load_hybrid_seed_candidates(seed_file)

        assert candidates[0]["source_path"] == (
            "_Temporal/Research/2026-03/20260327-research~Beads Memory Management For Agents.md"
        )

    def test_mine_candidates_includes_seeded_semantic_candidates(self, tmp_path, monkeypatch):
        index = {
            "documents": [
                {
                    "path": "People/Casey Rowan.md",
                    "title": "Casey Rowan",
                    "type": "living/person",
                    "tf": {"first": 1, "met": 1},
                    "title_tf": {"casey": 1},
                }
            ]
        }
        seed_file = tmp_path / "semantic-seeds.json"
        seed_file.write_text(
            json.dumps(
                {
                    "candidates": [
                        {
                            "query": "when did i first meet my gf?",
                            "target_path": "People/Casey Rowan.md",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        monkeypatch.setattr(cbf, "_mine_lexical_candidates", lambda _index: [])
        monkeypatch.setattr(cbf, "_mine_link_context_semantic_candidates", lambda _vault, _index: [])
        monkeypatch.setattr(
            cbf,
            "_mine_semantic_candidates",
            lambda _vault, _index, **_kwargs: [],
        )
        monkeypatch.setattr(cbf, "_mine_hybrid_candidates", lambda _vault, _index: [])
        monkeypatch.setattr(cbf, "_mine_cluster_candidates", lambda _index: [])
        monkeypatch.setattr(cbf, "_mine_filter_candidates", lambda _index: [])

        candidates = cbf.mine_candidates(
            tmp_path,
            index,
            semantic_seed_file=seed_file,
        )

        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate["query_style"] == cbf.QUERY_STYLE_SEEDED
        assert candidate["semantic_strategy"] == cbf.SEMANTIC_STRATEGY_SEED_FILE
        assert candidate["lexical_overlap_count"] >= 1
        assert candidate["title_overlap_ratio"] == 0.0

    def test_mine_candidates_includes_seeded_hybrid_candidates(self, tmp_path, monkeypatch):
        index = {
            "documents": [
                {
                    "path": "_Temporal/Research/2026-03/20260327-research~Beads Memory Management For Agents.md",
                    "title": "20260327-research~Beads Memory Management For Agents",
                    "type": "temporal/research",
                    "tf": {"persistent": 1, "memory": 1, "agents": 1},
                    "title_tf": {"beads": 1},
                }
            ]
        }
        seed_file = tmp_path / "hybrid-seeds.json"
        seed_file.write_text(
            json.dumps(
                {
                    "candidates": [
                        {
                            "query": "persistent memory for coding agents between sessions",
                            "target_path": "_Temporal/Research/2026-03/20260327-research~Beads Memory Management For Agents.md",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        monkeypatch.setattr(cbf, "_mine_lexical_candidates", lambda _index: [])
        monkeypatch.setattr(cbf, "_mine_link_context_semantic_candidates", lambda _vault, _index: [])
        monkeypatch.setattr(
            cbf,
            "_mine_semantic_candidates",
            lambda _vault, _index, **_kwargs: [],
        )
        monkeypatch.setattr(cbf, "_mine_hybrid_candidates", lambda _vault, _index: [])
        monkeypatch.setattr(cbf, "_mine_cluster_candidates", lambda _index: [])
        monkeypatch.setattr(cbf, "_mine_filter_candidates", lambda _index: [])

        candidates = cbf.mine_candidates(
            tmp_path,
            index,
            hybrid_seed_file=seed_file,
        )

        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate["bucket"] == "hybrid-expected"
        assert candidate["query_style"] == cbf.QUERY_STYLE_SEEDED
        assert candidate["lexical_overlap_count"] >= 1
        assert candidate["title_overlap_ratio"] > 0.0

    def test_pick_semantic_fragments_returns_multiple_candidates(self):
        doc = {"title": "Brain Search Design"}
        body = (
            "This note explains retrieval ranking in detail. "
            "The system compares candidate evidence across several signals before promotion. "
            "A separate section describes how operators interpret benchmark outcomes without overfitting. "
            "Finally, the design discusses maintenance workflows for search fixtures."
        )

        fragments = cbf._pick_semantic_fragments(doc, body, limit=2)

        assert len(fragments) == 2
        assert len(set(fragments)) == 2

    def test_pick_semantic_fragments_prefers_narrative_over_markdown_structure(self):
        doc = {"title": "Brain Skill System"}
        body = """
> [!question] Big open question
> - if claude code plugin is involved, what role does it play

- **Folder structure and naming** — flat vs grouped, colon notation
- **Runtime loading** — a single dispatcher that resolves any brain skill

These overlap in their core contracts: discovery, naming, resolution order, the read API.
Designing them in isolation kept producing decisions that contradicted each other or left dependencies unstated.

`brain_read(resource="skill")` is the code path.
"""

        fragments = cbf._pick_semantic_fragments(doc, body, limit=2)

        assert fragments
        assert fragments[0] in {
            "this is the master design that owns the shared contract; sub-designs specialise in folder structure, runtime loading, and",
            "designing them in isolation kept producing decisions that contradicted each other or left dependencies unstated.",
            "these overlap in their core contracts: discovery, naming, resolution order, the read api.",
        }
        assert all("brain_read" not in fragment for fragment in fragments)

    def test_hybrid_rewrite_variants_strip_leading_date_type_and_title_prefix(self):
        doc = {
            "title": "20260403-research~Raw Research - Documentation-Driven Development",
        }
        query = (
            "20260403 research raw research completed on ddd token-efficient doc structure and brain-core current state"
        )

        rewrites = cbf._hybrid_rewrite_variants(doc, query)

        assert rewrites
        assert rewrites[0] == "completed on ddd token-efficient doc structure and brain-core current state"

    def test_mine_hybrid_candidates_emits_rewrite_variant(self, monkeypatch, tmp_path):
        doc = {
            "path": "_Temporal/Research/2026-04/20260403-research~Raw Research - Documentation-Driven Development.md",
            "title": "20260403-research~Raw Research - Documentation-Driven Development",
            "type": "temporal/research",
        }
        index = {"documents": [doc]}
        body = "Long enough body for hybrid mining. " * 20

        monkeypatch.setattr(cbf, "_read_body", lambda _vault, _path: ({}, body))
        monkeypatch.setattr(
            cbf,
            "_pick_hybrid_queries",
            lambda _doc, _body, **_kwargs: [
                "20260403 research raw research completed on ddd token-efficient doc structure and brain-core current state"
            ],
        )

        candidates = cbf._mine_hybrid_candidates(tmp_path, index)

        assert len(candidates) == 2
        assert candidates[0]["query_style"] is None
        assert candidates[1]["query_style"] == cbf.QUERY_STYLE_HYBRID_REWRITE
        assert candidates[1]["query"] == "completed on ddd token-efficient doc structure and brain-core current state"

    def test_mine_link_context_semantic_candidates_uses_surrounding_context(
        self, tmp_path
    ):
        vault = tmp_path
        (vault / "Designs").mkdir()
        target = vault / "Designs" / "Target Design.md"
        source = vault / "Designs" / "Source Design.md"
        target.write_text(
            "---\ntype: living/design\n---\n# Target Design\n\nThis target covers dispatch behaviour.\n"
        )
        source.write_text(
            "---\ntype: living/design\n---\n# Source Design\n\nThis note explains the shared dispatch contract around [[Target Design]].\n"
        )
        index = {
            "documents": [
                {
                    "path": "Designs/Target Design.md",
                    "title": "Target Design",
                    "type": "living/design",
                },
                {
                    "path": "Designs/Source Design.md",
                    "title": "Source Design",
                    "type": "living/design",
                },
            ]
        }

        candidates = cbf._mine_link_context_semantic_candidates(vault, index)

        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate["relevant_paths"] == ["Designs/Target Design.md"]
        assert candidate["context_path"] == "Designs/Source Design.md"
        assert "target design" not in candidate["query"]

    def test_question_style_semantic_queries_extracts_first_met_question(self):
        doc = {"title": "Casey Rowan", "type": "living/person"}
        body = """
## Who

Legal name Casey Rowan.

## Relationship

My girlfriend. I love her a lot. First met 12 March 2024 at Riverside Cafe.
"""

        queries = cbf._question_style_semantic_queries(doc, body)

        assert "when did i first meet my gf?" in queries
        assert "when did i first meet my partner?" in queries
        assert "where did i first meet my partner?" in queries

    def test_question_style_semantic_queries_extracts_definition_question(self):
        doc = {
            "title": "20260327-research~Beads Memory Management For Agents",
            "type": "temporal/research",
        }
        body = """
## What Beads Is

Beads (`bd`) is a distributed graph issue tracker designed as persistent, structured memory for AI coding agents.
"""

        queries = cbf._question_style_semantic_queries(doc, body)

        assert any(query.startswith("what tool is") for query in queries)
        assert any(query.startswith("what tool gives") for query in queries)
        assert any("coding agents" in query for query in queries)

    def test_mine_semantic_candidates_keeps_short_docs_with_question_queries(
        self, monkeypatch, tmp_path
    ):
        doc = {
            "path": "People/Casey Rowan.md",
            "title": "Casey Rowan",
            "type": "living/person",
        }
        index = {"documents": [doc]}
        body = """
## Relationship

My girlfriend. First met 12 March 2024 at Riverside Cafe.
"""

        monkeypatch.setattr(cbf, "_read_body", lambda _vault, _path: ({}, body))

        candidates = cbf._mine_semantic_candidates(tmp_path, index)

        assert candidates
        assert candidates[0]["query_style"] == "question"
        assert candidates[0]["query"] == "when did i first meet my partner?"

    def test_mine_semantic_candidates_keeps_short_docs_with_relationship_start_query(
        self, monkeypatch, tmp_path
    ):
        doc = {
            "path": "People/Casey Rowan.md",
            "title": "Casey Rowan",
            "type": "living/person",
        }
        index = {"documents": [doc]}
        body = """
## Relationship

My girlfriend. We started dating on 15 January 2024.
"""

        monkeypatch.setattr(cbf, "_read_body", lambda _vault, _path: ({}, body))

        candidates = cbf._mine_semantic_candidates(tmp_path, index)

        assert candidates
        assert candidates[0]["query_style"] == "question"
        assert candidates[0]["query"] == "when did my relationship with my partner begin?"


class TestRunMode:
    def test_run_mode_retries_with_fallback_multiplier_when_exclusions_starve_results(
        self, monkeypatch, tmp_path
    ):
        calls = []

        def fake_search(_index, _query, _vault_root, **kwargs):
            top_k = kwargs["top_k"]
            calls.append(top_k)
            if top_k == cbf.DEFAULT_TOP_K * cbf.AUDIT_RESULT_FETCH_MULTIPLIER:
                return [
                    {"path": "Wiki/Semantic Benchmark/A.md"},
                    {"path": "Wiki/Semantic Benchmark/B.md"},
                    {"path": "Wiki/Semantic Benchmark/C.md"},
                    {"path": "Wiki/Semantic Benchmark/D.md"},
                    {"path": "Wiki/Semantic Benchmark/E.md"},
                    {"path": "Wiki/Semantic Benchmark/F.md"},
                    {"path": "Wiki/Semantic Benchmark/G.md"},
                    {"path": "Wiki/Semantic Benchmark/H.md"},
                    {"path": "Wiki/Semantic Benchmark/I.md"},
                    {"path": "Wiki/Semantic Benchmark/J.md"},
                    {"path": "Wiki/Semantic Benchmark/K.md"},
                    {"path": "Wiki/Semantic Benchmark/L.md"},
                    {"path": "Wiki/Semantic Benchmark/M.md"},
                    {"path": "Wiki/Semantic Benchmark/N.md"},
                    {"path": "Real/1.md"},
                    {"path": "Real/2.md"},
                ]
            return [
                {"path": "Wiki/Semantic Benchmark/A.md"},
                {"path": "Wiki/Semantic Benchmark/B.md"},
                {"path": "Wiki/Semantic Benchmark/C.md"},
                {"path": "Real/1.md"},
                {"path": "Real/2.md"},
                {"path": "Real/3.md"},
                {"path": "Real/4.md"},
                {"path": "Real/5.md"},
            ]

        monkeypatch.setattr(cbf.si, "search", fake_search)

        results = cbf._run_mode(
            {},
            "query",
            tmp_path,
            "lexical",
            top_k=5,
            exclude_predicate=cbf._is_benchmark_like_path,
        )

        assert calls == [15, 25]
        assert [result["path"] for result in results] == [
            "Real/1.md",
            "Real/2.md",
            "Real/3.md",
            "Real/4.md",
            "Real/5.md",
        ]


class TestConstructFixture:
    def test_writes_fixture_and_audit_with_default_audit_path(self, tmp_path, monkeypatch):
        fixture_out = tmp_path / "fixture.json"
        vault = tmp_path / "vault"
        vault.mkdir()

        monkeypatch.setattr(cbf, "_mode_available", lambda *_args, **_kwargs: (False, "semantic unavailable"))
        monkeypatch.setattr(
            cbf,
            "mine_candidates",
            lambda _vault_root, _index, **_kwargs: [
                {
                    "id": "lexical-a",
                    "bucket": "lexical-expected",
                    "query": "A-1",
                    "relevant_paths": ["A.md"],
                    "filters": {},
                    "source_path": "A.md",
                    "notes": "lexical",
                },
                {
                    "id": "semantic-b",
                    "bucket": "semantic-expected",
                    "query": "semantic query",
                    "relevant_paths": ["B.md"],
                    "filters": {},
                    "source_path": "B.md",
                    "notes": "semantic",
                },
            ],
        )

        def fake_audit(_index, _vault_root, candidate, **_kwargs):
            if candidate["bucket"] == "lexical-expected":
                return {
                    **candidate,
                    "ranks": {"lexical": 1, "semantic": None, "hybrid": 1},
                    "top_paths": {"lexical": ["A.md"], "semantic": [], "hybrid": []},
                    "cluster_recall_at_5": None,
                    "unfiltered_top_paths": [],
                    "unfiltered_distractor_count": None,
                    "admitted": True,
                    "admission_reason": "lexical rank 1 and semantic miss at top-5",
                }
            return {
                **candidate,
                "ranks": {"lexical": None, "semantic": None, "hybrid": None},
                "top_paths": {"lexical": [], "semantic": [], "hybrid": []},
                "cluster_recall_at_5": None,
                "unfiltered_top_paths": [],
                "unfiltered_distractor_count": None,
                "admitted": False,
                "admission_reason": "semantic unavailable",
            }

        monkeypatch.setattr(cbf, "_audit_candidate", fake_audit)

        result = cbf.construct_fixture(
            vault,
            fixture_out=fixture_out,
            semantic_seed_file="semantic-seeds.json",
            hybrid_seed_file="hybrid-seeds.json",
            targets={
                "lexical-expected": 1,
                "semantic-expected": 1,
                "hybrid-expected": 0,
                "cluster-expected": 0,
                "filter-sensitive": 0,
            },
            index={"documents": []},
            config={},
        )

        audit_out = fixture_out.with_name("fixture.audit.json")
        assert result["fixture_out"] == str(fixture_out)
        assert result["audit_out"] == str(audit_out)
        assert fixture_out.exists()
        assert audit_out.exists()

        fixture = json.loads(fixture_out.read_text())
        audit = json.loads(audit_out.read_text())

        assert [case["id"] for case in fixture["cases"]] == ["lexical-a"]
        assert audit["summary"]["lexical-expected"]["admitted"] == 1
        assert audit["summary"]["semantic-expected"]["shortfall"] == 1
        assert audit["semantic_available"] is False
        assert audit["semantic_seed_file"] == "semantic-seeds.json"
        assert audit["hybrid_seed_file"] == "hybrid-seeds.json"
