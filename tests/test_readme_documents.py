"""The existing root README is an unmanaged, revisioned file document."""
from pathlib import Path
import builtins

import pytest

import edit
from _application.document._types import DocumentLocator, DocumentResource, decode_document
from _common._source_access import SourceAccessError


def test_file_locator_requires_exact_readme():
    assert decode_document({'resource': 'file', 'reference': 'README.md'}) == DocumentLocator(DocumentResource.FILE, 'README.md')
    for reference in ('readme.md', './README.md', 'Wiki/README.md', 'Other.md'):
        with pytest.raises(ValueError):
            DocumentLocator(DocumentResource.FILE, reference)


@pytest.mark.parametrize('operation', ['edit', 'append', 'prepend', 'replace_text', 'frontmatter'])
def test_readme_edits_preserve_plain_text_without_artefact_lifecycle(tmp_path, operation):
    path = tmp_path / 'README.md'
    path.write_text('# Guide\n\nOriginal text.\n')
    initial_paths = set(tmp_path.iterdir())
    opened = edit.open_document(tmp_path, {}, 'file', 'README.md')
    if operation == 'replace_text':
        plan = edit.plan_document_edit(opened, operation='replace_text', old_text='Original', new_text='Updated')
    elif operation == 'frontmatter':
        plan = edit.plan_document_edit(opened, frontmatter_changes={'status': 'draft'})
    else:
        plan = edit.plan_document_edit(opened, operation=operation, body='Updated text.\n', target=':body', scope='section')
    assert edit.plan_document_lifecycle(tmp_path, {}, plan).artefact_transition is None
    result = edit.apply_document_edit(tmp_path, {}, plan)
    assert result['path'] == 'README.md'
    assert result['revision'] != opened.revision
    if operation == 'frontmatter':
        assert path.read_text().startswith('---\n')
        assert 'statusdate' not in path.read_text()
    else:
        assert not path.read_text().startswith('---')
        assert 'Updated' in path.read_text()
    assert set(tmp_path.iterdir()) == initial_paths


def test_existing_readme_frontmatter_is_preserved(tmp_path):
    path = tmp_path / 'README.md'
    path.write_text('---\ntitle: Existing\n---\nOld\n')
    opened = edit.open_document(tmp_path, {}, 'file', 'README.md')
    plan = edit.plan_document_edit(opened, body='New\n', target=':body', scope='section')
    edit.apply_document_edit(tmp_path, {}, plan)
    assert 'title: Existing' in path.read_text()


@pytest.mark.parametrize('target', ['Wiki/Other.md', '.brain/local/Synthetic.md', '.brain-core/guide.md'])
def test_readme_canonical_target_cannot_redirect_before_content_read(tmp_path, monkeypatch, target):
    source = tmp_path / target
    source.parent.mkdir(parents=True)
    source.write_text('Synthetic only\n')
    (tmp_path / 'README.md').symlink_to(source)
    original = builtins.open
    def checked(path, *args, **kwargs):
        if isinstance(path, (str, Path)) and Path(path).resolve() == source:
            pytest.fail('README refusal opened redirected content')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(builtins, 'open', checked)
    with pytest.raises(SourceAccessError):
        edit.open_document(tmp_path, {}, 'file', 'README.md')


def test_readme_sink_is_rechecked_before_writer(tmp_path):
    path = tmp_path / 'README.md'
    path.write_text('Original\n')
    opened = edit.open_document(tmp_path, {}, 'file', 'README.md')
    plan = edit.plan_document_edit(opened, body='Updated\n', target=':body', scope='section')
    other = tmp_path / 'Other.md'
    other.write_text('Unrelated\n')
    path.unlink()
    path.symlink_to(other)
    with pytest.raises(SourceAccessError):
        edit.apply_document_edit(tmp_path, {}, plan)
    assert other.read_text() == 'Unrelated\n'


def test_root_check_allows_only_exact_readme_name(tmp_path, monkeypatch):
    import check
    for name in ('README.md', 'readme.md', 'Other.md'):
        (tmp_path / name).write_text('Synthetic\n')
    monkeypatch.setattr(check.os, 'listdir', lambda root: ['README.md', 'readme.md', 'Other.md'])
    findings = check.check_root_files(str(tmp_path), {})
    assert {f['file'] for f in findings} == {'readme.md', 'Other.md'}


def test_readme_application_uses_current_read_revision_and_rejects_stale_write(command_vault_clone):
    from _application._mutation_support import InlineContent, FrontmatterField
    from _application.document.write_body import DocumentWriteBodyRequest, DocumentWriteBodyOperation
    from _application.document.update_frontmatter import DocumentUpdateFrontmatterRequest
    from _application.results import ErrorCode
    from _application.vault.read_file import VaultReadFileRequest
    from command_application import application_for

    root = command_vault_clone.vault_root
    path = root / 'README.md'
    path.write_text('# Guide\n\nPlain text.\n')
    application = application_for(root)
    read = application.invoke(VaultReadFileRequest('README.md'))
    assert read.status == 'ok', read
    locator = DocumentLocator(DocumentResource.FILE, 'README.md')
    request = DocumentWriteBodyRequest(locator, read.result.revision,
                                       DocumentWriteBodyOperation.REPLACE,
                                       InlineContent('Updated guide.\n'))
    written = application.invoke(request)
    assert written.status == 'ok', written
    assert path.read_text() == 'Updated guide.\n'
    assert not path.read_text().startswith('---')
    stale = application.invoke(request)
    assert stale.status == 'error' and stale.error.code is ErrorCode.CONFLICT
    patched = application.invoke(DocumentUpdateFrontmatterRequest(
        locator, written.result.revision, (FrontmatterField('title', 'Guide'),)))
    assert patched.status == 'ok', patched
    assert 'title: Guide' in path.read_text()
