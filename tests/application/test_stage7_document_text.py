"""DD-087 document decoding, reported reads and editable persistence."""
import codecs

import pytest

from _common import NonStandardVaultTextError, decode_persisted_document, document_revision
from _application.artefact.read import ArtefactReadRequest, ArtefactLocation
from _application.vault.read_file import VaultReadFileRequest
from _application.resource.read import ReadableResource, ResourceReadRequest
from _application.results import ErrorCode, WarningCode
from command_application import application_for


@pytest.mark.parametrize('raw', [b'', b'plain\x00text', 'é\r\ntext\r'.encode()])
def test_strict_utf8_preserves_existing_semantics_without_classifying(raw, monkeypatch):
    import _common._document_revision as seam
    def unexpected(_):
        pytest.fail('strict UTF-8 must bypass classification')
    monkeypatch.setattr(seam, 'diagnose_text', unexpected)
    content = decode_persisted_document(raw)
    assert content == raw.decode().replace('\r\n', '\n').replace('\r', '\n')
    assert content.revision == document_revision(raw)
    assert content.conversion_code is None


CASES = [(codecs.BOM_UTF8 + b'text', 'utf8_bom'),
         ('text'.encode('utf-16'), 'utf16_bom'),
         ('text'.encode('utf-32'), 'utf32_bom'),
         ('é'.encode() + b'\xc3', 'truncated_utf8'),
         (b'\xff', 'not_utf8'), (b'\xff\x00', 'not_text')]


@pytest.mark.parametrize('raw,code', CASES)
def test_default_refuses_every_nonstandard_code_by_name(raw, code):
    with pytest.raises(NonStandardVaultTextError) as caught:
        decode_persisted_document(raw, source_path='Projects/Damaged.md')
    assert not isinstance(caught.value, UnicodeDecodeError)
    assert caught.value.code == code
    assert 'Projects/Damaged.md' in str(caught.value)
    assert caught.value.remedy in str(caught.value)


@pytest.mark.parametrize('raw,code', CASES[3:])
def test_optin_never_accepts_lossy_or_ambiguous_bytes(raw, code):
    with pytest.raises(NonStandardVaultTextError, match=code):
        decode_persisted_document(raw, convert_lossless=True)


@pytest.mark.parametrize('encoding,code', [('utf-8-sig','utf8_bom'), ('utf-16','utf16_bom'), ('utf-32','utf32_bom')])
@pytest.mark.parametrize('kind', ['artefact','archive','file','skill'])
def test_read_commands_report_conversion_and_raw_revision(command_vault_clone, encoding, code, kind):
    root = command_vault_clone.vault_root
    relative, request = read_target(kind)
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    text = '# Heading\r\né\rtext\n'
    raw = text.encode(encoding)
    path.write_bytes(raw)
    result = application_for(root).invoke(request)
    assert result.status == 'ok', getattr(result, 'error', result)
    assert result.result.content == text.replace('\r\n','\n').replace('\r','\n')
    assert result.result.revision == document_revision(raw)
    assert any(w.code is WarningCode.FOLLOW_UP_REQUIRED and relative in w.message and code in w.message for w in result.warnings)
    assert path.read_bytes() == raw


def read_target(kind):
    if kind == 'artefact':
        return 'Projects/Command Fixture.md', ArtefactReadRequest('Projects/Command Fixture.md')
    if kind == 'archive':
        return '_Archive/Old.md', ArtefactReadRequest('_Archive/Old.md', ArtefactLocation.ARCHIVED)
    if kind == 'file':
        return '_Config/User/preferences-always.md', VaultReadFileRequest('_Config/User/preferences-always.md')
    return '.brain-core/skills/shaping/SKILL.md', ResourceReadRequest(ReadableResource.SKILL, 'core:shaping')


@pytest.mark.parametrize('kind', ['artefact','archive','file','skill'])
@pytest.mark.parametrize('raw,code', CASES[3:])
def test_read_refusals_have_precise_path_and_recovery(command_vault_clone, kind, raw, code):
    root = command_vault_clone.vault_root
    relative, request = read_target(kind)
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    result = application_for(root).invoke(request)
    assert result.status == 'error', result
    assert result.error.code is ErrorCode.CONFLICT
    assert relative in result.error.message and code in result.error.message
    assert result.error.next_action.command_id == ('vault.repair-text' if code == 'truncated_utf8' else 'vault.check')
    if code == 'truncated_utf8':
        assert result.error.next_action.arguments[0].value == (relative,)


