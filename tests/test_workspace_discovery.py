"""Workspace discovery across active retrieval and historical listing."""

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from _common._workspace import (
    WorkspaceMatch, canonical_query_workspace, workspace_discovery_metadata,
)
from _search.filters import SearchFilters
from _search import index as lexical_index, lexical_query, semantic_query, hybrid_query
from _semantic import assets, runtime, model
from _portable.artefact_listing import list_artefacts_page, list_combined_from_vault
from _application.artefact.list import ArtefactListRequest, decode as decode_list
from _application.artefact.search import ArtefactSearchRequest, decode as decode_search
from brain_test_support import make_searchable_vault


@pytest.mark.parametrize("value", ["barbican", " workspace/barbican", "workspace/Barbican", "workspace/barbican ", "workspace/a/b", "workspace/123", "workspace/../a", "/tmp/workspace/barbican", "[[workspace/barbican]]", "workspace/barbican.md", "workspace/" + "a" * 65, 4, []])
def test_strict_symbolic_filter(value):
    with pytest.raises(ValueError):
        canonical_query_workspace(value)
    for decode, extra in [(decode_list, {}), (decode_search, {"query": "bug"})]:
        with pytest.raises(ValueError):
            decode({**extra, "workspace": value})


@pytest.mark.parametrize("key", ["closed-workspace", "missing-workspace", "barbican"])
def test_filter_does_not_resolve_target(key):
    reference = "workspace/" + key
    assert decode_list({"workspace": reference}).workspace == reference
    assert decode_search({"query": "bug", "workspace": reference}).workspace == reference


def test_shared_projection_separates_membership_from_association():
    fields = {"workspace": "workspace/barbican", "tags": ["workspace/subcortex", "workspace/subcortex", "workspace/barbican", "workspace/Wrong", "workspace/a/b", "project/barbican"]}
    assert workspace_discovery_metadata(fields) == {
        "primary_workspace": "workspace/barbican",
        "relevant_workspaces": ["workspace/barbican", "workspace/subcortex"],
    }
    fields.pop("workspace")
    assert workspace_discovery_metadata(fields)["primary_workspace"] is None
    assert workspace_discovery_metadata({"type": "living/workspace", "key": "barbican"})["primary_workspace"] == "workspace/barbican"
    assert workspace_discovery_metadata({"tags": "workspace/subcortex"})["relevant_workspaces"] == []


@pytest.fixture
def corpus(tmp_path):
    vault = make_searchable_vault(tmp_path)
    (vault / "_Config/router.md").write_text("Always:\n- Safe test sources.\n\nConditional:\n")
    for path in vault.rglob("*.md"):
        if path.parts[-2] in {"Wiki", "Designs", "Logs"}:
            path.unlink()
    docs = [
        ("Wiki/Bug.md", "living/wiki", "workspace/barbican", ["workspace/subcortex", "workspace/barbican"]),
        ("_Temporal/Logs/20261015-log~Bug.md", "temporal/logs", None, ["workspace/subcortex", "workspace/missing"]),
        ("Wiki/Other.md", "living/wiki", "workspace/other", []),
    ]
    for path, type_name, workspace, tags in docs:
        key = "other" if path.endswith("/Other.md") else "bug"
        (vault / path).write_text("---\n" + f"type: {type_name}\nkey: {key}\ncreated: 2026-10-15\nstatus: active\n" + (f"workspace: {workspace}\n" if workspace else "") + f"tags: {json.dumps(tags)}\n---\n\nA reproducible bug in the client.\n")
    return vault


class Encoder:
    def __init__(self, np):
        self.np = np
        self.calls = []

    def encode(self, texts, **kwargs):
        self.calls.append(list(texts))
        result = self.np.zeros((len(texts), runtime.EMBEDDING_DIM), dtype=self.np.float32)
        result[:, 0] = 1
        return result


@pytest.fixture
def semantic_stack(monkeypatch):
    np = pytest.importorskip("numpy")
    encoder = Encoder(np)
    manifest = model.ModelManifest(model.SHIPPED_MODEL_NAME, model.SHIPPED_MODEL_REVISION, "2026-10-15")
    monkeypatch.setattr(model, "load_local_model_with_manifest", lambda *_: (encoder, manifest))
    monkeypatch.setattr(model, "inspect_model_state", lambda *_: SimpleNamespace(manifest=manifest))
    return np, encoder


def build_semantic(vault):
    import compile_router

    built = lexical_index.build_index(vault)
    router = compile_router.compile(vault)
    compile_router.persist_compiled_router(vault, router)
    _, vectors, metadata = assets.build_embeddings(vault, router, built.index["documents"])
    return built, router, vectors, metadata


