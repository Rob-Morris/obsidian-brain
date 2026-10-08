"""Clear text repairs preview exact bytes and account for each committed path."""

from pathlib import Path
import os
import codecs

import pytest

from _application.vault import repair_text
from _application.vault.repair_text import RepairTextRequest
from _application.registry import current_application_catalogue, current_request_resolver
from _application.results import ErrorCode
from _application.types import Authority, InitialAuthorisationClass
from command_application import application_for
import check


@pytest.mark.parametrize('data,fixed,code', [
    (codecs.BOM_UTF8 + b'body\r\n', b'body\r\n', 'utf8_bom'),
    ('body\r\n'.encode('utf-16'), b'body\r\n', 'utf16_bom'),
    ('body\r\n'.encode('utf-32'), b'body\r\n', 'utf32_bom'),
    ('café'.encode() + b'\xe2\x82', 'café'.encode(), 'truncated_utf8'),
])
def test_preview_and_apply(command_vault_clone, data, fixed, code):
    root = command_vault_clone.vault_root
    path = root / 'Projects/Encoding.md'
    path.write_bytes(data)
    request = RepairTextRequest(('Projects/Encoding.md',))
    result = application_for(root, dry_run=True).invoke(request)
    assert result.status == 'ok', result
    preview, = result.result.files
    assert (preview.code, preview.before_bytes, preview.after_bytes) == (code, len(data), len(fixed))
    assert result.committed_effects == () and path.read_bytes() == data
    if code == 'truncated_utf8':
        assert preview.dropped_hex == 'e2 82' and preview.dropped_windows1252
    result = application_for(root).invoke(request)
    assert result.status == 'ok', result
    assert path.read_bytes() == fixed
    assert any(effect.subject == 'Projects/Encoding.md' for effect in result.committed_effects)
    again = application_for(root).invoke(RepairTextRequest())
    assert again.status == 'ok' and not again.result.files


def test_refuses_path_without_current_clear_finding(command_vault_clone):
    root = command_vault_clone.vault_root
    for relative in ('Projects/Missing.md', '.brain/config.yaml'):
        result = application_for(root).invoke(RepairTextRequest((relative,)))
        assert result.status == 'error' and result.effects == 'none'
        assert relative in result.error.message


def test_changed_after_plan_is_skipped(command_vault_clone, monkeypatch):
    root = command_vault_clone.vault_root
    path = root / 'Projects/Encoding.md'
    path.write_bytes(codecs.BOM_UTF8 + b'old')
    real = repair_text.admit_owner
    def change(*args, **kwargs):
        real(*args, **kwargs)
        path.write_bytes(b'new editor content')
    monkeypatch.setattr(repair_text, 'admit_owner', change)
    result = application_for(root).invoke(RepairTextRequest())
    assert result.status == 'ok' and result.result.files[0].status == 'skipped: changed'
    assert result.committed_effects == ()
    assert path.read_bytes() == b'new editor content'


def test_one_write_failure_is_partial_with_only_written_effects(command_vault_clone, monkeypatch):
    root = command_vault_clone.vault_root
    for name in ('A', 'B'):
        (root / f'Projects/{name}.md').write_bytes(codecs.BOM_UTF8 + b'body')
    real = repair_text.repair_candidate
    def fail(root, relative, *args, **kwargs):
        if Path(relative).name == 'A.md':
            raise OSError('write refused')
        return real(root, relative, *args, **kwargs)
    monkeypatch.setattr(repair_text, 'repair_candidate', fail)
    result = application_for(root).invoke(RepairTextRequest())
    assert result.status == 'partial', result
    assert [effect.subject for effect in result.committed_effects] == ['Projects/B.md']
    assert 'Projects/A.md' in result.error.message
    assert (root / 'Projects/A.md').read_bytes().startswith(codecs.BOM_UTF8)
    assert (root / 'Projects/B.md').read_bytes() == b'body'


def test_missing_router_and_utf16_taxonomy_are_repairable(command_vault_clone):
    root = command_vault_clone.vault_root
    taxonomy = root / '_Config/Taxonomy/Living/projects.md'
    taxonomy.write_bytes(taxonomy.read_text().encode('utf-16'))
    (root / '.brain/local/compiled-router.json').unlink()
    result = application_for(root).invoke(RepairTextRequest(('_Config/Taxonomy/Living/projects.md',)))
    assert result.status == 'ok', result
    assert not taxonomy.read_bytes().startswith(codecs.BOM_UTF16_LE)
    assert (root / '.brain/local/compiled-router.json').is_file()


