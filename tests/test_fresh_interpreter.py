"""Tests for running lifecycle owners in a fresh interpreter."""

from __future__ import annotations

import pytest

from _lifecycle import fresh_interpreter


def test_run_lifecycle_returns_the_owner_result_from_a_child_process(tmp_path):
    result = fresh_interpreter.run_lifecycle_in_fresh_interpreter(
        "_lifecycle.retrieval_assets:rebuild_semantic_assets",
        tmp_path,
        dry_run=True,
    )

    assert result["status"] == "planned"
    assert result["dry_run"] is True
    assert [step["name"] for step in result["steps"]] == ["semantic_assets"]


def test_run_lifecycle_rejects_targets_outside_the_lifecycle_package(tmp_path, monkeypatch):
    monkeypatch.setattr(
        fresh_interpreter.subprocess,
        "run",
        lambda *_a, **_k: pytest.fail("no interpreter should start for an invalid target"),
    )

    with pytest.raises(ValueError, match="_lifecycle.<module>:<function>"):
        fresh_interpreter.run_lifecycle_in_fresh_interpreter("os:system", tmp_path)
    with pytest.raises(ValueError):
        fresh_interpreter.run_lifecycle_in_fresh_interpreter("_lifecycle.retrieval_assets", tmp_path)


def test_run_lifecycle_surfaces_owner_exceptions_as_typed_errors(tmp_path):
    with pytest.raises(
        fresh_interpreter.FreshInterpreterError,
        match="rebuild_semantic_assets failed with",
    ):
        fresh_interpreter.run_lifecycle_in_fresh_interpreter(
            "_lifecycle.retrieval_assets:rebuild_semantic_assets",
            tmp_path / "not-a-vault",
            dry_run=False,
        )


def test_run_lifecycle_reports_missing_reply(tmp_path, monkeypatch):
    class Completed:
        returncode = 1
        stdout = ""
        stderr = "Traceback: boom"

    monkeypatch.setattr(fresh_interpreter.subprocess, "run", lambda *_a, **_k: Completed())

    with pytest.raises(
        fresh_interpreter.FreshInterpreterError,
        match=r"returned no result \(exit 1\): Traceback: boom",
    ):
        fresh_interpreter.run_lifecycle_in_fresh_interpreter(
            "_lifecycle.retrieval_assets:rebuild_semantic_assets",
            tmp_path,
            dry_run=True,
        )