@pytest.mark.semantic
@pytest.mark.parametrize("workspace,match,count", [(None, WorkspaceMatch.RELEVANT, 3), ("workspace/subcortex", WorkspaceMatch.RELEVANT, 2), ("workspace/barbican", WorkspaceMatch.RELEVANT, 1), ("workspace/subcortex", WorkspaceMatch.PRIMARY, 0), ("workspace/barbican", WorkspaceMatch.PRIMARY, 1), ("workspace/missing", WorkspaceMatch.RELEVANT, 1)])
def test_list_lexical_semantic_hybrid_agree(corpus, semantic_stack, workspace, match, count):
    _, encoder = semantic_stack
    built, _, vectors, metadata = build_semantic(corpus)
    filters = SearchFilters(workspace=workspace, workspace_match=match)
    listed = list_artefacts_page(built.index, {}, workspace=workspace, workspace_match=match)["items"]
    lexical = lexical_query.search(built.index, "bug", corpus, filters=filters)
    semantic = semantic_query.search_semantic("bug", corpus, filters=filters, doc_embeddings=vectors, embeddings_meta=metadata, query_encoder=encoder)
    hybrid = hybrid_query.search_hybrid(built.index, "bug", corpus, filters=filters, doc_embeddings=vectors, embeddings_meta=metadata, query_encoder=encoder)
    expected = {item["path"] for item in listed}
    assert len(expected) == count
    for results in (lexical, semantic, hybrid):
        assert {item["path"] for item in results} == expected
        assert len(results) == count
        for item in results:
            listed_item = next(row for row in listed if row["path"] == item["path"])
            assert item["primary_workspace"] == listed_item["primary_workspace"]
            assert item["relevant_workspaces"] == listed_item["relevant_workspaces"]


@pytest.mark.parametrize("changed", [{"workspace": "workspace/barbican"}, {"workspace_match": WorkspaceMatch.PRIMARY}, {"location": "all"}, {"sort": "title"}])
def test_cursor_rejects_changed_query(corpus, changed):
    index = lexical_index.build_index(corpus).index
    first = list_artefacts_page(index, {}, workspace="workspace/subcortex", top_k=1)
    assert first["next_cursor"]
    second = list_artefacts_page(index, {}, workspace="workspace/subcortex", top_k=1, cursor=first["next_cursor"])
    assert len(second["items"]) == 1
    assert second["items"][0]["path"] != first["items"][0]["path"]
    with pytest.raises(ValueError, match="same list query"):
        list_artefacts_page(index, {}, cursor=first["next_cursor"], **{"workspace": "workspace/subcortex", **changed})


@pytest.mark.semantic
def test_archive_unarchive_active_retrieval_and_historical_listing(corpus, semantic_stack):
    _, encoder = semantic_stack
    bug = corpus / "Wiki/Bug.md"
    archived = corpus / "_Archive/Wiki/Bug.md"
    archived.parent.mkdir(parents=True)
    for action in (lambda: None, lambda: bug.rename(archived), lambda: archived.rename(bug)):
        action()
        built, _, vectors, metadata = build_semantic(corpus)
        active_count = 1 if bug.exists() else 0
        filters = SearchFilters(workspace="workspace/barbican")
        assert len(lexical_query.search(built.index, "bug", corpus, filters=filters)) == active_count
        assert len(semantic_query.search_semantic("bug", corpus, filters=filters, doc_embeddings=vectors, embeddings_meta=metadata, query_encoder=encoder)) == active_count
        assert len(hybrid_query.search_hybrid(built.index, "bug", corpus, filters=filters, doc_embeddings=vectors, embeddings_meta=metadata, query_encoder=encoder)) == active_count
        for location, expected in [("active", active_count), ("archived", 1-active_count), ("all", 1)]:
            page = list_combined_from_vault(corpus, router={"artefacts": []}, index=built.index, location=location, workspace="workspace/barbican")
            assert page["total"] == expected
            if expected:
                assert page["items"][0]["primary_workspace"] == "workspace/barbican"
                assert "workspace/subcortex" in page["items"][0]["relevant_workspaces"]


