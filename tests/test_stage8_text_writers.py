"""Persisted text writers preserve healthy bytes and refuse markers before effects."""

import pytest

from _bootstrap.file_transaction import FilePlan, apply_file_changes
from _common import NonStandardVaultTextError, safe_write


@pytest.mark.parametrize('writer', ('atomic', 'transaction'))
def test_leading_marker_is_refused_before_any_effect(tmp_path, writer):
    path = tmp_path / 'note.md'
    path.write_bytes(b'original\r\n')
    before = set(tmp_path.iterdir())
    with pytest.raises(ValueError, match='byte-order mark'):
        if writer == 'atomic':
            safe_write(path, '\ufeffreplacement', bounds=tmp_path)
        else:
            FilePlan().write_text(path, '\ufeffreplacement')
    assert path.read_bytes() == b'original\r\n'
    assert set(tmp_path.iterdir()) == before


def test_transaction_keeps_line_endings_and_interior_unicode_marker(tmp_path):
    path = tmp_path / 'client.toml'
    raw = 'value = "a\ufeffb"\r\n'.encode('utf-8')
    path.write_bytes(raw)
    plan = FilePlan()
    assert plan.read_text(path) == raw.decode('utf-8')
    plan.write_text(path, plan.read_text(path) + '# edited\r\n')
    apply_file_changes(plan.changes())
    assert path.read_bytes() == raw + b'# edited\r\n'


@pytest.mark.parametrize('raw,code', [('body'.encode('utf-16'), 'utf16_bom'),
                                    ('café'.encode() + b'\xe2\x82', 'truncated_utf8')])
def test_transaction_names_encoded_source_and_retains_bytes(tmp_path, raw, code):
    path = tmp_path / 'CLAUDE.md'
    path.write_bytes(raw)
    with pytest.raises(NonStandardVaultTextError) as caught:
        FilePlan().read_text(path)
    assert str(path) in str(caught.value) and code in str(caught.value)
    assert path.read_bytes() == raw


def test_direct_edit_script_opens_and_writes_healthy_artefact(command_vault_clone):
    import subprocess
    import sys
    from pathlib import Path
    import edit
    root = command_vault_clone.vault_root
    relative = 'Projects/Command Fixture.md'
    result = subprocess.run([sys.executable, str(Path(edit.__file__)), 'append',
        '--vault', str(root), '--path', relative, '--body', 'Direct script sentinel.',
        '--target', ':body', '--scope', 'section'],
        text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert 'Direct script sentinel.' in (root / relative).read_text()


def test_shaping_reports_converted_collateral_backlink(command_vault_clone):
    from _application.registry import current_request_resolver
    from _application.results import WarningCode
    from command_application import application_for
    import compile_router
    root = command_vault_clone.vault_root
    app = application_for(root, profile='operator')
    resolve = current_request_resolver().resolve
    terminal = app.invoke(resolve('artefact.set-status', {
        'path': 'Designs/project~command-fixture/Command Fixture Design.md', 'status': 'implemented'}))
    assert terminal.status == 'ok', terminal
    old = terminal.result.path
    backlink = root / 'Wiki/Shaping Backlink.md'
    backlink.parent.mkdir(exist_ok=True)
    raw = ('---\ntype: living/wiki\nkey: shaping-backlink\ntags: []\n---\n'
           + '[[' + old.removesuffix('.md') + ']]\n').encode('utf-16')
    backlink.write_bytes(raw)
    compile_router.persist_compiled_router(str(root), compile_router.compile(str(root)))
    result = app.invoke(resolve('shaping.start', {'target': 'design/command-fixture-design', 'mode': 'refine'}))
    assert result.status == 'ok', result
    text = backlink.read_bytes().decode('utf-8')
    assert result.result.target_path.removesuffix('.md') in text
    assert any(w.code is WarningCode.FOLLOW_UP_REQUIRED and 'Wiki/Shaping Backlink.md' in w.message
               and 'utf16_bom' in w.message for w in result.warnings)


def test_recursive_unarchive_names_every_unreadable_candidate_before_effects(command_vault_clone):
    from _application.registry import current_request_resolver
    from _application.results import ErrorCode
    from command_application import application_for
    root = command_vault_clone.vault_root
    app = application_for(root, profile='operator')
    resolve = current_request_resolver().resolve
    archived = app.invoke(resolve('artefact.archive', {'path': 'Ideas/Command Fixture Candidate.md'}))
    assert archived.status == 'ok', archived
    source = archived.result.new_path
    original = (root / source).read_bytes()
    bad = ['_Archive/Wiki/Bad A.md', '_Archive/Wiki/Bad B.md']
    for relative in bad:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'legacy\xff')
    result = app.invoke(resolve('artefact.unarchive', {'path': source, 'recursive': True}))
    assert result.status == 'error' and result.error.code is ErrorCode.CONFLICT, result
    assert result.effects == 'none'
    assert all(path in result.error.message for path in bad)
    assert 'not_utf8' in result.error.message
    assert result.error.next_action.command_id == 'vault.check'
    assert (root / source).read_bytes() == original
    assert all((root / path).read_bytes() == b'legacy\xff' for path in bad)


def test_healthy_bootstrap_read_keeps_backend_out_of_launcher_imports(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    path = tmp_path / 'client.toml'
    path.write_bytes(b'value = "healthy"\r\n')
    scripts = Path(__file__).resolve().parents[1] / 'src/brain-core/scripts'
    probe = """
import sys
from pathlib import Path
from _bootstrap.file_transaction import FilePlan
assert FilePlan().read_text(Path(sys.argv[1])) == 'value = "healthy"\\r\\n'
assert not any(name == '_common' or name.startswith('_common.') for name in sys.modules)
"""
    env = dict(os.environ, PYTHONPATH=str(scripts))
    result = subprocess.run([sys.executable, '-S', '-c', probe, str(path)],
                            env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
