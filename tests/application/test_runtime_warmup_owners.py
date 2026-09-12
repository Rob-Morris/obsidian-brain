"""Shared runtime status and warm-up command contracts."""

from __future__ import annotations

import json

from _application.registry import current_application_catalogue, current_request_resolver
from _application.runtime.status import RuntimeStatusRequest
from _application.runtime.warmup import RuntimeWarmupRequest, WarmupRequestOutcome
from _application.types import Authority, DependencyTier, EffectClass, RetryClass
from _bootstrap import readiness
from command_application import application_for


def test_runtime_status_is_pure_when_no_state_has_been_recorded(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()

    snapshot = readiness.read_runtime_status(root)

    assert snapshot["state"] == "cold"
    assert not (root / ".brain").exists()


def test_runtime_warmup_starts_once_and_concurrent_requests_join(
    command_vault_clone,
    monkeypatch,
):
    status_path = command_vault_clone.vault_root / ".brain/local/runtime-status.json"
    status_path.unlink(missing_ok=True)
    starts = []
    monkeypatch.setattr(
        readiness,
        "_spawn_worker",
        lambda root, run_id: starts.append((root, run_id)),
    )
    application = application_for(command_vault_clone.vault_root)

    first = application.invoke(RuntimeWarmupRequest())
    second = application.invoke(RuntimeWarmupRequest())
    observed = application.invoke(RuntimeStatusRequest())

    assert first.result.request_outcome is WarmupRequestOutcome.STARTED
    assert second.result.request_outcome is WarmupRequestOutcome.ALREADY_RUNNING
    assert len(starts) == 1
    assert first.result.runtime_status == second.result.runtime_status
    assert second.result.runtime_status == observed.result.runtime_status


def test_runtime_warmup_worker_materialises_ready_derived_state(
    command_vault_clone,
    monkeypatch,
):
    root = command_vault_clone.vault_root
    status_path = root / ".brain/local/runtime-status.json"
    status_path.unlink(missing_ok=True)
    monkeypatch.setattr(readiness, "_spawn_worker", lambda *_args: None)
    outcome, _snapshot = readiness.ensure_runtime_warmup(root, retry_failed=True)
    run_id = json.loads(status_path.read_text(encoding="utf-8"))["run_id"]

    readiness._run_worker(root, run_id)

    completed = readiness.read_runtime_status(root)
    assert outcome == "started"
    assert completed["state"] == "ready"
    assert completed["components"]["router"] == "ready"
    assert completed["components"]["lexical"] == "ready"
    assert completed["components"]["semantic"] in {"disabled", "ready", "deferred"}


def test_runtime_commands_have_distinct_effect_contracts():
    entries = {
        entry.command_id: entry
        for entry in current_application_catalogue().entries
        if entry.command_id in {"runtime.status", "runtime.warmup"}
    }

    assert entries["runtime.status"].dependency_tier is DependencyTier.BOOTSTRAP
    assert entries["runtime.status"].authority is Authority.READER
    assert entries["runtime.status"].effect_class is EffectClass.NONE
    assert entries["runtime.warmup"].dependency_tier is DependencyTier.BOOTSTRAP
    assert entries["runtime.warmup"].authority is Authority.READER
    assert entries["runtime.warmup"].effect_class is EffectClass.DERIVED_CACHE_WRITE
    assert entries["runtime.warmup"].retry_class is RetryClass.SAFE


def test_runtime_transports_are_empty_and_strict():
    resolver = current_request_resolver()

    assert type(resolver.resolve("runtime.status", {})) is RuntimeStatusRequest
    assert type(resolver.resolve("runtime.warmup", {})) is RuntimeWarmupRequest


def test_explicit_warmup_retries_ready_with_deferred_semantics(
    command_vault_clone, monkeypatch
):
    root = command_vault_clone.vault_root
    state = readiness._state_document(
        root,
        run_id="old",
        state="ready",
        phase=None,
        router="ready",
        lexical="ready",
        semantic="deferred",
        started_at=readiness._now(),
        last_error=None,
    )
    readiness._write_state(root, state)
    starts = []
    monkeypatch.setattr(readiness, "_spawn_worker", lambda *args: starts.append(args))
    assert (
        readiness.ensure_runtime_warmup(root, retry_failed=False)[0] == "already_ready"
    )
    assert readiness.ensure_runtime_warmup(root, retry_failed=True)[0] == "started"
    assert len(starts) == 1


def test_semantic_warmup_selects_managed_interpreter(command_vault_clone, monkeypatch):
    from pathlib import Path
    from _lifecycle import fresh_interpreter
    from _bootstrap import runtime
    import _common._venv as venv
    import _semantic.config as config

    root = command_vault_clone.vault_root
    python = root / "managed/bin/python"
    calls = []
    monkeypatch.setattr(config, "embeddings_enabled", lambda _root: True)
    monkeypatch.setattr(venv, "find_existing_central_venv", lambda _root: python)
    monkeypatch.setattr(
        runtime, "probe_python", lambda path, **kw: {"ok": path == str(python)}
    )

    def run(owner, vault, **kwargs):
        calls.append((owner.__module__, Path(vault), kwargs))
        return {"state": "ready"}

    monkeypatch.setattr(fresh_interpreter, "run_lifecycle_in_fresh_interpreter", run)
    assert readiness._warm_semantic(root, None) == ("ready", None)
    assert calls == [
        (
            "_lifecycle.runtime_warmup",
            root,
            {"python_executable": python, "timeout": 480},
        )
    ]


def test_managed_warmup_rejects_missing_model_even_with_current_sidecars(
    command_vault_clone, monkeypatch
):
    import pytest
    from _lifecycle.runtime_warmup import warm_semantic
    import _semantic.runtime as semantic_runtime

    monkeypatch.setattr(
        semantic_runtime,
        "load_embeddings_state",
        lambda *_args: pytest.fail(
            "Missing model must be checked before accepting old sidecars"
        ),
    )
    with pytest.raises(RuntimeError, match="model is unavailable"):
        warm_semantic(command_vault_clone.vault_root)
