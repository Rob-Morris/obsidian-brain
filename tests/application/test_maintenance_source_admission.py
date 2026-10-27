"""Synthetic source/capability controls at maintenance preparation's hash seam."""
from pathlib import Path
from types import SimpleNamespace
import hashlib
import os

import pytest

from _application._managed_preparation import maintenance_binding
from _application.runtime.refresh_router import RuntimeRefreshRouterRequest, catalogue_entry
from _bootstrap.owned_paths import UnsafeFilesystemLayoutError
from _common._source_access import SourceAccessError
from _portable.maintenance_inputs import (
    file_identity, owned_file_identity, public_file_identity, source_manifest,
)


def put(root, relative, text='Synthetic input\n'):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / 'vault'
    put(root, '.brain-core/VERSION', 'synthetic-version\n')
    put(root, '.brain-core/session-core.md')
    put(root, '.brain/config.yaml', '{}\n')
    return root


def observe_opens(monkeypatch, forbidden=()):
    original = Path.open
    forbidden = {path.resolve() for path in forbidden}
    opened = []
    def checked(path, *args, **kwargs):
        assert path.resolve() not in forbidden, 'Prohibited source reached Path.open before admission'
        opened.append(path)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', checked)
    return opened


def prepare(root, route):
    request = RuntimeRefreshRouterRequest()
    context = SimpleNamespace(selected_brain=SimpleNamespace(vault_root=root))
    if route == 'manifest':
        return source_manifest(root)
    if route == 'binding':
        return maintenance_binding(context, request)
    # Exercise the preparation hook registered by the supported public command.
    return catalogue_entry().preparation.prepare(context, request)


@pytest.mark.parametrize('route', ['manifest', 'binding', 'registered'])
@pytest.mark.parametrize('scope', ['outside', 'private'])
def test_type_ancestor_alias_refused_before_hash_open(vault, tmp_path, monkeypatch, route, scope):
    source = (put(tmp_path / 'other', 'Note.md') if scope == 'outside'
              else put(vault, '.brain/local/Note.md'))
    (vault / 'Wiki').symlink_to(source.parent, target_is_directory=True)
    observe_opens(monkeypatch, [source])
    with pytest.raises(SourceAccessError) as caught:
        prepare(vault, route)
    assert str(source) not in str(caught.value)
    assert str(source.parent) not in str(caught.value)


@pytest.mark.parametrize('route', ['manifest', 'registered'])
def test_named_tree_alias_does_not_grant_private_hash_authority(vault, monkeypatch, route):
    source = put(vault, '.brain/private/Note.md')
    (vault / '_Config').symlink_to(source.parent, target_is_directory=True)
    observe_opens(monkeypatch, [source])
    with pytest.raises(SourceAccessError):
        prepare(vault, route)


@pytest.mark.parametrize('route', ['manifest', 'binding', 'registered'])
def test_safe_public_alias_is_hashed_via_canonical_target(vault, monkeypatch, route):
    source = put(vault, 'Content/Note.md')
    alias = vault / 'Wiki'
    alias.symlink_to(source.parent, target_is_directory=True)
    opened = observe_opens(monkeypatch)
    result = prepare(vault, route)
    assert result
    assert source in opened
    assert alias / 'Note.md' not in opened


@pytest.mark.parametrize('layout', ['core-ancestor', 'version-leaf', 'defaults-ancestor', 'config-ancestor'])
@pytest.mark.parametrize('route', ['manifest', 'registered'])
def test_fixed_inputs_use_owned_capabilities_before_open(vault, tmp_path, monkeypatch, layout, route):
    if layout == 'core-ancestor':
        installation = tmp_path / 'installation'
        (vault / '.brain-core').rename(installation)
        (vault / '.brain-core').symlink_to(installation, target_is_directory=True)
        forbidden = [installation / 'VERSION', installation / 'session-core.md']
    elif layout == 'version-leaf':
        source = put(vault, 'Unrelated.txt')
        (vault / '.brain-core/VERSION').unlink()
        (vault / '.brain-core/VERSION').symlink_to(source)
        forbidden = [source]
    elif layout == 'defaults-ancestor':
        source = put(vault, '.brain/private/config.yaml')
        (vault / '.brain-core/defaults').symlink_to(source.parent, target_is_directory=True)
        forbidden = [source]
    else:
        source = put(vault, '.brain/private/config.yaml')
        (vault / '.brain/local').symlink_to(source.parent, target_is_directory=True)
        forbidden = [source]
    observe_opens(monkeypatch, forbidden)
    with pytest.raises(UnsafeFilesystemLayoutError) as caught:
        prepare(vault, route)
    assert str(tmp_path) not in str(caught.value)


