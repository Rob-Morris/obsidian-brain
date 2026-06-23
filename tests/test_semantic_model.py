"""Tests for the vault-local semantic model lifecycle helpers."""

from __future__ import annotations

import builtins
import os
import sys
import types

import pytest

import _semantic.model as semantic_model
import _semantic.runtime as semantic_runtime


def _make_vault(tmp_path):
    (tmp_path / ".brain" / "local").mkdir(parents=True)
    return tmp_path


def _manifest():
    return semantic_model.ModelManifest(
        model_name=semantic_model.SHIPPED_MODEL_NAME,
        revision=semantic_model.SHIPPED_MODEL_REVISION,
        provisioned_at="2026-05-06T00:00:00+10:00",
    )


def test_manifest_round_trip_is_idempotent(tmp_path):
    vault = _make_vault(tmp_path)

    changed = semantic_model.write_manifest(vault, _manifest())
    assert changed is True
    assert semantic_model.read_manifest(vault) == _manifest()

    changed = semantic_model.write_manifest(vault, _manifest())
    assert changed is False


def test_inspect_model_state_flags_missing_manifest(tmp_path):
    vault = _make_vault(tmp_path)

    state = semantic_model.inspect_model_state(vault)

    assert state.manifest_missing is True
    assert state.model_path_missing is True
    assert state.model_revision_mismatch is False
    assert state.healthy is False


def test_inspect_model_state_treats_corrupt_manifest_as_load_error(tmp_path):
    vault = _make_vault(tmp_path)
    semantic_model.manifest_path(vault).write_text("{not-json", encoding="utf-8")

    state = semantic_model.inspect_model_state(vault)

    assert state.manifest_missing is False
    assert state.load_error is not None
    assert "manifest" in state.load_error


def test_load_sentence_transformer_uses_local_files_only(tmp_path, monkeypatch):
    snapshot_path = tmp_path / "snapshot"
    snapshot_path.mkdir()
    calls = []

    class FakeSentenceTransformer:
        def __init__(self, path, **kwargs):
            calls.append((path, kwargs))

    fake_module = types.ModuleType("sentence_transformers")
    fake_module.SentenceTransformer = FakeSentenceTransformer
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)

    semantic_model._load_sentence_transformer(snapshot_path)

    assert calls == [
        (str(snapshot_path), {"local_files_only": True}),
    ]


def test_load_local_model_stays_local_under_offline_env(tmp_path, monkeypatch):
    vault = _make_vault(tmp_path)
    semantic_model.write_manifest(vault, _manifest())
    snapshot_path = semantic_model.model_snapshot_path(
        vault,
        semantic_model.SHIPPED_MODEL_NAME,
        semantic_model.SHIPPED_MODEL_REVISION,
    )
    snapshot_path.mkdir(parents=True)
    calls = []

    class FakeSentenceTransformer:
        def __init__(self, path, **kwargs):
            calls.append((path, kwargs))

    fake_module = types.ModuleType("sentence_transformers")
    fake_module.SentenceTransformer = FakeSentenceTransformer
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    semantic_model.load_local_model(vault)

    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert os.environ["TRANSFORMERS_OFFLINE"] == "1"
    assert calls == [
        (str(snapshot_path), {"local_files_only": True}),
    ]


def test_embeddings_sidecars_match_manifest_keeps_present_honest_without_manifest(tmp_path):
    vault = _make_vault(tmp_path)
    for rel in (
        semantic_runtime.TYPE_EMBEDDINGS_REL,
        semantic_runtime.DOC_EMBEDDINGS_REL,
        semantic_runtime.EMBEDDINGS_META_REL,
    ):
        path = vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"stub") if path.suffix == ".npy" else path.write_text("{}", encoding="utf-8")

    present, outdated = semantic_runtime.embeddings_sidecars_match_manifest(vault, None)

    assert present is True
    assert outdated is False

