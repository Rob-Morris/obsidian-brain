"""Long-lived derived-state snapshot cache contracts."""

from __future__ import annotations

import _command_interface.derived_snapshots as snapshots


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
