"""Fixed publication worksets and operation-owned partial-effect controls."""
from contextlib import contextmanager
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import compile_router
from _bootstrap.owned_paths import UnsafeFilesystemLayoutError
from _lifecycle import retrieval_assets
from _search import index as lexical
from _semantic import assets, runtime


@pytest.fixture
def root(tmp_path):
    selected = tmp_path / 'brain'
    (selected / '.brain/local').mkdir(parents=True)
    (selected / '.brain-core').mkdir()
    (selected / '.brain-core/VERSION').write_text('test-producer')
    return selected


@pytest.mark.parametrize('owner', ['router', 'lexical', 'semantic', 'clear', 'drop', 'combined'])
@pytest.mark.parametrize('redirect', ['endpoint', 'ancestor'])
def test_complete_output_admission_precedes_callbacks_unlink_and_mkdir(root, tmp_path, monkeypatch, owner, redirect):
    meta = root / runtime.EMBEDDINGS_META_REL
    meta.write_text('original readiness')
    target = tmp_path / 'external-destination'
    if redirect == 'ancestor':
        (root / '.brain/local').rename(target)
        (root / '.brain/local').symlink_to(target, target_is_directory=True)
        witness = target / 'embeddings-meta.json'
    else:
        target.write_text('unrelated destination sentinel')
        relative = {
            'router': '.obsidian/graph.json', 'lexical': '.brain/local/retrieval-index.json',
            'semantic': runtime.DOC_EMBEDDINGS_REL, 'clear': runtime.DOC_EMBEDDINGS_REL,
            'drop': runtime.DOC_EMBEDDINGS_REL, 'combined': runtime.DOC_EMBEDDINGS_REL,
        }[owner]
        endpoint = root / relative
        endpoint.parent.mkdir(parents=True, exist_ok=True)
        endpoint.symlink_to(target)
        witness = target
    before, mode = witness.read_bytes(), witness.stat().st_mode
    operations = {
        'router': lambda: compile_router.persist_compiled_router(root, {'artefacts': []}),
        'lexical': lambda: lexical.persist_retrieval_index(root, {}),
        'semantic': lambda: assets._persist_embeddings_outputs(root, object(), object(), {}),
        'clear': lambda: runtime.clear_embeddings_outputs(root),
        'drop': lambda: retrieval_assets.drop_path_keyed_retrieval_caches(root),
        'combined': lambda: retrieval_assets.persist_retrieval_outputs(root, {'documents': []}),
    }
    with monkeypatch.context() as effects:
        def forbidden(*_a, **_kw):
            pytest.fail('effect began before complete output admission')
        effects.setattr(Path, 'mkdir', forbidden)
        effects.setattr(Path, 'unlink', forbidden)
        effects.setattr(lexical, 'safe_write_json', forbidden)
        effects.setattr(compile_router, 'safe_write_json', forbidden)
        effects.setattr(assets, 'safe_write_via', forbidden)
        with pytest.raises(UnsafeFilesystemLayoutError):
            operations[owner]()
    assert witness.read_bytes() == before and witness.stat().st_mode == mode
    assert meta.read_text() == 'original readiness'


def test_layout_loader_failure_is_not_rebuildable_corruption(root, monkeypatch):
    monkeypatch.setattr(assets.semantic_model, 'inspect_model_state',
                        lambda _r: SimpleNamespace(manifest=None))
    def refused(_root):
        raise runtime.SemanticEmbeddingsLayoutError('Restore the unsafe semantic layout')
    monkeypatch.setattr(runtime, 'load_embeddings_state', refused)
    monkeypatch.setattr(assets, 'build_embeddings', lambda *_a: pytest.fail('layout refusal rebuilt'))
    with pytest.raises(runtime.SemanticEmbeddingsLayoutError, match='Restore'):
        assets.refresh_embeddings_outputs(root, {}, [])


def test_corrupt_vectors_still_allow_admitted_rebuild(root, monkeypatch):
    monkeypatch.setattr(assets.semantic_model, 'inspect_model_state',
                        lambda _r: SimpleNamespace(manifest=None))
    def corrupt(_root):
        raise runtime.SemanticEmbeddingsLoadError('corrupt synthetic vectors')
    monkeypatch.setattr(runtime, 'load_embeddings_state', corrupt)
    rebuilt = object()
    monkeypatch.setattr(assets, 'build_embeddings', lambda *_a: rebuilt)
    assert assets.refresh_embeddings_outputs(root, {}, []) is rebuilt


def test_lexical_receipt_excludes_publication_after_lock_release(root, monkeypatch):
    import _common
    import _portable.lexical_maintenance as portable
    import _application._lexical_maintenance as owner
    from _application.retrieval.refresh_lexical import RetrievalRefreshLexicalRequest
    index_path = root / '.brain/local/retrieval-index.json'
    index_path.write_text('before')
    locked = []
    @contextmanager
    def lock(_root):
        locked.append(True)
        try:
            yield
        finally:
            locked.pop()
            index_path.write_text('another invocation published after unlock')
            (root / '.brain/local/compiled-router.json').write_text('another router publication')
    def fail(*_args, **_kwargs):
        assert locked
        raise OSError('synthetic no-effect failure')
    monkeypatch.setattr(_common, 'vault_mutation_lock', lock)
    monkeypatch.setattr(owner, 'admit_owner', lambda *_a: None)
    monkeypatch.setattr(portable, 'maintain_lexical_index', fail)
    context = SimpleNamespace(selected_brain=SimpleNamespace(vault_root=root), dry_run=False)
    result = owner.execute_lexical_maintenance(context, RetrievalRefreshLexicalRequest(), force=False)
    assert result.status == 'error' and result.effects == 'none'
    assert index_path.read_text() == 'another invocation published after unlock'


