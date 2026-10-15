"""Typed and projected workspace discovery keeps scope separate from authority."""

import json
from io import StringIO
from pathlib import Path
import sys

import pytest

from _application.artefact.list import ArtefactListRequest
from _application.artefact.search import ArtefactSearchRequest, ArtefactSearchMode
from _application.projection import request_schema, result_payload_schema, canonical_result_envelope
from _application.registry import current_request_resolver
from _search import index as search_index
from _common._workspace import WorkspaceMatch
from command_application import application_for


def prepare_corpus(root):
    note = root / "Projects/Command Fixture.md"
    from _common import read_artefact, serialize_frontmatter
    fields, body = read_artefact(str(note))
    fields["workspace"] = "workspace/barbican"
    fields["tags"] = ["workspace/subcortex"]
    note.write_text(serialize_frontmatter(fields, body))
    import compile_router
    compile_router.persist_compiled_router(str(root), compile_router.compile(str(root)))
    built = search_index.build_index(root)
    search_index.persist_retrieval_index(root, built.index)
    return note


def test_typed_list_search_results_and_unfiltered_scope(command_vault_clone):
    root = command_vault_clone.vault_root
    prepare_corpus(root)
    app = application_for(root)
    for workspace, match, count in [("workspace/subcortex", WorkspaceMatch.RELEVANT, 1), ("workspace/subcortex", WorkspaceMatch.PRIMARY, 0), ("workspace/barbican", WorkspaceMatch.PRIMARY, 1), ("workspace/missing", WorkspaceMatch.RELEVANT, 0)]:
        requests = [ArtefactListRequest(workspace=workspace, workspace_match=match), ArtefactSearchRequest("command fixture", workspace=workspace, workspace_match=match, mode=ArtefactSearchMode.LEXICAL)]
        for request in requests:
            result = app.invoke(request)
            assert result.status == "ok", getattr(result, "error", result)
            assert result.result.returned == count
            assert canonical_result_envelope(result)["committed_effects"] == []
            if count:
                item = result.result.items[0]
                assert item.primary_workspace == "workspace/barbican"
                assert item.relevant_workspaces == ("workspace/barbican", "workspace/subcortex")
    assert app.invoke(ArtefactListRequest()).result.returned >= 1


def test_json_request_and_result_schema_include_workspace_modes():
    resolver = current_request_resolver()
    for request_type in (ArtefactListRequest, ArtefactSearchRequest):
        extra = {"query": "bug"} if request_type is ArtefactSearchRequest else {}
        request = resolver.resolve(request_type.COMMAND_ID, {**extra, "workspace": "workspace/missing", "workspace_match": "primary"})
        assert request.workspace_match is WorkspaceMatch.PRIMARY
        schema = request_schema(request_type)
        assert schema["properties"]["workspace_match"]["enum"] == ["relevant", "primary"]
        result_schema = result_payload_schema(request_type)
        assert "primary_workspace" in result_schema["properties"]["items"]["items"]["properties"]
        for wrong in ("primary-only", "all", None, True):
            with pytest.raises((TypeError, ValueError)):
                resolver.resolve(request_type.COMMAND_ID, {**extra, "workspace_match": wrong})


def test_public_cli_filesystem_workspace_and_json_filter_are_distinct():
    cli = Path(__file__).resolve().parents[2] / "cli"
    if str(cli) not in sys.path:
        sys.path.insert(0, str(cli))
    from _local_cli.main import _parse_common
    trusted, argv = _parse_common(["artefact", "list", "--workspace", "/tmp/source-folder", "--request-json", '{"workspace":"workspace/barbican","workspace_match":"primary"}'])
    request = current_request_resolver().resolve("artefact.list", json.loads(trusted.request_json))
    assert trusted.workspace == "/tmp/source-folder"
    assert request.workspace == "workspace/barbican"
    assert request.workspace_match is WorkspaceMatch.PRIMARY
    assert argv == ["artefact", "list"]