def test_load_embeddings_state_returns_none_without_numpy(tmp_path, monkeypatch):
    vault = _make_vault(tmp_path)
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "numpy":
            raise ImportError("numpy unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    assert semantic_runtime.load_embeddings_state(vault) == (None, None, None)


@pytest.mark.semantic
def test_load_embeddings_state_raises_on_corrupt_meta(tmp_path):
    pytest.importorskip("numpy")
    vault = _make_vault(tmp_path)
    meta_path = vault / semantic_runtime.EMBEDDINGS_META_REL
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(
        semantic_runtime.SemanticEmbeddingsLoadError,
        match="semantic embeddings metadata is unreadable",
    ):
        semantic_runtime.load_embeddings_state(vault)


@pytest.mark.semantic
def test_load_embeddings_state_raises_when_meta_is_not_a_json_object(tmp_path):
    pytest.importorskip("numpy")
    vault = _make_vault(tmp_path)
    meta_path = vault / semantic_runtime.EMBEDDINGS_META_REL
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text('["not", "an", "object"]', encoding="utf-8")

    with pytest.raises(
        semantic_runtime.SemanticEmbeddingsLoadError,
        match=r"semantic embeddings metadata at .* is not a JSON object",
    ):
        semantic_runtime.load_embeddings_state(vault)


@pytest.mark.semantic
def test_load_embeddings_state_raises_on_corrupt_document_array(tmp_path):
    pytest.importorskip("numpy")
    vault = _make_vault(tmp_path)
    meta_path = vault / semantic_runtime.EMBEDDINGS_META_REL
    doc_path = vault / semantic_runtime.DOC_EMBEDDINGS_REL
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text('{"documents": [], "types": []}', encoding="utf-8")
    doc_path.write_bytes(b"not-a-numpy-array")

    with pytest.raises(
        semantic_runtime.SemanticEmbeddingsLoadError,
        match="semantic document embeddings are unreadable",
    ):
        semantic_runtime.load_embeddings_state(vault)

def test_router_source_hash_reads_string_from_router_meta():
    assert semantic_runtime.router_source_hash({"meta": {"source_hash": "sha256:router"}}) == "sha256:router"
    assert semantic_runtime.router_source_hash({"meta": {}}) is None
    with pytest.raises(
        semantic_runtime.RouterMetadataError,
        match="compiled router meta.source_hash must be a string",
    ):
        semantic_runtime.router_source_hash({"meta": {"source_hash": 123}})
    with pytest.raises(
        semantic_runtime.RouterMetadataError,
        match="compiled router metadata must be a JSON object containing source_hash",
    ):
        semantic_runtime.router_source_hash({})
    with pytest.raises(
        semantic_runtime.RouterMetadataError,
        match="compiled router must be a JSON object with a meta.source_hash field",
    ):
        semantic_runtime.router_source_hash(None)


def test_embeddings_meta_matches_router_uses_source_hash_fingerprint():
    router = {"meta": {"source_hash": "sha256:router"}}

    assert (
        semantic_runtime.embeddings_meta_matches_router(
            {semantic_runtime.ROUTER_SOURCE_HASH_KEY: "sha256:router"},
            router,
        )
        is True
    )
    assert (
        semantic_runtime.embeddings_meta_matches_router(
            {semantic_runtime.ROUTER_SOURCE_HASH_KEY: "sha256:other"},
            router,
        )
        is False
    )
    assert semantic_runtime.embeddings_meta_matches_router({}, router) is False


def test_get_query_encoder_cache_is_vault_scoped(tmp_path, monkeypatch):
    vault_a = _make_vault(tmp_path / "vault-a")
    vault_b = _make_vault(tmp_path / "vault-b")
    manifest = _manifest()
    semantic_model.write_manifest(vault_a, manifest)
    semantic_model.write_manifest(vault_b, manifest)

    path_a = semantic_model.model_snapshot_path(
        vault_a,
        semantic_model.SHIPPED_MODEL_NAME,
        semantic_model.SHIPPED_MODEL_REVISION,
    )
    path_b = semantic_model.model_snapshot_path(
        vault_b,
        semantic_model.SHIPPED_MODEL_NAME,
        semantic_model.SHIPPED_MODEL_REVISION,
    )
    path_a.mkdir(parents=True, exist_ok=True)
    path_b.mkdir(parents=True, exist_ok=True)

    load_calls = []

    def fake_load(snapshot_path):
        load_calls.append(str(snapshot_path))
        return object()

    monkeypatch.setattr(semantic_model, "_load_sentence_transformer", fake_load)

    semantic_model.clear_query_encoder()
    try:
        encoder_a = semantic_model.get_query_encoder(vault_a)
        encoder_a_again = semantic_model.get_query_encoder(vault_a)
        encoder_b = semantic_model.get_query_encoder(vault_b)
    finally:
        semantic_model.clear_query_encoder()

    assert encoder_a is encoder_a_again
    assert encoder_a is not encoder_b
    assert load_calls == [str(path_a), str(path_b)]


def test_provision_semantic_model_downloads_then_becomes_noop(tmp_path, monkeypatch):
    vault = _make_vault(tmp_path)
    calls = {"download": 0, "load": 0}
    expected_path = semantic_model.model_snapshot_path(
        vault,
        semantic_model.SHIPPED_MODEL_NAME,
        semantic_model.SHIPPED_MODEL_REVISION,
    )

    def fake_download(model_name, revision, snapshot_path):
        calls["download"] += 1
        assert model_name == semantic_model.SHIPPED_MODEL_NAME
        assert revision == semantic_model.SHIPPED_MODEL_REVISION
        snapshot_path.mkdir(parents=True, exist_ok=True)
        (snapshot_path / "config.json").write_text("{}\n")

    def fake_load(snapshot_path):
        calls["load"] += 1
        assert snapshot_path == expected_path
        return object()

    monkeypatch.setattr(semantic_model, "_download_snapshot", fake_download)
    monkeypatch.setattr(semantic_model, "_load_sentence_transformer", fake_load)

    first = semantic_model.provision_semantic_model(vault)
    second = semantic_model.provision_semantic_model(vault)

    assert first.downloaded is True
    assert first.manifest_changed is True
    assert second.downloaded is False
    assert second.manifest_changed is False
    assert calls == {"download": 1, "load": 2}


def test_provision_semantic_model_records_replaced_manifest_note(tmp_path, monkeypatch):
    vault = _make_vault(tmp_path)
    semantic_model.manifest_path(vault).write_text("{not-json", encoding="utf-8")

    def fake_download(_model_name, _revision, snapshot_path):
        snapshot_path.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(semantic_model, "_download_snapshot", fake_download)
    monkeypatch.setattr(semantic_model, "_load_sentence_transformer", lambda _path: object())

    outcome = semantic_model.provision_semantic_model(vault)

    assert outcome.downloaded is True
    assert outcome.notes
    assert "unreadable semantic model manifest" in outcome.notes[0]
