"""Tests for check.py — router-driven vault compliance checker."""

import json
import os
import sys
import time

import pytest

import check
import compile_router as cr
import _lifecycle.semantic_repairs as semantic_repairs
import _search.index as search_index
import _search.paths as search_paths

from brain_test_support import make_router, write_md
from brain_test_support import filesystem_is_case_sensitive

from _check_helpers import compile_minimal_router

class TestRunChecks:
    def test_full_orchestration(self, vault):
        tmp_path, router = vault
        result = check.run_checks(str(tmp_path), router)
        assert result["brain_core_version"] == "0.9.11"
        assert "checked_at" in result
        assert "summary" in result
        assert "findings" in result
        # With the clean fixtures, should have 1 info (unconfigured Projects)
        assert result["summary"]["info"] >= 1

    def test_summary_counts_correct(self, vault):
        tmp_path, router = vault
        # Add violations
        (tmp_path / "readme.md").write_text("orphan\n")
        write_md(tmp_path / "_Temporal" / "Logs" / "2026-03" / "BAD NAME.md",
                 {"type": "temporal/log", "tags": ["log"]})
        result = check.run_checks(str(tmp_path), router)
        assert result["summary"]["errors"] >= 1   # root_files
        assert result["summary"]["warnings"] >= 1  # naming

    def test_missing_router_returns_error(self, tmp_path):
        (tmp_path / ".brain-core").mkdir()
        (tmp_path / ".brain-core" / "VERSION").write_text("0.9.11\n")
        result = check.run_checks(str(tmp_path))
        assert result["summary"]["errors"] == 1
        finding = result["findings"][0]
        assert "router" in finding["check"]
        assert finding["repair"]["scope"] == "router"
        assert "repair.py router" in finding["repair"]["command"]

    def test_with_loaded_router(self, vault):
        tmp_path, router = vault
        # Pass router directly — should not need to load from file
        result = check.run_checks(str(tmp_path), router)
        assert result["brain_core_version"] == "0.9.11"

    def test_stale_loaded_router_adds_router_repair_error(self, tmp_path):
        compile_minimal_router(tmp_path)
        router_source = tmp_path / "_Config" / "router.md"
        future = time.time() + 10
        os.utime(router_source, (future, future))

        result = check.run_checks(str(tmp_path))

        hit = next(f for f in result["findings"] if f["check"] == "router")
        assert hit["severity"] == "error"
        assert "source-newer-than-router" in hit["message"]
        assert hit["repair"]["scope"] == "router"
        assert "repair.py router" in hit["repair"]["command"]
        assert result["summary"]["errors"] >= 1

    def test_invalid_router_metadata_returns_repairable_error(self, tmp_path):
        (tmp_path / ".brain-core").mkdir()
        (tmp_path / ".brain-core" / "VERSION").write_text("0.9.11\n")
        brain_local = tmp_path / ".brain" / "local"
        brain_local.mkdir(parents=True)
        (brain_local / "compiled-router.json").write_text(
            json.dumps({"meta": "oops", "artefacts": []}) + "\n"
        )

        result = check.run_checks(str(tmp_path))

        assert result["summary"] == {"errors": 1, "warnings": 0, "info": 0}
        hit = result["findings"][0]
        assert hit["check"] == "router"
        assert hit["severity"] == "error"
        assert "invalid-metadata" in hit["message"]
        assert hit["repair"]["scope"] == "router"

    def test_missing_lexical_index_adds_warning_repair_guidance(self, tmp_path):
        compile_minimal_router(tmp_path)

        result = check.run_checks(str(tmp_path))

        hit = next(f for f in result["findings"] if f["check"] == "lexical_index")
        assert hit["severity"] == "warning"
        assert "missing" in hit["message"]
        assert hit["repair"]["scope"] == "lexical"
        assert "repair.py lexical" in hit["repair"]["command"]

    def test_lexical_version_drift_adds_warning_repair_guidance(self, tmp_path):
        compile_minimal_router(tmp_path)
        build_result = search_index.build_index(str(tmp_path))
        search_index.persist_retrieval_index(str(tmp_path), build_result.index)
        index_path = tmp_path / search_paths.OUTPUT_PATH
        index = json.loads(index_path.read_text())
        index["meta"]["index_version"] = -1
        index_path.write_text(json.dumps(index, indent=2) + "\n")

        result = check.run_checks(str(tmp_path))

        hit = next(f for f in result["findings"] if f["check"] == "lexical_index")
        assert hit["severity"] == "warning"
        assert "version-drift" in hit["message"]
        assert hit["repair"]["scope"] == "lexical"

    def test_invalid_lexical_document_count_adds_warning_repair_guidance(self, tmp_path):
        compile_minimal_router(tmp_path)
        build_result = search_index.build_index(str(tmp_path))
        search_index.persist_retrieval_index(str(tmp_path), build_result.index)
        index_path = tmp_path / search_paths.OUTPUT_PATH
        index = json.loads(index_path.read_text())
        index["meta"]["document_count"] = None
        index_path.write_text(json.dumps(index, indent=2) + "\n")

        result = check.run_checks(str(tmp_path))

        hit = next(f for f in result["findings"] if f["check"] == "lexical_index")
        assert hit["severity"] == "warning"
        assert "invalid-document-count" in hit["message"]
        assert hit["repair"]["scope"] == "lexical"

    def test_fresh_lexical_index_does_not_add_repair_guidance(self, tmp_path):
        compile_minimal_router(tmp_path)
        build_result = search_index.build_index(str(tmp_path))
        search_index.persist_retrieval_index(str(tmp_path), build_result.index)

        result = check.run_checks(str(tmp_path))

        assert not any(f["check"] == "lexical_index" for f in result["findings"])

    def test_semantic_repair_guidance_suppresses_lexical_guidance(self, tmp_path, monkeypatch):
        compile_minimal_router(tmp_path)
        semantic_finding = {
            "check": "retrieval-sidecars-missing",
            "severity": "warning",
            "file": None,
            "message": "Semantic retrieval is configured on, but the embeddings sidecars are missing.",
            "repair": {"scope": "semantic", "description": "Repair semantic state.", "command": "repair.py semantic"},
        }
        monkeypatch.setattr(
            semantic_repairs,
            "collect_managed_check_findings",
            lambda _vault: [semantic_finding],
        )

        result = check.run_checks(str(tmp_path))

        assert any(f.get("repair", {}).get("scope") == "semantic" for f in result["findings"])
        assert not any(f["check"] == "lexical_index" for f in result["findings"])

    def test_human_output_prints_repair_commands_for_derived_cache_findings(self, tmp_path):
        missing_router_vault = tmp_path / "missing-router"
        missing_router_vault.mkdir()
        (missing_router_vault / ".brain-core").mkdir()
        (missing_router_vault / ".brain-core" / "VERSION").write_text("0.9.11\n")
        router_result = check.run_checks(str(missing_router_vault))
        router_lines = check.render_human_findings(router_result)

        indexed_vault = tmp_path / "indexed"
        indexed_vault.mkdir()
        compile_minimal_router(indexed_vault)
        lexical_result = check.run_checks(str(indexed_vault))
        lexical_lines = check.render_human_findings(lexical_result)

        assert any("repair.py router" in line for line in router_lines)
        assert any("repair.py lexical" in line for line in lexical_lines)


