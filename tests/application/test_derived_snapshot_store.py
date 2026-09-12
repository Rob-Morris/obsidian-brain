"""Long-lived derived-state snapshot cache contracts."""

from __future__ import annotations

import _command_interface.derived_snapshots as snapshots
import pytest


def test_router_and_index_snapshots_reparse_only_after_file_change(
    tmp_path,
    monkeypatch,
):
    root = tmp_path.resolve()
    router_path = root / ".brain/local/compiled-router.json"
    index_path = root / ".brain/local/retrieval-index.json"
    router_path.parent.mkdir(parents=True)
    router_path.write_text("router-one", encoding="utf-8")
    index_path.write_text("index-one", encoding="utf-8")
    calls = {"router": 0, "index": 0}

    def load_router(_root):
        calls["router"] += 1
        return {"router": router_path.read_text(encoding="utf-8")}

    def load_index(_root):
        calls["index"] += 1
        return {"index": index_path.read_text(encoding="utf-8")}

    monkeypatch.setattr(snapshots, "load_compiled_router", load_router)
    monkeypatch.setattr(snapshots, "load_index", load_index)
    store = snapshots.FileDerivedSnapshotStore(root)

    first_router = store.load_router()
    first_index = store.load_lexical_index()
    assert store.load_router() is first_router
    assert store.load_lexical_index() is first_index
    assert calls == {"router": 1, "index": 1}

    router_path.write_text("router-two-longer", encoding="utf-8")
    index_path.write_text("index-two-longer", encoding="utf-8")

    assert store.load_router() == {"router": "router-two-longer"}
    assert store.load_lexical_index() == {"index": "index-two-longer"}
    assert calls == {"router": 2, "index": 2}


def test_explicit_invalidation_drops_only_named_snapshot(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    derived = root / ".brain/local"
    derived.mkdir(parents=True)
    (derived / "compiled-router.json").write_text("router", encoding="utf-8")
    (derived / "retrieval-index.json").write_text("index", encoding="utf-8")
    calls = {"router": 0, "index": 0}

    monkeypatch.setattr(
        snapshots,
        "load_compiled_router",
        lambda _root: {"load": calls.__setitem__("router", calls["router"] + 1)},
    )
    monkeypatch.setattr(
        snapshots,
        "load_index",
        lambda _root: {"load": calls.__setitem__("index", calls["index"] + 1)},
    )
    store = snapshots.FileDerivedSnapshotStore(root)
    store.load_router()
    store.load_lexical_index()

    store.invalidate("router")
    store.load_router()
    store.load_lexical_index()

    assert calls == {"router": 2, "index": 1}


def test_snapshot_publication_waits_for_matching_content_and_signature(
    tmp_path,
    monkeypatch,
):
    root = tmp_path.resolve()
    router_path = root / ".brain/local/compiled-router.json"
    router_path.parent.mkdir(parents=True)
    router_path.write_text("A", encoding="utf-8")
    replacements = iter(("B-longer", "C-longest", None))
    calls = []

    def load_router(_root):
        value = router_path.read_text(encoding="utf-8")
        calls.append(value)
        replacement = next(replacements)
        if replacement is not None:
            router_path.write_text(replacement, encoding="utf-8")
        return {"router": value}

    monkeypatch.setattr(snapshots, "load_compiled_router", load_router)
    store = snapshots.FileDerivedSnapshotStore(root)

    assert store.load_router() == {"router": "C-longest"}
    assert calls == ["A", "B-longer", "C-longest"]
    assert store.load_router() == {"router": "C-longest"}
    assert calls == ["A", "B-longer", "C-longest"]


def test_unstable_snapshot_fails_without_publishing_mismatched_state(
    tmp_path,
    monkeypatch,
):
    root = tmp_path.resolve()
    router_path = root / ".brain/local/compiled-router.json"
    router_path.parent.mkdir(parents=True)
    router_path.write_text("seed", encoding="utf-8")
    calls = []

    def load_router(_root):
        value = router_path.read_text(encoding="utf-8")
        calls.append(value)
        router_path.write_text(value + "-changed", encoding="utf-8")
        return {"router": value}

    monkeypatch.setattr(snapshots, "load_compiled_router", load_router)
    store = snapshots.FileDerivedSnapshotStore(root)

    with pytest.raises(snapshots.DerivedSnapshotChangedError, match="3 consecutive"):
        store.load_router()
    assert len(calls) == 3
    assert store._cache == {}


def test_callers_cannot_mutate_cached_nested_snapshot_state(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    router_path = root / ".brain/local/compiled-router.json"
    router_path.parent.mkdir(parents=True)
    router_path.write_text("stable", encoding="utf-8")
    calls = []

    def load_router(_root):
        calls.append("load")
        return {"nested": {"values": ["original"]}}

    monkeypatch.setattr(snapshots, "load_compiled_router", load_router)
    store = snapshots.FileDerivedSnapshotStore(root)

    first = store.load_router()
    with pytest.raises(TypeError, match="read-only"):
        first["nested"]["values"].append("caller-change")
    with pytest.raises(TypeError, match="read-only"):
        first["nested"] = {"values": []}

    assert store.load_router() == {"nested": {"values": ["original"]}}
    assert store.load_router() is first
    assert calls == ["load"]