def test_version_is_owned_input_but_never_a_public_exception(vault):
    assert owned_file_identity(vault, '.brain-core/VERSION')
    with pytest.raises(SourceAccessError):
        public_file_identity(vault, '.brain-core/VERSION')


def test_owned_defaults_participate_in_manifest_without_public_grant(vault):
    source = put(vault, '.brain-core/defaults/config.yaml', 'before\n')
    before = source_manifest(vault)
    source.write_text('after\n')
    assert source_manifest(vault)['sha256'] != before['sha256']
    with pytest.raises(SourceAccessError):
        public_file_identity(vault, '.brain-core/defaults/config.yaml')


def test_managed_output_refused_before_hashing_unrelated_public_bytes(vault, monkeypatch):
    source = put(vault, 'Unrelated.txt')
    output = vault / '.brain/local/compiled-router.json'
    output.parent.mkdir(parents=True)
    output.symlink_to(source)
    observe_opens(monkeypatch, [source])
    with pytest.raises(UnsafeFilesystemLayoutError):
        prepare(vault, 'registered')


def test_benchmark_preparation_observes_fixed_outputs_through_owned_guard(vault, monkeypatch):
    from _application.retrieval._preparation import BenchmarkInputs, benchmark_binding
    from _application.retrieval.evaluate import RetrievalEvaluateRequest
    source = put(vault, 'Unrelated.txt')
    output = vault / '.brain/local/compiled-router.json'
    output.parent.mkdir(parents=True)
    output.symlink_to(source)
    observe_opens(monkeypatch, [source])
    context = SimpleNamespace(selected_brain=SimpleNamespace(vault_root=vault))
    with pytest.raises(UnsafeFilesystemLayoutError):
        benchmark_binding(context, RetrievalEvaluateRequest('benchmark.json'),
                          plan=BenchmarkInputs({}, benchmark={}))


@pytest.mark.parametrize('kind', ['missing', 'directory', 'fifo'])
def test_benchmark_preparation_refuses_nonregular_inputs_before_parser(vault, monkeypatch, kind):
    import evaluate_search
    from _application.retrieval._preparation import benchmark_inputs
    from _application.retrieval.evaluate import RetrievalEvaluateRequest
    path = vault / 'benchmark.json'
    if kind == 'directory':
        path.mkdir()
    elif kind == 'fifo':
        if not hasattr(os, 'mkfifo'):
            pytest.skip('Named-pipe fixture requires POSIX')
        os.mkfifo(path)
    def prohibited(*_args, **_kwargs):
        pytest.fail('Nonregular input reached the benchmark parser')
    monkeypatch.setattr(evaluate_search, 'load_benchmark', prohibited)
    context = SimpleNamespace(selected_brain=SimpleNamespace(vault_root=vault))
    with pytest.raises(ValueError, match='existing regular file'):
        benchmark_inputs(context, RetrievalEvaluateRequest('benchmark.json'))


def test_benchmark_preparation_keeps_explicit_local_input_capability(vault, monkeypatch):
    import evaluate_search
    from _application.retrieval._preparation import benchmark_inputs
    from _application.retrieval.evaluate import RetrievalEvaluateRequest
    source = put(vault, '.brain/local/benchmark.json', '{}')
    seen = []
    def parsed(path):
        seen.append(path)
        return {'cases': []}
    monkeypatch.setattr(evaluate_search, 'load_benchmark', parsed)
    context = SimpleNamespace(selected_brain=SimpleNamespace(vault_root=vault))
    result = benchmark_inputs(context, RetrievalEvaluateRequest('.brain/local/benchmark.json'))
    assert seen == [source.resolve()]
    assert result.benchmark == {'cases': []}