def test_missing_output_parents_are_created_only_after_complete_admission(tmp_path):
    root = tmp_path / 'fresh-brain'
    root.mkdir()
    lexical.persist_retrieval_index(root, {'meta': {'index_version': '1.3.0'}})
    assert json.loads((root / '.brain/local/retrieval-index.json').read_text())['meta']['index_version'] == '1.3.0'


def test_current_folder_type_matches_lexical_semantic_filters_and_metadata_refresh(root, monkeypatch):
    import _common
    from _search.filters import SearchFilters
    notes = root / 'Notes'
    notes.mkdir()
    (notes / 'untyped.md').write_text('# Current untyped note\nFresh admitted body')
    index = lexical.build_index(root).index
    lexical_doc, = index['documents']
    calls = {'living': 0, 'temporal': 0}
    living, temporal = _common.scan_living_types, _common.scan_temporal_types
    def scan_living(selected):
        calls['living'] += 1
        return living(selected)
    def scan_temporal(selected):
        calls['temporal'] += 1
        return temporal(selected)
    monkeypatch.setattr(_common, 'scan_living_types', scan_living)
    monkeypatch.setattr(_common, 'scan_temporal_types', scan_temporal)
    supplied = [{**lexical_doc, 'type': 'untrusted/cached-type', 'title': 'Untrusted title'}]
    texts, documents = assets._build_doc_corpus(root, supplied,
        type_desc_by_frontmatter={'living/notes': 'Current taxonomy description'})
    assert calls == {'living': 1, 'temporal': 1}
    semantic_doc, = documents
    assert semantic_doc['type'] == lexical_doc['type'] == 'living/notes'
    assert semantic_doc['embedding_source_hash'] == lexical_doc['embedding_source_hash']
    assert 'Current taxonomy description' in texts[0] and 'Untrusted title' not in texts[0]
    assert SearchFilters(type='living/notes').matches(semantic_doc)
    router = {'meta': {'source_hash': 'sha256:current'}, 'artefacts': []}
    meta = {'metadata_version': runtime.EMBEDDINGS_METADATA_VERSION,
            'type_resources_hash': assets._type_resources_hash(router), 'documents': documents}
    path = root / runtime.EMBEDDINGS_META_REL
    path.write_text(json.dumps(meta))
    assert assets.refresh_document_metadata(root, router, supplied)
    assert calls == {'living': 2, 'temporal': 2}
    refreshed = json.loads(path.read_text())['documents'][0]
    assert refreshed['type'] == 'living/notes'
    assert refreshed['embedding_source_hash'] == lexical_doc['embedding_source_hash']


def test_repeated_refresh_excludes_unchanged_mirror_from_later_failure_receipt(command_vault_clone, monkeypatch):
    from datetime import datetime, timezone
    import session
    root = command_vault_clone.vault_root
    frozen = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)

    class FixedCompileClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen.astimezone(tz) if tz is not None else frozen.replace(tzinfo=None)

    # Keep the actual compiler and mirror writer, with identical rendered input
    # across the repeated refresh rather than relying on clock resolution.
    monkeypatch.setattr(compile_router, 'datetime', FixedCompileClock)
    compiled = compile_router.compile(root)
    compile_router.persist_compiled_router(root, compiled)
    # Generated colour directories can themselves affect environment discovery.
    # Observe the settled installed layout before checking a true mirror no-op.
    compiled = compile_router.compile(root)
    compile_router.persist_compiled_router(root, compiled)
    mirror = root / session.SESSION_MARKDOWN_REL
    mirror.unlink(missing_ok=True)
    assert compile_router.refresh_session_markdown(root, compiled) is True
    before = mirror.read_bytes()
    info = mirror.stat()
    before_identity = info.st_ino, info.st_mtime_ns, info.st_size
    assert compile_router.refresh_session_markdown(root, compiled) is False

    def fail_after_mirror(_root):
        raise OSError('synthetic lexical failure after unchanged mirror refresh')

    monkeypatch.setattr(lexical, 'build_index', fail_after_mirror)
    with pytest.raises(retrieval_assets.RetrievalRefreshIncomplete) as exc:
        retrieval_assets.refresh_retrieval_assets(root)
    assert exc.value.committed_paths == (
        compile_router.OUTPUT_PATH,
        compile_router.compile_colours.OUTPUT_REL,
        compile_router.compile_colours.GRAPH_JSON_REL,
    )
    assert isinstance(exc.value.__cause__, OSError)
    assert mirror.read_bytes() == before
    info = mirror.stat()
    assert (info.st_ino, info.st_mtime_ns, info.st_size) == before_identity
