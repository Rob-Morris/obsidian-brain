"""Filesystem text findings survive router failure and reuse source bytes."""

import builtins
import io
from collections import Counter
import os
from pathlib import Path

import pytest

import check
from _repair_common import Identity, JUDGEMENT_FINDINGS


def source(root, relative, data):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@pytest.mark.parametrize('data,code', [(b'first\r\nnext\xff', 'not_utf8'), (b'a\x00b\xff', 'not_text')])
@pytest.mark.parametrize('router', [None, {'error': 'invalid router'}, {'artefacts': []}])
def test_ambiguous_files_report_even_without_router(tmp_path, data, code, router):
    source(tmp_path, '_Config/router.md', data)
    result = check.run_checks(str(tmp_path), router)
    finding = next(f for f in result['findings'] if f['check'] == 'unreadable_file')
    assert finding['file'] == '_Config/router.md'
    assert finding['code'] == code and finding['severity'] == 'error'
    assert 'repair' not in finding
    assert JUDGEMENT_FINDINGS[('unreadable_file', code)] is Identity.SUBJECT
    if code == 'not_utf8':
        assert (finding['line'], finding['column']) == (2, 5)


@pytest.mark.skipif(os.name == 'nt' or (hasattr(os, 'geteuid') and os.geteuid() == 0), reason='mode bits require non-root POSIX')
def test_permission_failure_has_errno_evidence(tmp_path):
    path = source(tmp_path, 'Wiki/Unreadable.md', b'text')
    path.chmod(0)
    try:
        finding, = check.check_unreadable_files(tmp_path)
        assert finding['code'] == 'os_error'
        assert finding['evidence'] == {'errno': 'EACCES'}
        assert 'cloud' in finding['fix']
    finally:
        path.chmod(0o600)


def test_vanished_file_is_not_a_finding(tmp_path, monkeypatch):
    source(tmp_path, 'Wiki/Gone.md', b'text')
    real = Path.read_bytes
    def removed(path):
        path.unlink()
        return real(path)
    monkeypatch.setattr(Path, 'read_bytes', removed)
    assert check.check_unreadable_files(tmp_path) == []


@pytest.mark.parametrize('data', [b'bad\xff', b'nul\x00'])
def test_failed_read_is_cached_across_consumers(tmp_path, monkeypatch, data):
    path = source(tmp_path, 'Wiki/Bad.md', data)
    count = 0
    real = Path.read_bytes
    def counted(path):
        nonlocal count
        count += 1
        return real(path)
    monkeypatch.setattr(Path, 'read_bytes', counted)
    ctx = check.CheckContext(tmp_path, {})
    assert check.check_unreadable_files(tmp_path, ctx=ctx)
    for reader in (ctx.read_frontmatter, ctx.read_text, ctx.has_authoring_hint):
        with pytest.raises(OSError):
            reader(path)
    assert count == 1


def test_clean_read_reuses_delimiters_and_normalises_crlf(tmp_path):
    path = source(tmp_path, 'Wiki/Good.md', b'  ---\r\ntype: living/wiki\r\n  ---\r\nbody\r\n')
    ctx = check.CheckContext(tmp_path, {})
    assert ctx.read_frontmatter(path) == {'type': 'living/wiki'}
    assert ctx.read_text(path).endswith('body\n')


@pytest.mark.parametrize('content', [b'{broken', b'bad\xff', b'{}'])
def test_tracking_failure_keeps_unrelated_findings(tmp_path, content):
    source(tmp_path, '.brain/skill-sources.json', content)
    source(tmp_path, 'Wiki/Bad.md', b'bad\xff')
    source(tmp_path, '_Config/Skills/Unknown/SKILL.md', b'bad\xff')
    result = check.run_checks(tmp_path)
    assert any(f['file'] == 'Wiki/Bad.md' and f['code'] == 'not_utf8' for f in result['findings'])
    assert any(f['check'] == 'text_scan' and f['file'] == '.brain/skill-sources.json' for f in result['findings'])
    assert not any(f['file'] == '_Config/Skills/Unknown/SKILL.md' for f in result['findings'])


@pytest.mark.skipif(os.name == 'nt' or (hasattr(os, 'geteuid') and os.geteuid() == 0), reason='mode bits require non-root POSIX')
def test_inaccessible_directory_reports_incomplete_scan(tmp_path):
    path = source(tmp_path, 'Blocked/note.md', b'text').parent
    source(tmp_path, 'Wiki/Bad.md', b'bad\xff')
    path.chmod(0)
    try:
        result = check.run_checks(tmp_path)
        hit = next(f for f in result['findings'] if f['file'] == 'Blocked')
        assert hit['code'] == 'os_error' and hit['evidence'] == {'errno': 'EACCES'}
        assert 'incomplete' in hit['fix']
        assert any(f['file'] == 'Wiki/Bad.md' for f in result['findings'])
    finally:
        path.chmod(0o700)


def test_normal_router_and_bootstrap_use_one_open_per_scanned_file(command_vault_clone, fake_home, monkeypatch):
    from _lifecycle.text_files import iter_vault_text_files
    root = command_vault_clone.vault_root
    source(root, '.mcp.json', b'{}')
    scanned = {root / p for p in iter_vault_text_files(root)}
    counts = Counter()
    def wrap(real):
        def counted(file, mode='r', *args, **kwargs):
            if not isinstance(file, int) and 'r' in mode:
                path = Path(file)
                if path in scanned:
                    counts[path] += 1
            return real(file, mode, *args, **kwargs)
        return counted
    monkeypatch.setattr(builtins, 'open', wrap(builtins.open))
    monkeypatch.setattr(io, 'open', wrap(io.open))
    result = check.run_checks(root)
    assert result['findings'] is not None
    assert set(counts) == scanned
    assert all(count == 1 for count in counts.values()), counts


def test_ownership_symlink_refusal_does_not_assume_perfile_finding(tmp_path):
    path = source(tmp_path, '_Assets/Bad.md', b'bad\xff')
    (tmp_path / 'Wiki').mkdir()
    (tmp_path / 'Wiki/Linked.md').symlink_to(path)
    result = check.run_checks(tmp_path, {'artefacts': []})
    finding = next(f for f in result['findings'] if f['code'] == 'workspace_scan_unreadable')
    assert 'Wiki/Linked.md' in finding['message']
    assert 'any matching' in finding['fix']
    assert not any(f['check'] == 'unreadable_file' for f in result['findings'])


@pytest.mark.parametrize('body', [b'body\x00', b'a' * 10000 + b'\xff'])
def test_router_firstblock_tolerance_matches_original_reader(tmp_path, body):
    import compile_router
    path = source(tmp_path, 'Wiki/Good.md', b'---\ntype: living/wiki\nkey: good\n---\n' + body)
    artefacts = [{'classification': 'living', 'path': 'Wiki'}]
    ctx = check.CheckContext(tmp_path, {})
    original = compile_router.living_artefact_source_state(str(tmp_path), artefacts)
    cached = compile_router.living_artefact_source_state(str(tmp_path), artefacts, read_fm=ctx.read_source_frontmatter)
    assert original == cached
    assert original[0] == 1
