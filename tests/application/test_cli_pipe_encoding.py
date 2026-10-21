"""Real pre-paging command output through a legacy-codepage pipe."""
import json
import os
from pathlib import Path
import subprocess
import sys

from _common import document_revision_at, parse_frontmatter


def test_cp1252_unicode_mutation_receipt_preserves_effects(command_vault_clone, tmp_path):
    root = command_vault_clone.vault_root
    original = root / 'Designs/project~command-fixture/Command Fixture Design.md'
    path = original.with_name('CLI Unicode 雪 😀.md')
    original.rename(path)
    import compile_router
    compile_router.persist_compiled_router(str(root), compile_router.compile(str(root)))
    reference = path.relative_to(root).as_posix()
    env = {**os.environ, **command_vault_clone.environment,
           'PYTHONUTF8': '0', 'PYTHONIOENCODING': 'cp1252'}
    for name in ('BRAIN_OWNER_CHANNEL', 'BRAIN_PROCESS_CONTEXT', 'BRAIN_OWNER_UNAVAILABLE'):
        env.pop(name, None)
    script = root / '.brain-core/scripts/command.py'

    def call(noun, verb, payload):
        completed = subprocess.run(
            [sys.executable, str(script), noun, verb, '--vault', str(root),
             '--json', '--request-json', '-'], input=json.dumps(payload).encode('ascii'),
            cwd=tmp_path, env=env, capture_output=True, timeout=30)
        assert completed.returncode == 0, completed.stderr.decode('cp1252') or completed.stdout.decode('cp1252')
        assert completed.stdout.isascii()
        return json.loads(completed.stdout.decode('cp1252'))

    before = path.read_text(encoding='utf-8')
    marker = '\nOnce-only CP1252 output: 雪 😀.\n'
    written = call('document', 'write-body', {
        'document': {'resource': 'artefact', 'reference': reference},
        'expected_revision': document_revision_at(path), 'operation': 'append',
        'content': {'source': 'inline', 'content': marker},
    })
    assert written['status'] == 'ok' and written['committed_effects']
    assert '雪' in json.dumps(written, ensure_ascii=False)
    assert parse_frontmatter(path.read_text(encoding='utf-8'))[1] == parse_frontmatter(before)[1] + marker
    after = path.read_bytes()
    read = call('artefact', 'read', {'reference': reference})
    assert read['status'] == 'ok' and marker in read['result']['content']
    assert path.read_bytes() == after
