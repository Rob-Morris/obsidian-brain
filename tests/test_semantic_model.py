"""Tests for the vault-local semantic model lifecycle helpers."""

from __future__ import annotations

import builtins
import json
import os
from pathlib import Path
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


def _write_snapshot(snapshot_path, *, pooling='{"pooling_mode_mean_tokens": true}'):
    (snapshot_path / "onnx").mkdir(parents=True, exist_ok=True)
    (snapshot_path / "1_Pooling").mkdir(exist_ok=True)
    (snapshot_path / "onnx" / "model.onnx").write_bytes(b"stub")
    (snapshot_path / "tokenizer.json").write_text("{}", encoding="utf-8")
    (snapshot_path / "sentence_bert_config.json").write_text(
        '{"max_seq_length": 256}', encoding="utf-8"
    )
    (snapshot_path / "1_Pooling" / "config.json").write_text(pooling, encoding="utf-8")


class _FakeTensor:
    def __init__(self, name, shape):
        self.name = name
        self.shape = shape


class _FakeSession:
    """Emit token embedding (t + 1, 2.0) for token position t, in two dimensions."""

    calls: list = []

    def __init__(self, path, providers):
        _FakeSession.calls.append((path, providers))

    def get_inputs(self):
        return [_FakeTensor(name, [None, None]) for name in ("input_ids", "attention_mask", "token_type_ids")]

    def get_outputs(self):
        return [_FakeTensor("last_hidden_state", [None, None, 2])]

    def run(self, _outputs, feeds):
        import numpy as np

        assert set(feeds) == {"input_ids", "attention_mask", "token_type_ids"}
        batch, length = feeds["input_ids"].shape
        positions = np.arange(1, length + 1, dtype=np.float32)
        hidden = np.stack([positions, np.full(length, 2.0, dtype=np.float32)], axis=1)
        return [np.broadcast_to(hidden, (batch, length, 2)).copy()]


class _FakeEncoding:
    def __init__(self, ids, attention_mask):
        self.ids = ids
        self.attention_mask = attention_mask


class _FakeTokenizer:
    """One token per character, padded to the longest text in the batch."""

    calls: dict = {}

    @classmethod
    def from_file(cls, path):
        cls.calls["path"] = path
        return cls()

    def enable_truncation(self, max_length):
        _FakeTokenizer.calls["truncation"] = max_length

    def enable_padding(self):
        _FakeTokenizer.calls["padding"] = True

    def encode_batch(self, texts):
        longest = max(len(text) for text in texts)
        return [
            _FakeEncoding(
                list(range(1, len(text) + 1)) + [0] * (longest - len(text)),
                [1] * len(text) + [0] * (longest - len(text)),
            )
            for text in texts
        ]


def _install_fake_runtime(monkeypatch):
    _FakeSession.calls = []
    _FakeTokenizer.calls = {}
    fake_ort = types.ModuleType("onnxruntime")
    fake_ort.InferenceSession = _FakeSession
    fake_tokenizers = types.ModuleType("tokenizers")
    fake_tokenizers.Tokenizer = _FakeTokenizer
    monkeypatch.setitem(sys.modules, "onnxruntime", fake_ort)
    monkeypatch.setitem(sys.modules, "tokenizers", fake_tokenizers)


def test_load_local_encoder_pins_cpu_provider_and_snapshot_files(tmp_path, monkeypatch):
    snapshot_path = tmp_path / "snapshot"
    _write_snapshot(snapshot_path)
    _install_fake_runtime(monkeypatch)

    encoder = semantic_model._load_local_encoder(snapshot_path)

    assert _FakeSession.calls == [
        (str(snapshot_path / "onnx" / "model.onnx"), ["CPUExecutionProvider"]),
    ]
    assert _FakeTokenizer.calls == {
        "path": str(snapshot_path / "tokenizer.json"),
        "truncation": 256,
        "padding": True,
    }
    assert encoder.dimension == 2


def test_load_local_encoder_rejects_incomplete_snapshot(tmp_path, monkeypatch):
    snapshot_path = tmp_path / "snapshot"
    _write_snapshot(snapshot_path)
    (snapshot_path / "onnx" / "model.onnx").unlink()
    _install_fake_runtime(monkeypatch)

    with pytest.raises(semantic_model.SemanticModelLoadError, match="missing onnx/model.onnx"):
        semantic_model._load_local_encoder(snapshot_path)
    assert _FakeSession.calls == []


def test_load_local_encoder_rejects_non_mean_pooling(tmp_path, monkeypatch):
    snapshot_path = tmp_path / "snapshot"
    _write_snapshot(snapshot_path, pooling='{"pooling_mode_cls_token": true}')
    _install_fake_runtime(monkeypatch)

    with pytest.raises(semantic_model.SemanticModelLoadError, match="mean pooling"):
        semantic_model._load_local_encoder(snapshot_path)


def test_load_local_encoder_wraps_runtime_load_failures(tmp_path, monkeypatch):
    snapshot_path = tmp_path / "snapshot"
    _write_snapshot(snapshot_path)
    _install_fake_runtime(monkeypatch)

    class ExplodingSession:
        def __init__(self, _path, providers):
            raise Exception("INVALID_PROTOBUF")

    sys.modules["onnxruntime"].InferenceSession = ExplodingSession

    with pytest.raises(semantic_model.SemanticModelLoadError, match="INVALID_PROTOBUF"):
        semantic_model._load_local_encoder(snapshot_path)