@pytest.mark.semantic
def test_metadata_refresh_reuses_vectors_and_matches_full_build(corpus, semantic_stack):
    np, encoder = semantic_stack
    built, router, vectors, metadata = build_semantic(corpus)
    encoder.calls.clear()
    bug = corpus / "Wiki/Bug.md"
    bug.write_text(bug.read_text().replace("workspace/barbican", "workspace/new-owner").replace("workspace/subcortex", "workspace/new-related"))
    lexical_index.index_update(built.index, corpus, "Wiki/Bug.md")
    full = lexical_index.build_index(corpus)
    assert sorted(built.index["documents"], key=lambda x: x["path"]) == sorted(full.index["documents"], key=lambda x: x["path"])
    _, refreshed_vectors, refreshed = assets.refresh_embeddings_outputs(corpus, router, built.index["documents"])
    assert encoder.calls == []
    row = next(doc for doc in refreshed["documents"] if doc["path"] == "Wiki/Bug.md")
    assert row["primary_workspace"] == "workspace/new-owner"
    assert row["relevant_workspaces"] == ["workspace/new-owner", "workspace/new-related"]
    _, full_vectors, full_meta = assets.build_embeddings(corpus, router, full.index["documents"])
    by_path = lambda meta: {doc["path"]: {k:v for k,v in doc.items() if k != "index"} for doc in meta["documents"]}
    assert by_path(refreshed) == by_path(full_meta)
    for i, doc in enumerate(refreshed["documents"]):
        j = next(j for j, old in enumerate(metadata["documents"]) if old["path"] == doc["path"])
        np.testing.assert_array_equal(refreshed_vectors[i], vectors[j])
    encoder.calls.clear()
    bug.write_text(bug.read_text() + "\nNew body content.\n")
    updated = lexical_index.build_index(corpus)
    assets.refresh_embeddings_outputs(corpus, router, updated.index["documents"])
    assert sum(map(len, encoder.calls)) == 1


def test_old_generations_refuse_then_refresh(corpus):
    from _lifecycle.derived_cache_state import inspect_lexical_cache
    built = lexical_index.build_index(corpus)
    built.index["meta"]["index_version"] = "1.1.0"
    lexical_index.persist_retrieval_index(corpus, built.index)
    with pytest.raises(ValueError, match="schema is outdated"):
        lexical_query.load_index(corpus)
    assert inspect_lexical_cache(corpus).stale
    built = lexical_index.build_index(corpus)
    lexical_index.persist_retrieval_index(corpus, built.index)
    assert lexical_query.load_index(corpus)["documents"][0]["relevant_workspaces"]
    router = {"meta": {"source_hash": "sha256:test"}}
    assert not runtime.embeddings_meta_matches_router({"router_source_hash": "sha256:test"}, router)
    assert runtime.embeddings_meta_matches_router({"metadata_version": runtime.EMBEDDINGS_METADATA_VERSION, "router_source_hash": "sha256:test"}, router)


def test_router_fingerprint_tracks_filter_metadata():
    import compile_router
    from _common._artefacts import living_artefact_index_entry
    fields = {"type": "living/wiki", "key": "bug", "workspace": "workspace/barbican", "tags": ["workspace/subcortex"]}
    definition = {"classification": "living", "key": "wiki", "frontmatter_type": "living/wiki"}
    row = living_artefact_index_entry(definition, "Wiki/Bug.md", fields)
    assert row["relevant_workspaces"] == ["workspace/barbican", "workspace/subcortex"]
    initial = compile_router._hash_index_payload(fields)
    assert compile_router._hash_index_payload({**fields, "tags": [*fields["tags"], "unrelated"]}) != initial
    assert compile_router._hash_index_payload({**fields, "body": "ignored"}) == initial
    assert compile_router._hash_index_payload({**fields, "tags": ["workspace/other"]}) != initial


@pytest.mark.semantic
def test_metadata_refresh_refuses_changed_body_and_type_resources(corpus, semantic_stack):
    built, router, _, _ = build_semantic(corpus)
    assert assets.refresh_document_metadata(corpus, router, built.index["documents"])
    changed = deepcopy(built.index["documents"])
    changed[0]["embedding_source_hash"] = "changed"
    # Supplied fingerprints cannot override the freshly admitted source.
    assert assets.refresh_document_metadata(corpus, router, changed)
    path = corpus / runtime.EMBEDDINGS_META_REL
    original_metadata = path.read_bytes()
    source = corpus / changed[0]["path"]
    original_source = source.read_text()
    source.write_text(original_source + "\nChanged current body.\n")
    assert not assets.refresh_document_metadata(corpus, router, changed)
    assert path.read_bytes() == original_metadata
    source.write_text(original_source)
    assert not assets.refresh_document_metadata(corpus, {**router, "artefacts": [{"key": "new-type"}]}, built.index["documents"])
    assert not assets.refresh_document_metadata(corpus, router, built.index["documents"][:-1])


