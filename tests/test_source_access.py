"""Small source-policy controls with synthetic bytes and real owner boundaries."""
from pathlib import Path
import builtins

import pytest

from _common._source_access import (
    SourceAccessError, resolve_public_source, resolve_archive_source,
    read_public_content, resolve_named_destination,
)
from _common import MissingFileResult
from _portable.artefact_read import read_artefact
from _portable.named_documents import read_named_document
from _portable.type_definitions import read_template_exact
from _portable.vault_files import read_archived_artefact


def put(root, relative, content='Synthetic source\n'):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def forbid_content_open(monkeypatch, *paths):
    original = builtins.open
    forbidden = {Path(p).resolve() for p in paths}

    def checked(path, *args, **kwargs):
        if isinstance(path, (str, Path)) and Path(path).resolve() in forbidden:
            pytest.fail('Refused source reached the content reader')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(builtins, 'open', checked)


@pytest.mark.parametrize('relative', [
    'Wiki/Note.md', '_Config/Templates/Custom.md', 'Custom/Public-template.md',
    '.brain-core/guide.md', '.brain-core/skills/example/SKILL.md',
    '.brain-core/artefact-library/living/example/template.md',
])
def test_public_success_and_root_alias(tmp_path, relative):
    root = tmp_path / 'vault'
    source = put(root, relative)
    alias = tmp_path / 'root-alias'
    alias.symlink_to(root, target_is_directory=True)
    assert resolve_public_source(alias, relative) == source.resolve()
    assert read_public_content(alias, relative) == 'Synthetic source\n'


@pytest.mark.parametrize('relative', [
    '_Archive/Wiki/Note.md', '_Temporal/Notes/_Archive/Note.md',
    'Wiki/Project/_Archive/Note.md',
])
def test_archive_authority_is_explicit(tmp_path, relative):
    source = put(tmp_path, relative)
    assert resolve_archive_source(tmp_path, relative) == source
    with pytest.raises(SourceAccessError):
        resolve_public_source(tmp_path, relative)
    assert read_archived_artefact(tmp_path, relative) == 'Synthetic source\n'


@pytest.mark.parametrize('target', ['private', 'archive', 'outside', 'directory'])
def test_public_alias_refused_before_content_open(tmp_path, monkeypatch, target):
    root = tmp_path / 'vault'
    root.mkdir()
    if target == 'outside':
        source = put(tmp_path / 'vault-neighbour', 'Note.md')
    elif target == 'directory':
        source = root / 'Folder'
        source.mkdir()
    else:
        source = put(root, '.brain/local/Note.md' if target == 'private' else '_Archive/Note.md')
    alias = root / 'Alias.md'
    alias.symlink_to(source, target_is_directory=target == 'directory')
    forbid_content_open(monkeypatch, source)
    with pytest.raises(SourceAccessError) as caught:
        read_public_content(root, 'Alias.md')
    assert str(source) not in str(caught.value)


@pytest.mark.parametrize('target', ['Wiki/Active.md', '_Archive/.private/Note.md', '.brain/local/Note.md'])
def test_archive_alias_cannot_acquire_other_scope(tmp_path, monkeypatch, target):
    source = put(tmp_path, target)
    alias = tmp_path / '_Archive/Alias.md'
    alias.parent.mkdir(exist_ok=True)
    alias.symlink_to(source)
    forbid_content_open(monkeypatch, source)
    result = read_archived_artefact(tmp_path, '_Archive/Alias.md')
    assert isinstance(result, dict) and 'error' in result


@pytest.mark.parametrize('reader', ['path', 'key', 'named', 'template'])
def test_final_inferred_candidate_is_guarded_at_owner(tmp_path, monkeypatch, reader):
    source = put(tmp_path, '.brain/local/Synthetic.md')
    alias = tmp_path / 'Wiki/Note.md'
    alias.parent.mkdir()
    alias.symlink_to(source)
    forbid_content_open(monkeypatch, source)
    router = {
        'artefact_index': {'wiki/note': {'path': 'Wiki/Note', 'key': 'wiki/note'}},
        'skills': [{'name': 'note', 'source': 'user', 'skill_doc': 'Wiki/Note'}],
        'artefacts': [{'key': 'wiki', 'template_file': 'Wiki/Note'}],
    }
    with pytest.raises(SourceAccessError):
        if reader == 'path':
            read_artefact(router, str(tmp_path), 'Wiki/Note')
        elif reader == 'key':
            read_artefact(router, str(tmp_path), 'wiki/note')
        elif reader == 'named':
            read_named_document(router, str(tmp_path), 'skill', 'note')
        else:
            read_template_exact(router, str(tmp_path), 'wiki')


def test_missing_is_not_policy_refusal(tmp_path):
    with pytest.raises(FileNotFoundError):
        resolve_public_source(tmp_path, 'Missing.md')
    assert isinstance(read_public_content(tmp_path, 'Missing'), MissingFileResult)
    assert isinstance(read_archived_artefact(tmp_path, '_Archive/Missing'), MissingFileResult)