def test_public_workspace_match_enum():
    from brain_application.values import WorkspaceMatch as PublicWorkspaceMatch
    assert PublicWorkspaceMatch is WorkspaceMatch


@pytest.mark.semantic
def test_workspace_tag_edit_refreshes_semantic_filters_without_encoding(command_vault_clone, monkeypatch):
    np = pytest.importorskip("numpy")
    from types import SimpleNamespace
    from _semantic import model, assets, runtime
    from _common import load_compiled_router, document_revision_at
    from _application._mutation_support import FrontmatterField
    from _application.document._types import DocumentLocator, DocumentResource
    from _application.document.update_frontmatter import DocumentUpdateFrontmatterRequest

    root = command_vault_clone.vault_root
    from _application.workspace.create import WorkspaceCreateRequest
    for key in ("barbican", "subcortex"):
        result = application_for(root).invoke(WorkspaceCreateRequest(key))
        assert result.status == "ok", getattr(result, "error", result)
    note = prepare_corpus(root)
    calls = []

    def encode(texts, **kwargs):
        calls.append(texts)
        vectors = np.zeros((len(texts), runtime.EMBEDDING_DIM))
        vectors[:, 0] = 1
        return vectors

    manifest = model.ModelManifest(model.SHIPPED_MODEL_NAME, model.SHIPPED_MODEL_REVISION, "2026-10-15")
    monkeypatch.setattr(model, "load_local_model_with_manifest", lambda *_: (SimpleNamespace(encode=encode), manifest))
    built = search_index.build_index(root)
    _, vectors, metadata = assets.build_embeddings(root, load_compiled_router(root), built.index["documents"], embedding_parts_by_path=built.embedding_parts_by_path)
    vector_bytes = (root / runtime.DOC_EMBEDDINGS_REL).read_bytes()
    calls.clear()
    relative = "Projects/Command Fixture.md"
    result = application_for(root).invoke(DocumentUpdateFrontmatterRequest(
        DocumentLocator(DocumentResource.ARTEFACT, relative), document_revision_at(note),
        (FrontmatterField("tags", ()),),
    ))
    assert result.status == "ok", getattr(result, "error", result)
    assert calls == []
    assert (root / runtime.DOC_EMBEDDINGS_REL).read_bytes() == vector_bytes
    _, refreshed_vectors, refreshed = runtime.load_embeddings_state(root)
    assert runtime.embeddings_meta_matches_router(refreshed, load_compiled_router(root))
    row = next(doc for doc in refreshed["documents"] if doc["path"] == relative)
    assert row["primary_workspace"] == "workspace/barbican"
    assert row["relevant_workspaces"] == ["workspace/barbican"]
    assert row["tags"] == []
    assert application_for(root).invoke(ArtefactListRequest(workspace="workspace/subcortex", type_filter="living/project")).result.returned == 0
    from _search.semantic_query import search_semantic
    from _search.filters import SearchFilters
    assert search_semantic("command", root, filters=SearchFilters(workspace="workspace/subcortex", type="living/project"), doc_embeddings=refreshed_vectors, embeddings_meta=refreshed, query_encoder=SimpleNamespace(encode=encode)) == []


@pytest.mark.semantic
def test_metadata_only_incremental_status_keeps_vectors(command_vault_clone, monkeypatch):
    np = pytest.importorskip("numpy")
    from types import SimpleNamespace
    from _semantic import model, assets, runtime
    from _common import load_compiled_router
    from _application.artefact.set_status import ArtefactSetStatusRequest

    root = command_vault_clone.vault_root
    manifest = model.ModelManifest(model.SHIPPED_MODEL_NAME, model.SHIPPED_MODEL_REVISION, "2026-10-15")
    monkeypatch.setattr(model, "load_local_model_with_manifest", lambda *_: (SimpleNamespace(encode=lambda texts, **_: np.zeros((len(texts), runtime.EMBEDDING_DIM))), manifest))
    built = search_index.build_index(root)
    assets.build_embeddings(root, load_compiled_router(root), built.index["documents"], embedding_parts_by_path=built.embedding_parts_by_path)
    before = (root / runtime.DOC_EMBEDDINGS_REL).read_bytes()
    result = application_for(root).invoke(ArtefactSetStatusRequest("Ideas/Command Fixture Candidate.md", "shaping"))
    assert result.status == "ok", getattr(result, "error", result)
    assert (root / runtime.DOC_EMBEDDINGS_REL).read_bytes() == before
    meta = json.loads((root / runtime.EMBEDDINGS_META_REL).read_text())
    assert next(doc for doc in meta["documents"] if doc["path"] == "Ideas/Command Fixture Candidate.md")["status"] == "shaping"
    assert runtime.embeddings_meta_matches_router(meta, load_compiled_router(root))