@pytest.mark.semantic
def test_old_semantic_metadata_refresh_builds_current_filters(corpus, semantic_stack):
    _, encoder = semantic_stack
    built, router, _, _ = build_semantic(corpus)
    path = corpus / runtime.EMBEDDINGS_META_REL
    old = json.loads(path.read_text())
    old.pop("metadata_version")
    old.pop("type_text_hashes")
    for doc in old["documents"]:
        for name in ("workspace", "primary_workspace", "relevant_workspaces", "embedding_text_hash", "embedding_source_hash"):
            doc.pop(name, None)
    path.write_text(json.dumps(old))
    assert not assets.refresh_document_metadata(corpus, router, built.index["documents"])
    encoder.calls.clear()
    _, _, refreshed = assets.refresh_embeddings_outputs(corpus, router, built.index["documents"])
    assert encoder.calls
    assert runtime.embeddings_meta_matches_router(refreshed, router)
    assert next(doc for doc in refreshed["documents"] if doc["path"] == "Wiki/Bug.md")["primary_workspace"] == "workspace/barbican"


def test_temporal_archive_filter_and_all_pagination(corpus):
    source = corpus / "_Temporal/Logs/20261015-log~Bug.md"
    destination = corpus / "_Archive/_Temporal/Logs/20261015-log~Bug.md"
    destination.parent.mkdir(parents=True)
    source.rename(destination)
    index = lexical_index.build_index(corpus).index
    router = {"artefacts": []}
    first = list_combined_from_vault(corpus, router=router, index=index, location="all", workspace="workspace/subcortex", top_k=1)
    second = list_combined_from_vault(corpus, router=router, index=index, location="all", workspace="workspace/subcortex", top_k=1, cursor=first["next_cursor"])
    assert first["total"] == second["total"] == 2
    assert {item["location"] for page in (first, second) for item in page["items"]} == {"active", "archived"}
    archived = list_combined_from_vault(corpus, router=router, index=index, location="archived", workspace="workspace/missing")
    assert archived["total"] == 1
    assert archived["items"][0]["primary_workspace"] is None
    assert archived["items"][0]["relevant_workspaces"] == ["workspace/missing", "workspace/subcortex"]


@pytest.mark.parametrize("alias_kind", ["file", "archive-directory", "legacy-archive-directory", "type-directory"])
def test_archive_read_and_listing_never_open_outside_vault(tmp_path, monkeypatch, alias_kind):
    from _portable import vault_files
    vault = tmp_path / "vault"
    outside = tmp_path / "outside"
    vault.mkdir()
    outside.mkdir()
    secret = outside / "Secret.md"
    secret.write_text("---\ntype: living/workspace\nkey: outside\nworkspace: workspace/outside\n---\nOutside content.\n")
    archive = vault / "_Archive"
    if alias_kind == "file":
        archive.mkdir()
        (archive / "Secret.md").symlink_to(secret)
        reference = "_Archive/Secret.md"
    elif alias_kind == "archive-directory":
        archive.symlink_to(outside, target_is_directory=True)
        reference = "_Archive/Secret.md"
    elif alias_kind == "legacy-archive-directory":
        (vault / "Wiki").mkdir()
        (vault / "Wiki/_Archive").symlink_to(outside, target_is_directory=True)
        reference = "Wiki/_Archive/Secret.md"
    else:
        (outside / "_Archive").mkdir()
        (outside / "_Archive/Secret.md").write_text(secret.read_text())
        (vault / "Wiki").symlink_to(outside, target_is_directory=True)
        reference = "Wiki/_Archive/Secret.md"
    original_read = vault_files.read_exact_file_content
    original_walk = vault_files.os.walk
    original_listdir = vault_files.os.listdir

    def bounded(path):
        assert Path(path).resolve().is_relative_to(vault.resolve()), "attempted an outside-vault read"

    def safe_read(path, **kwargs):
        bounded(path)
        return original_read(path, **kwargs)

    def safe_walk(path, *args, **kwargs):
        bounded(path)
        return original_walk(path, *args, **kwargs)

    def safe_listdir(path):
        bounded(path)
        return original_listdir(path)

    monkeypatch.setattr(vault_files, "read_exact_file_content", safe_read)
    monkeypatch.setattr(vault_files, "os", SimpleNamespace(
        path=vault_files.os.path, walk=safe_walk, listdir=safe_listdir))
    from _common._source_access import SourceAccessError
    result = vault_files.read_archived_artefact(vault, reference)
    assert "error" in result and str(outside) not in result["error"]
    with pytest.raises(SourceAccessError):
        vault_files.list_archived_artefacts({"artefacts": [{"path": "Wiki"}]}, vault)
    excluded = []
    assert vault_files.list_archived_artefacts({"artefacts": [{"path": "Wiki"}]}, vault, excluded_sources=excluded) == []
    assert excluded