def test_inference_finishes_before_admitting_actual_candidate(tmp_path):
    put(tmp_path, 'Wiki/Note.md', 'Chosen Markdown\n')
    private = put(tmp_path, '.brain/local/Unused')
    (tmp_path / 'Wiki/Note').symlink_to(private)
    assert read_artefact({}, str(tmp_path), 'Wiki/Note') == 'Chosen Markdown\n'


def test_named_destination_canonical_policy_and_missing_leaf(tmp_path):
    private = tmp_path / '.brain/local'
    private.mkdir(parents=True)
    config = tmp_path / '_Config'
    config.mkdir()
    (config / 'Styles').symlink_to(private, target_is_directory=True)
    with pytest.raises(SourceAccessError):
        resolve_named_destination(tmp_path, '_Config/Styles/New.md')
    assert not (private / 'New.md').exists()
    assert resolve_named_destination(tmp_path, 'Custom/New.md') == tmp_path / 'Custom/New.md'


def test_backlink_snapshot_does_not_bypass_source_admission(tmp_path, monkeypatch):
    from _common._wikilinks import build_wikilink_pattern, plan_wikilink_rewrites, require_readable_wikilink_plan
    from _common import decode_persisted_document
    source = put(tmp_path, '.brain/local/Note.md')
    alias = tmp_path / 'Wiki/Note.md'
    alias.parent.mkdir()
    alias.symlink_to(source)
    snapshot = decode_persisted_document(b'[[Old]]', source_path=str(alias))
    forbid_content_open(monkeypatch, source)
    plan = plan_wikilink_rewrites(tmp_path, build_wikilink_pattern('Old'),
                                 lambda m: '[[New]]', paths=['Wiki/Note.md'],
                                 read_snapshot={'Wiki/Note.md': snapshot})
    assert not plan.writes
    with pytest.raises(ValueError):
        require_readable_wikilink_plan(plan)


def test_mutation_scan_refuses_unsafe_auxiliary_before_effects(tmp_path, monkeypatch):
    import fix_links
    source = put(tmp_path, '.brain/local/Note.md')
    alias = tmp_path / 'Wiki/Note.md'
    alias.parent.mkdir()
    alias.symlink_to(source)
    forbid_content_open(monkeypatch, source)
    with pytest.raises(ValueError):
        fix_links.plan_link_fixes(str(tmp_path), router={})
    scan = fix_links.scan_and_resolve(str(tmp_path), router={}, for_mutation=False)
    assert scan['excluded_sources'] == 1
    assert scan['warnings']