@pytest.mark.parametrize('encoding,code', [('utf-8-sig','utf8_bom'), ('utf-16','utf16_bom'), ('utf-32','utf32_bom')])
@pytest.mark.parametrize('resource,relative', [('artefact','Projects/Command Fixture.md'), ('style','_Config/Styles/writing.md')])
def test_opened_edit_uses_raw_revision_reports_conversion_and_persists_utf8(command_vault_clone, monkeypatch, encoding, code, resource, relative):
    from _application.document._types import DocumentLocator, DocumentResource
    from _application.document.write_body import DocumentWriteBodyRequest, DocumentWriteBodyOperation
    from _application._mutation_support import InlineContent
    import edit
    from _common import load_compiled_router

    root = command_vault_clone.vault_root
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text() if path.exists() else '# Original\né\n'
    raw = text.encode(encoding)
    path.write_bytes(raw)
    # Router handling of unreadable index sources belongs to the preceding stages.
    router = load_compiled_router(root)
    import _lifecycle.derived_cache_state as cache
    monkeypatch.setattr(cache, 'require_fresh_compiled_router', lambda _root: router)
    reference = relative if resource == 'artefact' else 'writing'
    opened = edit.open_document(root, load_compiled_router(root), resource, reference)
    assert opened.revision == document_revision(raw)
    assert opened.conversion_code == code
    request = DocumentWriteBodyRequest(
        document=DocumentLocator(DocumentResource(resource), reference),
        expected_revision=opened.revision, operation=DocumentWriteBodyOperation.REPLACE,
        content=InlineContent('# Edited\né\n'))
    result = application_for(root, profile='operator').invoke(request)
    assert result.status == 'ok', getattr(result, 'error', result)
    persisted = path.read_bytes()
    assert '# Edited\né\n' in persisted.decode('utf-8')
    assert not persisted.startswith(codecs.BOM_UTF8)
    assert result.result.revision == document_revision(persisted)
    assert any(relative in w.message and code in w.message for w in result.warnings)


def test_portable_template_type_and_router_collection_reads_remain_strict(tmp_path):
    from _portable.type_definitions import read_template_exact, read_type_exact
    from _portable.router_collections import read_memory_exact
    from _portable.named_documents import read_named_document
    from _common import read_file_content
    (tmp_path / 'Marked.md').write_bytes('hello'.encode('utf-16'))
    router = {'artefacts': [{'key': 'example', 'template_file': 'Marked.md', 'taxonomy_file': 'Marked.md'}],
              'memories': [{'name': 'example', 'memory_doc': 'Marked.md'}],
              'styles': [{'name': 'example', 'style_doc': 'Marked.md'}]}
    for read in [lambda: read_file_content(tmp_path, 'Marked.md'),
                 lambda: read_template_exact(router, tmp_path, 'example'),
                 lambda: read_type_exact(router, tmp_path, 'example'),
                 lambda: read_memory_exact(router, tmp_path, 'example'),
                 lambda: read_named_document(router, tmp_path, 'style', 'example')]:
        with pytest.raises(NonStandardVaultTextError, match='Marked.md.*utf16_bom'):
            read()


@pytest.mark.parametrize('resource,reference,collection,path_field', [
    (ReadableResource.MEMORY, 'brain-core-reference', 'memories', 'memory_doc'),
    (ReadableResource.TEMPLATE, 'designs', 'artefacts', 'template_file'),
    (ReadableResource.TYPE, 'designs', 'artefacts', 'taxonomy_file'),
])
def test_resource_command_optin_does_not_change_strict_collection_defaults(command_vault_clone, resource, reference, collection, path_field):
    from _common import load_compiled_router, markdown_rel_path
    root = command_vault_clone.vault_root
    router = load_compiled_router(root)
    entry = next(item for item in router[collection] if item.get('name', item.get('key')) == reference)
    relative = markdown_rel_path(entry[path_field])
    path = root / relative
    raw = path.read_text().encode('utf-16')
    path.write_bytes(raw)
    result = application_for(root).invoke(ResourceReadRequest(resource, reference))
    assert result.status == 'ok', getattr(result, 'error', result)
    assert result.result.revision == document_revision(raw)
    assert any(relative in w.message and 'utf16_bom' in w.message for w in result.warnings)
    assert path.read_bytes() == raw