# ---------------------------------------------------------------------------
# TestCheckContext — per-run frontmatter cache
# ---------------------------------------------------------------------------

class TestCheckContext:
    def test_read_frontmatter_memoized(self, tmp_path, monkeypatch):
        f = tmp_path / "a.md"
        f.write_text("---\ntype: living/wiki\nstatus: active\n---\nBody\n")
        ctx = check.CheckContext(str(tmp_path), router={})

        calls = {"n": 0}
        real = check.read_frontmatter

        def counting(path):
            calls["n"] += 1
            return real(path)

        monkeypatch.setattr(check, "read_frontmatter", counting)
        first = ctx.read_frontmatter(str(f))
        second = ctx.read_frontmatter(str(f))
        assert first == second == {"type": "living/wiki", "status": "active"}
        assert calls["n"] == 1

    def test_file_index_lazy_and_memoized(self, tmp_path):
        (tmp_path / "Wiki").mkdir()
        (tmp_path / "Wiki" / "page.md").write_text(
            "---\ntype: living/wiki\n---\nBody\n"
        )
        ctx = check.CheckContext(str(tmp_path), router={})
        first = ctx.file_index
        second = ctx.file_index
        assert first is second
        assert "md_basenames" in first

    def test_run_checks_dedupes_frontmatter_reads(self, vault, monkeypatch):
        """With the cache, a full compliance run reads each file at most once.
        Without the cache (ctx=None per check), the same files are re-parsed
        once per check that visits them — so the cached count must be strictly
        smaller given ≥2 checks touch the same files.
        """
        tmp_path, router = vault
        calls = {"n": 0}
        seen = set()
        real = check.read_frontmatter

        def counting(path):
            calls["n"] += 1
            seen.add(path)
            return real(path)

        monkeypatch.setattr(check, "read_frontmatter", counting)

        # Cached path: run_checks threads one ctx through every check
        check.run_checks(str(tmp_path), router)
        cached_reads = calls["n"]
        cached_unique = len(seen)

        # Uncached baseline: invoke each check with ctx=None
        calls["n"] = 0
        seen.clear()
        for check_fn in check.ALL_CHECKS:
            check_fn(str(tmp_path), router, ctx=None)
        uncached_reads = calls["n"]

        # One real read per unique file, strictly fewer than the uncached total
        assert cached_reads == cached_unique
        assert cached_reads < uncached_reads