def test_render_preserves_archive_source_and_core_template(tmp_path):
    from datetime import datetime, timezone
    from _render_plan import plan_render_document
    import shape_presentation
    put(tmp_path, '_Archive/Old.md')
    put(tmp_path, '.brain-core/artefact-library/temporal/presentations/template.md', '# Template\n')
    plan = plan_render_document(str(tmp_path), {'source': '_Archive/Old.md', 'slug': 'example'},
                                kind='presentation', read_template=shape_presentation._read_template,
                                effective_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert plan.created and plan.content == '# Template\n'
    assert not (tmp_path / plan.path).exists()


@pytest.mark.parametrize('owner', ['artefact', 'resource'])
def test_source_refusal_maps_to_invalid_request_at_read_owner(tmp_path, monkeypatch, owner):
    from types import SimpleNamespace
    from _application.results import ErrorCode
    from _application.artefact.read import read_result as read_artefact_result, ArtefactReadRequest
    from _application.resource.read import read_result as read_resource_result, ResourceReadRequest, ReadableResource
    import _portable.artefact_read as artefact_module
    import _application._named_documents as named_module

    source = put(tmp_path, '.brain/local/Source.md')
    alias = tmp_path / 'Wiki/Note.md'
    alias.parent.mkdir()
    alias.symlink_to(source)
    router = {'skills': [{'name': 'note', 'source': 'user', 'skill_doc': 'Wiki/Note.md'}]}
    monkeypatch.setattr(artefact_module, 'load_compiled_router', lambda root: router)
    monkeypatch.setattr(named_module, '_current_router', lambda root: router)
    forbid_content_open(monkeypatch, source)
    context = SimpleNamespace(selected_brain=SimpleNamespace(vault_root=tmp_path))
    result = (read_artefact_result(context, ArtefactReadRequest('Wiki/Note.md')) if owner == 'artefact'
              else read_resource_result(context, ResourceReadRequest(ReadableResource.SKILL, 'note')))
    assert result.error.code is ErrorCode.INVALID_REQUEST
    assert str(source) not in result.error.message


@pytest.mark.parametrize('requested,destination', [
    ('_Archive/Wiki/Alias.md', 'Wiki/_Archive/Other.md'),
    ('Wiki/_Archive/Alias.md', '_Archive/Wiki/Other.md'),
    ('Wiki/Project/_Archive/Alias.md', 'Wiki/Other/_Archive/Other.md'),
])
def test_archive_alias_cannot_change_requested_archive_root(tmp_path, monkeypatch, requested, destination):
    source = put(tmp_path, destination)
    alias = tmp_path / requested
    alias.parent.mkdir(parents=True, exist_ok=True)
    alias.symlink_to(source)
    forbid_content_open(monkeypatch, source)
    with pytest.raises(SourceAccessError, match='requested archive scope'):
        resolve_archive_source(tmp_path, requested)


def test_archive_alias_within_requested_scope_is_permitted(tmp_path):
    source = put(tmp_path, '_Archive/Wiki/Actual.md')
    alias = tmp_path / '_Archive/Wiki/Alias.md'
    alias.symlink_to(source)
    assert resolve_archive_source(tmp_path, '_Archive/Wiki/Alias.md') == source


def test_markdown_inference_oserror_has_safe_diagnostic(tmp_path, monkeypatch):
    def unavailable(path):
        raise PermissionError('synthetic external location must not appear')
    monkeypatch.setattr(Path, 'exists', unavailable)
    with pytest.raises(SourceAccessError) as caught:
        read_public_content(tmp_path, 'Wiki/Note')
    assert 'synthetic external location' not in str(caught.value)


def test_public_ancestor_alias_is_admitted_before_read(tmp_path, monkeypatch):
    source = put(tmp_path, '.brain/local/Synthetic.md')
    (tmp_path / 'Wiki').symlink_to(source.parent, target_is_directory=True)
    forbid_content_open(monkeypatch, source)
    with pytest.raises(SourceAccessError):
        read_public_content(tmp_path, 'Wiki/Synthetic.md')


def test_redirected_archive_root_does_not_redefine_authority(tmp_path, monkeypatch):
    source = put(tmp_path, 'Wiki/_Archive/Note.md')
    (tmp_path / '_Archive').symlink_to(source.parent, target_is_directory=True)
    forbid_content_open(monkeypatch, source)
    with pytest.raises(SourceAccessError):
        resolve_archive_source(tmp_path, '_Archive/Note.md')


def test_nonregular_source_is_refused_before_open(tmp_path, monkeypatch):
    import os
    if not hasattr(os, 'mkfifo'):
        pytest.skip('Native FIFO fixture requires POSIX')
    source = tmp_path / 'Nonregular.md'
    os.mkfifo(source)
    forbid_content_open(monkeypatch, source)
    with pytest.raises(SourceAccessError):
        read_public_content(tmp_path, 'Nonregular.md')


def test_archive_list_reports_exclusion_and_direct_mutation_consumers_refuse(tmp_path, monkeypatch):
    from _portable.vault_files import list_archived_artefacts
    from _portable.artefact_listing import list_combined_from_vault
    import _portable.vault_files as module
    source = put(tmp_path, '.brain/local/Synthetic.md')
    alias = tmp_path / '_Archive/Bad.md'
    alias.parent.mkdir()
    alias.symlink_to(source)
    put(tmp_path, '_Archive/Good.md', '---\ntype: living/wiki\ncreated: 2026-01-01\n---\nGood\n')
    monkeypatch.setattr(module, 'iter_archived_artefact_paths', lambda root, router, **kwargs: iter(['_Archive/Bad.md', '_Archive/Good.md']))
    forbid_content_open(monkeypatch, source)
    with pytest.raises(SourceAccessError, match='coverage is incomplete'):
        list_archived_artefacts({}, tmp_path)
    page = list_combined_from_vault(tmp_path, location='archived', router={})
    assert page['excluded_sources'] == 1
    assert [item['path'] for item in page['items']] == ['_Archive/Good.md']


def test_archive_inventory_refuses_unsafe_root_before_walk(tmp_path, monkeypatch):
    from _common._artefacts import iter_archived_artefact_paths
    from _portable.vault_files import list_archived_artefacts
    from _portable.artefact_listing import list_combined_from_vault
    import _common._artefacts as module
    root = tmp_path / 'vault'
    root.mkdir()
    outside = tmp_path / 'other'
    outside.mkdir()
    (root / '_Archive').symlink_to(outside, target_is_directory=True)
    original_walk = module.os.walk
    def admitted_walk(path, *args, **kwargs):
        assert not Path(path).resolve().is_relative_to(outside)
        return original_walk(path, *args, **kwargs)
    monkeypatch.setattr(module.os, 'walk', admitted_walk)
    with pytest.raises(SourceAccessError):
        list(iter_archived_artefact_paths(root, {}))
    with pytest.raises(SourceAccessError):
        list_archived_artefacts({}, root)
    page = list_combined_from_vault(root, location='archived', router={})
    assert page['items'] == [] and page['excluded_sources'] == 1


def test_document_archive_source_admission_does_not_grant_root_archive_write(tmp_path, monkeypatch):
    import edit
    source = put(tmp_path, '_Archive/Note.md')
    monkeypatch.setattr(edit, 'resolve_and_validate_folder', lambda root, router, reference: ('_Archive/Note.md', {}))
    forbid_content_open(monkeypatch, source)
    with pytest.raises(ValueError, match='protected folder'):
        edit.open_document(tmp_path, {}, 'artefact', '_Archive/Note.md')