@pytest.mark.parametrize("transport", ["mcp", "cli"])
@pytest.mark.parametrize("command", ["list", "search"])
def test_public_transports_keep_symbolic_workspace_filter_separate_from_caller(
    command_vault_clone, tmp_path, monkeypatch, capsys, transport, command,
):
    root = command_vault_clone.vault_root
    prepare_corpus(root)
    folder = tmp_path / "bound-caller"
    folder.mkdir()
    import vault_registry
    from _application.workspace.create import WorkspaceCreateRequest
    from _bootstrap.workspace_binding import save_workspace_manifest_data

    assert application_for(root).invoke(WorkspaceCreateRequest("caller")).status == "ok"
    vault_registry.register(root, "fixture-brain")
    save_workspace_manifest_data(folder, {"schema_version": 1, "brain": "fixture-brain", "slug": "caller"})
    app = application_for(root, workspace_dir=folder)
    payload = {"workspace": "workspace/subcortex", "workspace_match": "relevant"}
    if command == "search":
        payload.update(query="command fixture", mode="lexical")
    if transport == "mcp":
        import asyncio
        from mcp.server import MCPServer
        from brain_mcp._command_adapter import register_application_tools
        from _application.registry import current_application_catalogue

        mcp = MCPServer("workspace-discovery")
        register_application_tools(mcp, catalogue=current_application_catalogue(),
            resolver=current_request_resolver(), context_factory=lambda **_metadata: app._context,
            invocation_guard=lambda: None)
        called = asyncio.run(mcp.call_tool("artefact_" + command, payload))
        assert not called.is_error, called
        envelope = called.structured_content
    else:
        repo = Path(__file__).resolve().parents[2]
        monkeypatch.syspath_prepend(str(repo / "cli"))
        from _local_cli.main import run

        monkeypatch.setenv("BRAIN_CLI_BINARY", str(repo / "cli/brain"))
        monkeypatch.setenv("BRAIN_CLI_DISTRIBUTION_ROOT", str(repo))
        monkeypatch.delenv("BRAIN_WORKSPACE_DIR", raising=False)
        monkeypatch.delenv("BRAIN_VAULT_ROOT", raising=False)
        code = run(["--vault", str(root), "--workspace", str(folder),
                    "artefact", command, "--request-json", json.dumps(payload), "--json"])
        output = capsys.readouterr()
        assert code == 0, output
        envelope = json.loads(output.out)
    assert envelope["committed_effects"] == []
    assert envelope["result"]["returned"] == 1
    item = envelope["result"]["items"][0]
    assert item["primary_workspace"] == "workspace/barbican"
    assert item["relevant_workspaces"] == ["workspace/barbican", "workspace/subcortex"]


def test_resource_search_keeps_its_existing_result_shape():
    from _application.resource.search import ResourceSearchRequest
    from _application._search_support import search_payload

    schema = result_payload_schema(ResourceSearchRequest)
    properties = schema["properties"]["items"]["items"]["properties"]
    assert "primary_workspace" not in properties
    assert "relevant_workspaces" not in properties
    from _application.projection import canonical_wire_value
    result = canonical_wire_value(search_payload("lexical", [{"path": "resource"}]))
    assert set(result["items"][0]) == {"path", "title", "resource_type", "status", "score", "snippet"}
