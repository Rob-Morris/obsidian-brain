"""Synthetic source admission and derived-readiness regression controls."""
from __future__ import annotations

import builtins
import json
from pathlib import Path

import pytest

import compile_router
from _common._router import load_compiled_router
from _common._source_access import SourceAccessError
from _command_interface.derived_snapshots import FileDerivedSnapshotStore
from _lifecycle.derived_cache_state import inspect_router_cache, require_fresh_compiled_router, RouterCacheUnavailable
from _search import index as lexical
from _search.lexical_query import load_index
from _search.paths import INDEX_VERSION
from _search.resource import search_resource
from _search.snippet import extract_snippet
from _semantic import assets, runtime
from _taxonomy_descriptions import extract_type_description


@pytest.fixture
def guarded_root(tmp_path):
    (tmp_path / '.brain-core').mkdir()
    (tmp_path / '.brain-core/VERSION').write_text('test-guarded\n')
    (tmp_path / '.brain-core/session-core.md').write_text('Always:\n- Core rule\n')
    (tmp_path / '_Config').mkdir()
    (tmp_path / '_Config/router.md').write_text('Always:\n- Public rule\n')
    (tmp_path / 'Notes').mkdir()
    (tmp_path / 'Notes/one.md').write_text('---\ntype: living/note\nkey: one\n---\n# One\nSafe text\n')
    (tmp_path / '.brain/local').mkdir(parents=True)
    return tmp_path


def save_router(root, **meta):
    router = {'meta': {'brain_core_version': 'test-guarded', 'source_hash': 'sha256:current', **meta}}
    (root / '.brain/local/compiled-router.json').write_text(json.dumps(router))
    return router


def forbid_content_read(monkeypatch, forbidden):
    real_open = builtins.open
    real_path_open = Path.open

    def check(path):
        if isinstance(path, (str, bytes, Path)):
            assert Path(path).resolve() != forbidden.resolve(), 'unadmitted source opened'

    def guarded_open(path, *args, **kwargs):
        check(path)
        return real_open(path, *args, **kwargs)

    def guarded_path_open(path, *args, **kwargs):
        check(path)
        return real_path_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, 'open', guarded_open)
    monkeypatch.setattr(Path, 'open', guarded_path_open)


@pytest.mark.parametrize('source', ['private', 'archived', 'outside'])
def test_retrieval_sources_refuse_before_read(guarded_root, tmp_path, monkeypatch, source):
    root = guarded_root
    parent = {'private': root / '.brain/private', 'archived': root / '_Archive/Notes',
              'outside': tmp_path.parent / (tmp_path.name + '-external')}[source]
    parent.mkdir(parents=True)
    target = parent / 'synthetic.md'
    target.write_text('Synthetic excluded sentinel')
    alias = root / 'Notes/alias.md'
    alias.symlink_to(target)
    forbid_content_read(monkeypatch, target)
    with pytest.raises(SourceAccessError):
        lexical.parse_doc(root, 'Notes/alias.md')
    assert extract_snippet(root, 'Notes/alias.md', ['sentinel']) is None
    assert extract_type_description(root, {'taxonomy_file': 'Notes/alias.md'}) == ''
    excluded = []
    assert search_resource({'styles': [{'name': 'sentinel', 'style_doc': 'Notes/alias.md'}]},
                           root, 'style', 'sentinel', excluded=excluded) == []
    assert len(excluded) == 1
    with pytest.raises(SourceAccessError):
        assets._build_doc_corpus(root, [{'path': 'Notes/alias.md'}], type_desc_by_frontmatter={})


def test_public_alias_is_read_from_canonical_source(guarded_root):
    root = guarded_root
    (root / 'Notes/alias.md').symlink_to(root / 'Notes/one.md')
    parsed = lexical.parse_doc(root, 'Notes/alias.md')
    assert parsed.doc['tf']['safe'] == 1
    assert extract_snippet(root, 'Notes/alias.md', ['safe']).startswith('# One')


def test_full_index_excludes_denied_sources_and_counts_honestly(guarded_root):
    root = guarded_root
    private = root / '.brain/private.md'
    private.write_text('Excluded sentinel')
    (root / 'Notes/denied.md').symlink_to(private)
    result = lexical.build_index(root)
    assert result.index['meta']['index_version'] == '1.3.0'
    assert result.index['meta']['document_count'] == 1
    assert result.index['meta']['skipped_paths'] == ['Notes/denied.md']
    assert {doc['path'] for doc in result.index['documents']} == {'Notes/one.md'}