# ---------------------------------------------------------------------------
# TestParseArgs
# ---------------------------------------------------------------------------

class TestParseArgs:
    def test_defaults(self):
        json_mode, actionable, severity, vault = check.parse_args(["check.py"])
        assert not json_mode
        assert not actionable
        assert severity is None
        assert vault is None

    def test_json_flag(self):
        json_mode, _, _, _ = check.parse_args(["check.py", "--json"])
        assert json_mode

    def test_actionable_flag(self):
        _, actionable, _, _ = check.parse_args(["check.py", "--actionable"])
        assert actionable

    def test_severity_filter(self):
        _, _, severity, _ = check.parse_args(["check.py", "--severity", "warning"])
        assert severity == "warning"

    def test_invalid_severity_filter(self):
        with pytest.raises(SystemExit, match="--severity must be one of"):
            check.parse_args(["check.py", "--severity", "fatal"])

    def test_vault_flag(self):
        _, _, _, vault = check.parse_args(["check.py", "--vault", "/path/to/vault"])
        assert vault == "/path/to/vault"

    def test_combined_flags(self):
        json_mode, actionable, severity, vault = check.parse_args(
            ["check.py", "--json", "--actionable", "--severity", "error", "--vault", "/tmp/v"])
        assert json_mode
        assert actionable
        assert severity == "error"
        assert vault == "/tmp/v"


# ---------------------------------------------------------------------------
# TestOutput
# ---------------------------------------------------------------------------

class TestOutput:
    def test_json_output_valid(self, vault):
        tmp_path, router = vault
        result = check.run_checks(str(tmp_path), router)
        json_str = json.dumps(result, indent=2, ensure_ascii=False)
        parsed = json.loads(json_str)
        assert parsed["brain_core_version"] == "0.9.11"

    def test_findings_have_expected_keys(self, vault):
        tmp_path, router = vault
        (tmp_path / "readme.md").write_text("orphan\n")
        result = check.run_checks(str(tmp_path), router)
        for f in result["findings"]:
            assert "check" in f
            assert "severity" in f
            assert "message" in f
            # file can be None for folder-level checks


class TestCheckCli:
    def test_main_reexecs_into_managed_runtime_when_bootstrap_succeeds(
        self, vault, monkeypatch
    ):
        tmp_path, _router = vault
        captured = {}

        class _ReexecCalled(Exception):
            pass

        def fake_handoff(vault_root, *, dependency_owner, forwarded_args, script_path, required_modules):
            captured["vault_root"] = vault_root
            captured["dependency_owner"] = dependency_owner
            captured["forwarded_args"] = forwarded_args
            captured["script_path"] = script_path
            captured["required_modules"] = required_modules
            raise _ReexecCalled

        monkeypatch.setattr(check, "handoff_current_script_to_managed_runtime", fake_handoff)
        monkeypatch.setattr(sys, "argv", ["check.py", "--vault", str(tmp_path)])

        with pytest.raises(_ReexecCalled):
            check.main()

        assert captured["vault_root"] == str(tmp_path)
        assert captured["dependency_owner"] == "check.py"
        assert captured["required_modules"] == ("mcp",)
        assert captured["script_path"].endswith("check.py")
        assert captured["forwarded_args"] == ["--vault", str(tmp_path)]

    def test_main_emits_bootstrap_failure_result_when_runtime_bootstrap_fails(
        self, vault, monkeypatch, capsys
    ):
        tmp_path, _router = vault
        monkeypatch.setattr(
            check,
            "handoff_current_script_to_managed_runtime",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("managed runtime unavailable")),
        )
        monkeypatch.setattr(sys, "argv", ["check.py", "--vault", str(tmp_path), "--json"])

        with pytest.raises(SystemExit) as excinfo:
            check.main()

        assert excinfo.value.code == 2
        payload = json.loads(capsys.readouterr().out)
        assert payload["summary"] == {"errors": 1, "warnings": 0, "info": 0}
        assert payload["findings"][0]["check"] == "runtime:bootstrap-failed"
        assert payload["findings"][0]["severity"] == "error"
        assert payload["findings"][0]["message"] == "managed runtime unavailable"
        assert payload["findings"][0]["repair"]["scope"] == "runtime"