def test_check_reports_clear_set_before_router_gate(command_vault_clone):
    root = command_vault_clone.vault_root
    for name, data in [('utf8', codecs.BOM_UTF8 + b'body'), ('utf16', 'body'.encode('utf-16')),
                       ('utf32', 'body'.encode('utf-32')), ('cut', 'é'.encode() + b'\xe2')]:
        (root / f'Projects/{name}.md').write_bytes(data)
    (root / '.brain/local/compiled-router.json').unlink()
    findings = [f for f in check.run_checks(root)['findings'] if f['check'] == 'text_encoding']
    assert {f['code'] for f in findings} == {'utf8_bom', 'utf16_bom', 'utf32_bom', 'truncated_utf8'}
    assert all(f['repair']['command_id'] == 'vault.repair-text' for f in findings)
    assert next(f for f in findings if f['code'] == 'utf8_bom')['severity'] == 'warning'


def test_command_authority_and_strict_paths():
    entry = current_application_catalogue().resolve(RepairTextRequest())
    assert entry.authority is Authority.MAINTAINER
    assert entry.initial_class is InitialAuthorisationClass.CONTENT
    resolver = current_request_resolver()
    for payload in ({'paths': 'one.md'}, {'paths': ['../escape.md']}, {'unexpected': True}):
        with pytest.raises(ValueError):
            resolver.resolve('vault.repair-text', payload)


@pytest.mark.skipif(os.name != 'posix', reason='POSIX parent swap uses anchored directory handles')
def test_parent_swap_reports_committed_original_as_partial(command_vault_clone, tmp_path, monkeypatch):
    import os
    from _lifecycle import text_repair_io
    root = command_vault_clone.vault_root
    original = root / 'Projects'
    detached = root / 'Projects-before-swap'
    path = original / 'Encoding.md'
    data = codecs.BOM_UTF8 + b'old'
    path.write_bytes(data)
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'Encoding.md').write_bytes(data)
    real = text_repair_io.os.replace
    def swap(source, destination, *args, **kwargs):
        if destination == 'Encoding.md':
            original.rename(detached)
            original.symlink_to(outside, target_is_directory=True)
        return real(source, destination, *args, **kwargs)
    monkeypatch.setattr(text_repair_io.os, 'replace', swap)
    result = application_for(root).invoke(RepairTextRequest(('Projects/Encoding.md',)))
    assert result.status == 'partial', result
    assert [effect.subject for effect in result.committed_effects] == ['Projects/Encoding.md']
    assert 'path changed' in result.error.message
    assert (outside / 'Encoding.md').read_bytes() == data
    assert (detached / 'Encoding.md').read_bytes() == b'old'


def test_lock_release_failure_preserves_committed_effects(command_vault_clone, monkeypatch):
    from contextlib import contextmanager
    root = command_vault_clone.vault_root
    path = root / 'Projects/Encoding.md'
    path.write_bytes(codecs.BOM_UTF8 + b'body')
    import _common
    real = _common.vault_mutation_lock

    @contextmanager
    def fail_on_release(*args, **kwargs):
        with real(*args, **kwargs):
            yield
        raise OSError('lock release failed')

    monkeypatch.setattr(_common, 'vault_mutation_lock', fail_on_release)
    result = application_for(root).invoke(RepairTextRequest(('Projects/Encoding.md',)))
    assert result.status == 'partial', result
    assert [effect.subject for effect in result.committed_effects] == ['Projects/Encoding.md']
    assert path.read_bytes() == b'body'
    assert result.error.next_action.command_id == 'vault.check'


def test_lock_release_keeps_prior_reconciliation_failure(command_vault_clone, monkeypatch):
    from contextlib import contextmanager
    from _application import _transition_indexes
    from _application.results import CommandError, CommandNextAction
    root = command_vault_clone.vault_root
    path = root / 'Projects/Encoding.md'
    path.write_bytes(codecs.BOM_UTF8 + b'body')
    import _common
    real = _common.vault_mutation_lock

    @contextmanager
    def fail_on_release(*args, **kwargs):
        with real(*args, **kwargs):
            yield
        raise OSError('lock release failed')

    def fail_reconciliation(*args, **kwargs):
        raise _transition_indexes.TransitionIndexesIncomplete(CommandError(
            ErrorCode.CONFLICT, 'router rebuild failed', next_action=CommandNextAction('runtime.refresh-router')))

    monkeypatch.setattr(_common, 'vault_mutation_lock', fail_on_release)
    monkeypatch.setattr(_transition_indexes, 'reconcile_transition_indexes', fail_reconciliation)
    result = application_for(root).invoke(RepairTextRequest(('Projects/Encoding.md',)))
    assert result.status == 'partial', result
    assert result.error.next_action.command_id == 'runtime.refresh-router'
    assert 'router rebuild failed' in result.error.message and 'lock release failed' in result.error.message
    assert [effect.subject for effect in result.committed_effects] == ['Projects/Encoding.md']
    assert path.read_bytes() == b'body'