def test_old_lexical_state_cannot_be_loaded_or_incrementally_blessed(guarded_root):
    root = guarded_root
    old = {'meta': {'index_version': '1.2.0'}, 'documents': []}
    (root / '.brain/local/retrieval-index.json').write_text(json.dumps(old))
    with pytest.raises(ValueError, match='refresh-lexical'):
        load_index(root)
    with pytest.raises(ValueError, match='refresh-lexical'):
        lexical.index_update(old, root, 'Notes/one.md')
    assert old['documents'] == []


def test_router_version_rejected_before_source_verification(guarded_root, monkeypatch):
    root = guarded_root
    private = root / '.brain/private.md'
    private.write_text('Synthetic sentinel')
    save_router(root, brain_core_version='old', sources={'.brain/private.md': 'sha256:any'},
                compiled_at='2026-10-01T00:00:00+00:00')
    forbid_content_read(monkeypatch, private)
    assert 'error' in load_compiled_router(root)
    state = inspect_router_cache(root, verify_content=True)
    assert state.reason == 'version-drift' and state.payload is None
    with pytest.raises(RouterCacheUnavailable):
        require_fresh_compiled_router(root)


def test_current_router_does_not_admit_private_hash_source(guarded_root, monkeypatch):
    root = guarded_root
    private = root / '.brain/private.md'
    private.write_text('Synthetic sentinel')
    router = compile_router.compile(root)
    router['meta']['sources'] = {'.brain/private.md': 'sha256:any'}
    (root / '.brain/local/compiled-router.json').write_text(json.dumps(router))
    forbid_content_read(monkeypatch, private)
    state = inspect_router_cache(root, verify_content=True)
    assert state.stale and state.reason == 'source-unavailable'
    with pytest.raises(RouterCacheUnavailable):
        require_fresh_compiled_router(root)


def test_resident_router_rechecks_installed_producer(guarded_root):
    root = guarded_root
    save_router(root)
    store = FileDerivedSnapshotStore(root)
    assert store.load_router()['meta']['brain_core_version'] == 'test-guarded'
    (root / '.brain-core/VERSION').write_text('next-guarded\n')
    assert 'error' in store.load_router()


def test_resident_index_rechecks_current_index_version(guarded_root, monkeypatch):
    import _command_interface.derived_snapshots as snapshots
    root = guarded_root
    (root / '.brain/local/retrieval-index.json').write_text(json.dumps({'meta': {'index_version': INDEX_VERSION}}))
    store = FileDerivedSnapshotStore(root)
    assert store.load_lexical_index()['meta']['index_version'] == INDEX_VERSION
    monkeypatch.setattr(snapshots, 'INDEX_VERSION', 'next')
    # A loader using the replaced runtime's schema must refuse the old file too.
    import _search.lexical_query as query
    monkeypatch.setattr(query, 'INDEX_VERSION', 'next')
    with pytest.raises(ValueError, match='refresh-lexical'):
        store.load_lexical_index()


def semantic_meta():
    return {'metadata_version': 3, 'router_source_hash': 'sha256:current',
            'model': runtime.EMBEDDING_MODEL, 'model_revision': runtime.EMBEDDING_MODEL_REVISION,
            'dim': runtime.EMBEDDING_DIM, 'types': [], 'documents': []}


def test_old_semantic_metadata_never_loads_arrays(guarded_root, monkeypatch):
    np = pytest.importorskip('numpy')
    root = guarded_root
    save_router(root)
    meta = semantic_meta() | {'metadata_version': 2}
    (root / runtime.EMBEDDINGS_META_REL).write_text(json.dumps(meta))
    (root / runtime.DOC_EMBEDDINGS_REL).write_bytes(b'not an array')
    monkeypatch.setattr(np, 'load', lambda *_a, **_k: pytest.fail('old vectors loaded'))
    assert runtime.load_embeddings_state(root) == (None, None, None)