@pytest.mark.semantic
def test_local_sentence_encoder_mean_pools_masked_tokens_then_normalises(tmp_path, monkeypatch):
    np = pytest.importorskip("numpy")
    snapshot_path = tmp_path / "snapshot"
    _write_snapshot(snapshot_path)
    _install_fake_runtime(monkeypatch)
    encoder = semantic_model._load_local_encoder(snapshot_path)

    vectors = encoder.encode(["ab", "abc"])

    # "ab" pads to length 3 with mask [1, 1, 0]: mean of (1, 2) and (2, 2) is (1.5, 2).
    # "abc" has mask [1, 1, 1]: mean of (1, 2), (2, 2), (3, 2) is (2, 2).
    assert vectors.dtype == np.float32
    assert vectors.shape == (2, 2)
    np.testing.assert_allclose(vectors[0], [0.6, 0.8], rtol=1e-6)
    np.testing.assert_allclose(vectors[1], [2 ** -0.5, 2 ** -0.5], rtol=1e-6)

    raw = encoder.encode(["ab"], normalize_embeddings=False)
    np.testing.assert_allclose(raw[0], [1.5, 2.0], rtol=1e-6)


@pytest.mark.semantic
def test_local_encoder_matches_sentence_transformers_reference_vectors():
    """Opt-in parity gate against vectors recorded from the torch pipeline.

    Set BRAIN_TEST_SEMANTIC_SNAPSHOT to a provisioned snapshot directory for the
    shipped model pin to run it; it needs the real 86 MB ONNX export.
    """
    snapshot = os.environ.get("BRAIN_TEST_SEMANTIC_SNAPSHOT")
    if not snapshot:
        pytest.skip("BRAIN_TEST_SEMANTIC_SNAPSHOT is not set")
    np = pytest.importorskip("numpy")
    pytest.importorskip("onnxruntime")
    pytest.importorskip("tokenizers")
    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "semantic" / "minilm_l6_v2_reference_vectors.json")
        .read_text(encoding="utf-8")
    )
    assert fixture["revision"] == semantic_model.SHIPPED_MODEL_REVISION

    encoder = semantic_model._load_local_encoder(Path(snapshot))
    vectors = encoder.encode(fixture["texts"])
    reference = np.array(fixture["vectors"], dtype=np.float32)

    assert encoder.dimension == semantic_runtime.EMBEDDING_DIM
    cosines = (vectors * reference).sum(axis=1)
    assert cosines.min() >= 0.9999, cosines


def test_load_local_model_loads_the_manifest_snapshot(tmp_path, monkeypatch):
    vault = _make_vault(tmp_path)
    semantic_model.write_manifest(vault, _manifest())
    snapshot_path = semantic_model.model_snapshot_path(
        vault,
        semantic_model.SHIPPED_MODEL_NAME,
        semantic_model.SHIPPED_MODEL_REVISION,
    )
    snapshot_path.mkdir(parents=True)
    loaded = []
    monkeypatch.setattr(
        semantic_model,
        "_load_local_encoder",
        lambda path: loaded.append(path) or object(),
    )

    semantic_model.load_local_model(vault)

    assert loaded == [snapshot_path]


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
            raise ModuleNotFoundError("numpy unavailable", name="numpy")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    assert semantic_runtime.load_embeddings_state(vault) == (None, None, None)


def test_load_embeddings_state_reports_broken_numpy_import(tmp_path, monkeypatch):
    vault = _make_vault(tmp_path)
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "numpy":
            raise ImportError("native extension failed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(
        semantic_runtime.SemanticEmbeddingsLoadError,
        match="semantic NumPy dependency failed to import",
    ):
        semantic_runtime.load_embeddings_state(vault)


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


@pytest.mark.semantic
def test_load_embeddings_state_skips_unrequested_arrays(tmp_path):
    np = pytest.importorskip("numpy")
    vault = _make_vault(tmp_path)
    meta_path = vault / semantic_runtime.EMBEDDINGS_META_REL
    type_path = vault / semantic_runtime.TYPE_EMBEDDINGS_REL
    doc_path = vault / semantic_runtime.DOC_EMBEDDINGS_REL
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text('{"documents": [], "types": []}', encoding="utf-8")

    np.save(type_path, np.array([[1.0]]))
    doc_path.write_bytes(b"unreadable but unrequested")
    type_embeddings, doc_embeddings, metadata = semantic_runtime.load_embeddings_state(
        vault,
        selection="types",
    )
    assert type_embeddings.shape == (1, 1)
    assert doc_embeddings is None
    assert metadata == {"documents": [], "types": []}

    type_path.write_bytes(b"unreadable but unrequested")
    np.save(doc_path, np.array([[2.0]]))
    type_embeddings, doc_embeddings, metadata = semantic_runtime.load_embeddings_state(
        vault,
        selection="documents",
    )
    assert type_embeddings is None
    assert doc_embeddings.shape == (1, 1)
    assert metadata == {"documents": [], "types": []}

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

    monkeypatch.setattr(semantic_model, "_load_local_encoder", fake_load)

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
    monkeypatch.setattr(semantic_model, "_load_local_encoder", fake_load)

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
    monkeypatch.setattr(semantic_model, "_load_local_encoder", lambda _path: object())

    outcome = semantic_model.provision_semantic_model(vault)

    assert outcome.downloaded is True
    assert outcome.notes
    assert "unreadable semantic model manifest" in outcome.notes[0]