@pytest.mark.parametrize('selected', ['source', 'document', 'pdf'])
def test_render_preparation_admits_content_and_output_observations(vault, monkeypatch, selected):
    from _application.shaping._render_preparation import render_binding
    source = put(vault, 'Wiki/Source.md')
    document = put(vault, '_Temporal/Presentations/Deck.md')
    pdf = vault / '_Assets/Generated/Presentations/Deck.pdf'
    pdf.parent.mkdir(parents=True)
    private = put(vault, '.brain/private/Synthetic.txt')
    target = {'source': source, 'document': document, 'pdf': pdf}[selected]
    target.unlink(missing_ok=True)
    target.symlink_to(private)
    observe_opens(monkeypatch, [private])
    context = SimpleNamespace(selected_brain=SimpleNamespace(vault_root=vault))
    request = SimpleNamespace(source='Wiki/Source.md', slug='deck', render=True,
                              output=SimpleNamespace(kind='presentation', preview=False))
    plan = SimpleNamespace(kind='presentation', source='Wiki/Source.md',
                           path='_Temporal/Presentations/Deck.md', content='Synthetic deck', created=False)
    with pytest.raises(SourceAccessError):
        render_binding(context, request, plan=plan)


def test_missing_inputs_and_outputs_remain_absent_observations(tmp_path):
    root = tmp_path / 'empty'
    root.mkdir()
    assert source_manifest(root)['file_count'] == 0
    assert public_file_identity(root, 'Missing.md') is None
    assert owned_file_identity(root, '.brain/local/missing.json') is None
    binding = prepare(root, 'registered')
    outputs = [item for item in binding.observations if item.kind in {'maintenance-output', 'session-output'}]
    assert outputs and all(item.revision is None for item in outputs)


def test_selected_root_alias_is_preserved(vault, tmp_path):
    alias = tmp_path / 'selected'
    alias.symlink_to(vault, target_is_directory=True)
    assert source_manifest(alias) == source_manifest(vault)


def test_explicit_archive_identity_preserves_scope_and_public_separation(vault, monkeypatch):
    source = put(vault, '_Archive/Wiki/Note.md')
    opened = observe_opens(monkeypatch)
    assert public_file_identity(vault, '_Archive/Wiki/Note.md')
    assert source in opened
    alias = vault / 'Wiki.md'
    alias.symlink_to(source)
    with pytest.raises(SourceAccessError):
        public_file_identity(vault, 'Wiki.md')


def test_legacy_identity_remains_a_byte_observation_not_an_internal_grant(tmp_path):
    source = put(tmp_path, 'caller-output.bin', 'Output bytes\n')
    expected = 'sha256:' + hashlib.sha256(b'Output bytes\n').hexdigest()
    assert file_identity(source) == expected
    assert file_identity(tmp_path / 'absent-output') is None


def test_hash_read_failure_has_no_host_path_in_diagnostic(vault, monkeypatch):
    source = put(vault, 'Wiki/Note.md')
    original = Path.open
    def denied(path, *args, **kwargs):
        if path == source:
            raise PermissionError(f'Synthetic host path {source}')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', denied)
    with pytest.raises(SourceAccessError) as caught:
        source_manifest(vault)
    assert str(source) not in str(caught.value)


@pytest.mark.parametrize('route', ['manifest', 'registered'])
@pytest.mark.parametrize('relative', [
    '_Plugins/.gitkeep', '_Config/.gitkeep',
    '.brain-core/skills/.gitkeep', '_Plugins/.metadata/placeholder.md',
    'Wiki/.placeholder.md',
])
def test_hidden_public_corpus_placeholders_are_not_hashed(vault, monkeypatch, route, relative):
    before = source_manifest(vault)
    placeholder = put(vault, relative)
    observe_opens(monkeypatch, [placeholder])
    result = prepare(vault, route)
    manifest = result if route == 'manifest' else result.frozen_inputs['maintenance_sources']
    assert manifest == before


@pytest.mark.parametrize('route', ['manifest', 'registered'])
def test_placeholder_skip_does_not_hide_redirected_meaningful_plugin(vault, monkeypatch, route):
    placeholder = put(vault, '_Plugins/.gitkeep')
    private = put(vault, '.brain/private/Note.md')
    (vault / '_Plugins/Meaningful.md').symlink_to(private)
    observe_opens(monkeypatch, [placeholder, private])
    with pytest.raises(SourceAccessError):
        prepare(vault, route)


def test_meaningful_plugin_content_still_participates_in_manifest(vault):
    put(vault, '_Plugins/.gitkeep')
    plugin = put(vault, '_Plugins/Example/SKILL.md', 'Before\n')
    before = source_manifest(vault)
    plugin.write_text('After\n')
    after = source_manifest(vault)
    assert after['file_count'] == before['file_count']
    assert after['sha256'] != before['sha256']