def test_semantic_metadata_replacement_during_array_load_is_unavailable(guarded_root, monkeypatch):
    np = pytest.importorskip('numpy')
    root = guarded_root
    save_router(root)
    meta_path = root / runtime.EMBEDDINGS_META_REL
    meta_path.write_text(json.dumps(semantic_meta()))
    np.save(root / runtime.DOC_EMBEDDINGS_REL, np.zeros((0, runtime.EMBEDDING_DIM)))
    load = np.load

    def replace_metadata(*args, **kwargs):
        result = load(*args, **kwargs)
        alternate = meta_path.with_suffix('.replacement')
        alternate.write_text(meta_path.read_text())
        alternate.replace(meta_path)
        return result

    monkeypatch.setattr(np, 'load', replace_metadata)
    assert runtime.load_embeddings_state(root, selection='documents') == (None, None, None)


def test_semantic_publication_withdraws_readiness_before_arrays(guarded_root, monkeypatch):
    root = guarded_root
    meta_path = root / runtime.EMBEDDINGS_META_REL
    meta_path.write_text(json.dumps(semantic_meta()))
    seen = []

    def save_array(_root, path, array):
        assert not meta_path.exists()
        seen.append(path)
        if len(seen) == 2:
            raise OSError('synthetic publication interruption')

    monkeypatch.setattr(assets, '_safe_save_npy', save_array)
    from _lifecycle.retrieval_errors import RetrievalPersistenceError
    with pytest.raises(RetrievalPersistenceError):
        assets._persist_embeddings_outputs(root, object(), object(), semantic_meta())
    assert len(seen) == 2 and not meta_path.exists()


def test_metadata_only_refresh_does_not_upgrade_old_vectors(guarded_root):
    root = guarded_root
    path = root / runtime.EMBEDDINGS_META_REL
    path.write_text(json.dumps(semantic_meta() | {'metadata_version': 2}))
    before = path.read_bytes()
    assert not assets.refresh_document_metadata(root, save_router(root), [])
    assert path.read_bytes() == before


def test_supplied_semantic_metadata_does_not_override_current_source(guarded_root):
    root = guarded_root
    texts, meta = assets._build_doc_corpus(root, [{
        'path': 'Notes/one.md', 'title': 'Untrusted cached heading', 'type': 'old/type',
    }], type_desc_by_frontmatter={})
    assert 'Untrusted cached heading' not in texts[0]
    assert 'Safe text' in texts[0]
    assert meta[0]['type'] == 'living/note'



def test_rebuild_failure_after_router_publication_retains_exact_effects(guarded_root, monkeypatch):
    import _lifecycle.retrieval_assets as retrieval
    import _lifecycle.semantic_rebuild as rebuild
    root = guarded_root

    def fail_build(_root):
        raise OSError('synthetic later publication failure')

    monkeypatch.setattr(retrieval.search_index, 'build_index', fail_build)
    monkeypatch.setattr(compile_router, 'refresh_session_markdown',
        lambda *_a: (_ for _ in ()).throw(OSError('synthetic mirror failure')))
    result = rebuild.rebuild_semantic(root, dry_run=False)
    assert result['status'] == 'partial'
    assert result['steps'][0]['committed_effects'] == [
        '.brain/local/compiled-router.json',
        compile_router.compile_colours.OUTPUT_REL,
        compile_router.compile_colours.GRAPH_JSON_REL,
    ]
    assert (root / '.brain/local/compiled-router.json').exists()


def test_live_list_revision_changes_with_command_semantics_without_asset_change(guarded_root, monkeypatch):
    from types import SimpleNamespace
    from _command_interface.result_delivery import ResultDeliverySession
    from _application.artefact.list import ArtefactListRequest
    root = guarded_root
    context = SimpleNamespace(derived_snapshots=FileDerivedSnapshotStore(root))
    first = ResultDeliverySession._revision(context, 'active')
    monkeypatch.setattr(ArtefactListRequest, 'COMMAND_VERSION', 4)
    old = ResultDeliverySession._revision(context, 'active')
    assert old != first
    assert ResultDeliverySession._revision(context, 'archived') is None
    assert ResultDeliverySession._revision(context, 'all') is None


def test_supplied_old_semantic_state_refused_before_query_encoding(guarded_root, monkeypatch):
    from _search.semantic_query import search_semantic, EmbeddingsSidecarsUnavailableError
    root = guarded_root
    save_router(root)
    monkeypatch.setattr(runtime, 'encode_query', lambda *_a, **_k: pytest.fail('encoder ran before admission'))
    with pytest.raises(EmbeddingsSidecarsUnavailableError):
        search_semantic('safe', root, doc_embeddings=object(),
                        embeddings_meta=semantic_meta() | {'metadata_version': 2})