@pytest.mark.parametrize('raw,code', CASES[3:])
def test_opened_edit_refuses_lossy_bytes_before_writing(command_vault_clone, monkeypatch, raw, code):
    from _application.document._types import DocumentLocator, DocumentResource
    from _application.document.write_body import DocumentWriteBodyRequest, DocumentWriteBodyOperation
    from _application._mutation_support import InlineContent
    from _common import load_compiled_router
    import _lifecycle.derived_cache_state as cache
    root = command_vault_clone.vault_root
    relative = 'Projects/Command Fixture.md'
    path = root / relative
    router = load_compiled_router(root)
    monkeypatch.setattr(cache, 'require_fresh_compiled_router', lambda _root: router)
    path.write_bytes(raw)
    request = DocumentWriteBodyRequest(
        document=DocumentLocator(DocumentResource.ARTEFACT, relative),
        expected_revision=document_revision(raw), operation=DocumentWriteBodyOperation.REPLACE,
        content=InlineContent('replacement'))
    result = application_for(root).invoke(request)
    assert result.error.code is ErrorCode.CONFLICT
    assert relative in result.error.message and code in result.error.message
    assert result.effects == 'none'
    assert path.read_bytes() == raw


@pytest.mark.parametrize('kind', ['artefact', 'archive', 'file', 'skill'])
@pytest.mark.parametrize('encoding,code', [('utf-8-sig', 'utf8_bom'), ('utf-16', 'utf16_bom'), ('utf-32', 'utf32_bom')])
def test_converted_pages_budget_warnings_and_continuation(command_vault_clone, kind, encoding, code):
    from dataclasses import replace
    import json
    from _application.projection import canonical_result_envelope
    root = command_vault_clone.vault_root
    relative, request = read_target(kind)
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    text = ('文"\\🧠\r\n' * 7000)
    raw = text.encode(encoding)
    path.write_bytes(raw)
    app = application_for(root)
    pages, cursor, first_cursor = [], None, None
    while True:
        result = app.invoke(replace(request, cursor=cursor))
        assert result.status == 'ok', getattr(result, 'error', result)
        envelope = canonical_result_envelope(result)
        assert len(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) <= 16000
        assert result.result.revision == document_revision(raw)
        assert any(relative in w.message and code in w.message for w in result.warnings)
        pages.append(result.result.content)
        cursor = result.result.range.next_cursor
        first_cursor = first_cursor or cursor
        if cursor is None:
            break
    assert len(pages) > 1
    assert ''.join(pages) == text.replace('\r\n', '\n')
    # The normalised text is identical, but the continuation pins original bytes.
    path.write_bytes(text.encode('utf-8'))
    result = app.invoke(replace(request, cursor=first_cursor))
    assert result.error.code is ErrorCode.CONFLICT
    assert path.read_bytes() == text.encode('utf-8')


@pytest.mark.parametrize('target', ['read', 'edit'])
@pytest.mark.parametrize('raw,code', CASES[3:])
def test_access_prepare_preserves_named_text_refusals(command_vault_clone, monkeypatch, target, raw, code):
    from _application.access.prepare import AccessPrepareRequest, PrepareCommand
    from _common import load_compiled_router
    import _lifecycle.derived_cache_state as cache
    root = command_vault_clone.vault_root
    relative = 'Projects/Command Fixture.md'
    path = root / relative
    router = load_compiled_router(root)
    if target == 'edit':
        monkeypatch.setattr(cache, 'require_fresh_compiled_router', lambda _root: router)
    path.write_bytes(raw)
    command, arguments = ('vault.read-file', {'path': relative}) if target == 'read' else (
        'document.write-body', {'document': {'resource': 'artefact', 'reference': relative},
                               'expected_revision': document_revision(raw), 'operation': 'replace',
                               'content': {'source': 'inline', 'content': 'replacement'}})
    app = application_for(root)
    result = app.invoke(AccessPrepareRequest(PrepareCommand(command, arguments)))
    assert result.command_id == 'access.prepare'
    assert result.error.code is ErrorCode.CONFLICT
    assert relative in result.error.message and code in result.error.message
    assert result.error.next_action.command_id == ('vault.repair-text' if code == 'truncated_utf8' else 'vault.check')
    if code == 'truncated_utf8':
        assert result.error.next_action.arguments[0].value == (relative,)
    assert result.effects == 'none'
    assert all(intent.command_id == "access.prepare" for intent in app._context.authorisation.receipts.intents.values())
    assert path.read_bytes() == raw