def test_provisioning_partial_asset_step_preserves_published_paths():
    from _semantic.provision import SemanticProvisionOutcome, append_asset_step
    from _lifecycle_common import derive_step_status
    outcome = SemanticProvisionOutcome(
        runtime_changed=False, model_outcome=None, marker_changed=False,
        marker_installed=False, assets_changed=False, assets_error='Synthetic interruption',
        notes=[], assets_committed_paths=('.brain/local/compiled-router.json',),
    )
    steps, notes = [], []
    append_asset_step(steps, notes, outcome)
    assert derive_step_status(steps) == 'partial'
    assert steps[0]['committed_effects'] == ['.brain/local/compiled-router.json']


def test_lexical_and_semantic_builders_share_source_fingerprint(guarded_root):
    from _lifecycle.document_parts import read_embedding_document
    root = guarded_root
    lexical_doc = lexical.parse_doc(root, 'Notes/one.md')
    semantic_doc, semantic_parts = read_embedding_document(root, 'Notes/one.md')
    assert semantic_doc['embedding_source_hash'] == lexical_doc.doc['embedding_source_hash']
    assert semantic_parts == lexical_doc.embedding_parts


@pytest.mark.parametrize('operation', ['compile', 'verify'])
def test_version_hash_read_rechecks_fixed_installation_capability(guarded_root, monkeypatch, operation):
    from _bootstrap.owned_paths import UnsafeFilesystemLayoutError
    root = guarded_root
    version = root / '.brain-core/VERSION'
    target = root / '.brain/synthetic-version-target.txt'
    target.write_text('Synthetic installation-metadata sentinel')
    if operation == 'verify':
        router = compile_router.compile(root)
        (root / '.brain/local/compiled-router.json').write_text(json.dumps(router))
    hash_file = compile_router.hash_file
    redirected = False

    def redirect_before_version_hash(path):
        nonlocal redirected
        if not redirected:
            version.unlink()
            version.symlink_to(target)
            redirected = True
        return hash_file(path)

    # Exercise the hash seam independently of the earlier producer-version read.
    monkeypatch.setattr(compile_router, 'hash_file', redirect_before_version_hash)
    forbid_content_read(monkeypatch, target)
    if operation == 'compile':
        with pytest.raises(UnsafeFilesystemLayoutError):
            compile_router.compile(root)
    else:
        state = inspect_router_cache(root, verify_content=True)
        assert state.stale and state.reason == 'source-unavailable'
    assert redirected


@pytest.fixture
def derived_endpoint_root(tmp_path):
    root = tmp_path / 'selected-brain'
    (root / '.brain/local').mkdir(parents=True)
    (root / '.brain-core').mkdir()
    (root / '.brain-core/VERSION').write_text('test-guarded\n')
    return root


@pytest.mark.parametrize('kind', ['router', 'lexical'])
def test_fixed_json_cache_endpoint_refused_before_open(derived_endpoint_root, tmp_path, monkeypatch, kind):
    from _bootstrap.owned_paths import UnsafeFilesystemLayoutError
    from _lifecycle.derived_cache_state import inspect_lexical_cache
    root = derived_endpoint_root
    relative = ('.brain/local/compiled-router.json' if kind == 'router'
                else '.brain/local/retrieval-index.json')
    outside = tmp_path / 'external-cache.json'
    outside.write_text(json.dumps({'synthetic': 'excluded sentinel'}))
    endpoint = root / relative
    endpoint.symlink_to(outside)
    forbid_content_read(monkeypatch, outside)
    if kind == 'router':
        assert 'Unsafe Brain-owned filesystem layout' in load_compiled_router(root)['error']
        state = inspect_router_cache(root)
    else:
        with pytest.raises(UnsafeFilesystemLayoutError):
            load_index(root)
        state = inspect_lexical_cache(root)
    assert state.stale and state.reason == 'unsafe-layout' and state.payload is None
    assert endpoint.is_symlink()