def test_prepared_converted_read_invalidates_on_equivalent_raw_reencoding(command_vault_clone):
    from dataclasses import replace
    from _application.access.prepare import AccessPrepareRequest, PrepareCommand
    from _application.registry import current_request_resolver
    root = command_vault_clone.vault_root
    relative = '_Config/User/preferences-always.md'
    path = root / relative
    text = '# Identical\r\né\n'
    original = codecs.BOM_UTF16_LE + text.encode('utf-16-le')
    replacement = codecs.BOM_UTF16_BE + text.encode('utf-16-be')
    assert decode_persisted_document(original, convert_lossless=True) == decode_persisted_document(replacement, convert_lossless=True)
    path.write_bytes(original)
    app = application_for(root, initial_commands={'access.prepare', 'access.request', 'access.status'})
    prepared = app.invoke(AccessPrepareRequest(PrepareCommand('vault.read-file', {'path': relative})))
    assert prepared.status == 'ok', prepared
    operation = prepared.result
    grant = app.invoke(current_request_resolver().resolve('access.request', {'consent': {
        'scope': 'operation', 'operation_id': operation.operation_id,
        'digest': operation.digest, 'review': operation.review}}))
    assert grant.status == 'ok', grant
    path.write_bytes(replacement)
    app._context = replace(app._context, operation_id=operation.operation_id)
    result = app.invoke(VaultReadFileRequest(relative))
    assert result.error.code is ErrorCode.CONFLICT
    assert result.effects == 'none'
    assert path.read_bytes() == replacement
    assert result.error.details.reason == 'stale_operation'
    assert 'Prepared scope or inputs changed' in result.error.message


@pytest.mark.parametrize('encoding,code', [('utf-8-sig', 'utf8_bom'), ('utf-16', 'utf16_bom'), ('utf-32', 'utf32_bom')])
def test_committed_frontmatter_conversion_warns_when_index_refresh_fails(command_vault_clone, monkeypatch, encoding, code):
    from _application.document._types import DocumentLocator, DocumentResource
    from _application.document.update_frontmatter import DocumentUpdateFrontmatterRequest
    from _application._mutation_support import FrontmatterField
    from _application.workspace_context import WorkspaceMutationPartial
    from _common import load_compiled_router, parse_frontmatter
    from _portable import router_maintenance
    import _lifecycle.derived_cache_state as cache
    root = command_vault_clone.vault_root
    relative = 'Projects/Command Fixture.md'
    path = root / relative
    router = load_compiled_router(root)
    fields, body = parse_frontmatter(path.read_text())
    from _common import serialize_frontmatter
    fields['type'] = 'living/wiki'
    raw = serialize_frontmatter(fields, body=body).encode(encoding)
    path.write_bytes(raw)
    monkeypatch.setattr(cache, 'require_fresh_compiled_router', lambda _root: router)
    def fail_router(*args, **kwargs):
        raise OSError('forced index failure')
    monkeypatch.setattr(router_maintenance, 'maintain_router', fail_router)
    request = DocumentUpdateFrontmatterRequest(
        DocumentLocator(DocumentResource.ARTEFACT, relative), document_revision(raw),
        (FrontmatterField('type', 'living/project'),))
    result = application_for(root).invoke(request)
    assert isinstance(result, WorkspaceMutationPartial), result
    assert result.error.next_action.command_id == 'runtime.refresh-router'
    assert any(w.code is WarningCode.FOLLOW_UP_REQUIRED and relative in w.message and code in w.message for w in result.warnings)
    assert result.committed_effects[0].subject == relative
    persisted = path.read_bytes()
    assert not persisted.startswith(codecs.BOM_UTF8)
    fields, _ = parse_frontmatter(persisted.decode('utf-8'))
    assert fields['type'] == 'living/project'