@pytest.mark.parametrize('kind', ['router', 'lexical'])
def test_resident_cache_does_not_follow_link_to_unchanged_inode(derived_endpoint_root, tmp_path, monkeypatch, kind):
    from _bootstrap.owned_paths import UnsafeFilesystemLayoutError
    root = derived_endpoint_root
    store = FileDerivedSnapshotStore(root)
    if kind == 'router':
        save_router(root)
        endpoint = root / '.brain/local/compiled-router.json'
        load = store.load_router
    else:
        endpoint = root / '.brain/local/retrieval-index.json'
        endpoint.write_text(json.dumps({'meta': {'index_version': INDEX_VERSION}}))
        load = store.load_lexical_index
    assert 'meta' in load()
    outside = tmp_path / 'moved-cache.json'
    endpoint.rename(outside)
    endpoint.symlink_to(outside)
    forbid_content_read(monkeypatch, outside)
    if kind == 'router':
        assert 'Unsafe Brain-owned filesystem layout' in load()['error']
    else:
        with pytest.raises(UnsafeFilesystemLayoutError):
            load()
    assert kind not in store._cache


@pytest.mark.parametrize('record,selection', [
    (runtime.EMBEDDINGS_META_REL, 'all'),
    (runtime.TYPE_EMBEDDINGS_REL, 'types'),
    (runtime.DOC_EMBEDDINGS_REL, 'documents'),
])
def test_semantic_cache_endpoint_refused_before_metadata_or_array_open(
    derived_endpoint_root, tmp_path, monkeypatch, record, selection,
):
    np = pytest.importorskip('numpy')
    root = derived_endpoint_root
    save_router(root)
    (root / runtime.EMBEDDINGS_META_REL).write_text(json.dumps(semantic_meta()))
    outside = tmp_path / 'external-semantic-record'
    outside.write_text('Synthetic excluded semantic sentinel')
    endpoint = root / record
    endpoint.unlink(missing_ok=True)
    endpoint.symlink_to(outside)
    forbid_content_read(monkeypatch, outside)
    monkeypatch.setattr(np, 'load', lambda *_a, **_k: pytest.fail('array opened before endpoint admission'))
    with pytest.raises(runtime.SemanticEmbeddingsLayoutError, match='Unsafe Brain-owned filesystem layout'):
        runtime.load_embeddings_state(root, selection=selection)
    assert endpoint.is_symlink()


def test_metadata_only_refresh_refuses_redirected_record_before_open(derived_endpoint_root, tmp_path, monkeypatch):
    from _bootstrap.owned_paths import UnsafeFilesystemLayoutError
    root = derived_endpoint_root
    outside = tmp_path / 'external-meta.json'
    outside.write_text(json.dumps(semantic_meta()))
    (root / runtime.EMBEDDINGS_META_REL).symlink_to(outside)
    forbid_content_read(monkeypatch, outside)
    with pytest.raises(UnsafeFilesystemLayoutError):
        assets.refresh_document_metadata(root, save_router(root), [])


def test_missing_derived_endpoints_retain_loader_contracts(derived_endpoint_root):
    from _search.lexical_query import IndexNotFoundError
    root = derived_endpoint_root
    assert load_compiled_router(root)['error'].startswith('Compiled router not found at ')
    assert FileDerivedSnapshotStore(root).load_router()['error'].startswith('Compiled router not found at ')
    with pytest.raises(IndexNotFoundError):
        load_index(root)
    assert runtime.load_embeddings_state(root) == (None, None, None)


@pytest.mark.parametrize('kind', ['relative-public', 'private', 'outside', 'absolute-public'])
def test_missing_router_source_diagnosis_only_exposes_admitted_relative_locator(guarded_root, kind):
    root = guarded_root
    router = compile_router.compile(root)
    locator = {
        'relative-public': 'Notes/missing.md',
        'private': '.brain/missing-private.md',
        'outside': str(root.parent / 'missing-outside.md'),
        'absolute-public': str(root / 'Notes/missing.md'),
    }[kind]
    router['meta']['sources'] = {locator: 'sha256:missing'}
    router['meta']['artefact_index_sources'] = [locator]
    router['meta'].pop('artefact_index_source_count', None)
    (root / '.brain/local/compiled-router.json').write_text(json.dumps(router))
    state = inspect_router_cache(root, verify_content=True)
    assert state.stale
    if kind == 'relative-public':
        assert state.reason == 'artefact-index-source-unreadable'
        assert state.source_path == 'Notes/missing.md'
    else:
        assert state.reason == 'source-unavailable'
        assert state.source_path is None and state.payload is None
