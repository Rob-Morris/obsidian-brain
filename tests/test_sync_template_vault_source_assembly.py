"""Repository sync keeps the devlink stable and publishes only definition outputs."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from brain_test_support import write_executable
import template_source_assembly as source_owner


@pytest.fixture
def source_assembly(tmp_path):
    repo = tmp_path / 'repo'
    scripts = repo / 'src/scripts'
    scripts.mkdir(parents=True)
    originals = Path(__file__).resolve().parents[1] / 'src/scripts'
    for name in ('sync-template-vault.sh', 'template_source_assembly.py'):
        shutil.copy2(originals / name, scripts / name)
    core = repo / 'src/brain-core'
    (core / 'scripts/_bootstrap').mkdir(parents=True)
    real_core = originals.parent / 'brain-core'
    shutil.copy2(real_core / 'scripts/_bootstrap/owned_paths.py', core / 'scripts/_bootstrap/owned_paths.py')
    (core / 'VERSION').write_text('1.0.0\n')
    vault = repo / 'template-vault'
    (vault / '.brain/local').mkdir(parents=True)
    (vault / '.brain/local/compiled-router.json').write_text('actual-root-cache\n')
    (vault / 'Notes/local').mkdir(parents=True)
    (vault / 'Notes/local/Keep.md').write_text('Keep exact local-named content\n')
    (vault / '.brain-core').symlink_to('../src/brain-core', target_is_directory=True)
    check = '''from pathlib import Path
import json, os, sys
actual = Path(os.environ['ACTUAL_TEMPLATE'])
assert (actual / '.brain-core').is_symlink(), 'shared devlink changed during callback'
root = Path(sys.argv[sys.argv.index('--vault') + 1]) if '--vault' in sys.argv else None
if root:
    assert root != actual
    assert not (root / '.brain-core').is_symlink()
    assert (root / 'Notes/local/Keep.md').read_text() == 'Keep exact local-named content\\n'
    assert not (root / '.brain-core/.venv').exists()
    assert not (root / '.brain/local/compiled-router.json').exists()
'''
    (core / 'scripts/sync_definitions.py').write_text(check + '''
from dataclasses import dataclass
@dataclass
class Plan:
    result: dict
    copies: tuple
    folders: tuple
@dataclass
class Copy:
    target: str
    content: bytes

def plan_sync_definitions(root, force=False):
    assert Path(root) != actual
    target = os.environ.get('SYNC_TARGET', '_Config/Updated.md')
    return Plan({'errors': []}, (Copy(target, b'Updated\\n'),), ('NewStarterFolder',))

def apply_definition_sync(root, plan):
    assert (actual / '.brain-core').is_symlink()
    root = Path(root)
    for item in plan.copies:
        target = root / item.target
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(item.content)
    for folder in plan.folders:
        (root / folder).mkdir(exist_ok=True)
    (root / '.brain').mkdir(exist_ok=True)
    (root / '.brain/tracking.json').write_text('{}\\n')

if __name__ == '__main__':
    if os.environ.get('FAIL_SYNC'):
        raise SystemExit(7)
    print(json.dumps({'types': {'in_sync': []}, 'not_installable': []}))
''')
    (core / 'scripts/compile_router.py').write_text(check + '''
def compile(root):
    root = Path(root)
    assert root != actual
    assert (actual / '.brain-core').is_symlink()
    if os.environ.get('FAIL_COMPILE'):
        raise ValueError('synthetic compilation failure')
    (root / '.brain/local').mkdir(parents=True, exist_ok=True)
    (root / '.brain/local/compiled-router.json').write_text(str(root))
    return {}
''')
    # Explicit authored inventory excludes ignored local runtime/model state.
    inventory = [str(p.relative_to(repo)) for base in (core, vault)
                 for p in base.rglob('*') if p.is_file() and not p.is_symlink()]
    (core / '.venv').mkdir()
    (core / '.venv/ignored-model').write_text('Must never be copied\n')
    tools = tmp_path / 'tools'
    tools.mkdir()
    write_executable(tools / 'git', '#!' + sys.executable + '\nimport sys\n'
                     + 'if "ls-files" in sys.argv: sys.stdout.write(' + repr('\0'.join(inventory) + '\0') + ')\n')
    env = os.environ.copy()
    scratch = tmp_path / 'assembly-scratch'
    scratch.mkdir()
    env.update(PYTHON_BIN=sys.executable, PYTHONDONTWRITEBYTECODE='1',
               ACTUAL_TEMPLATE=str(vault), TMPDIR=str(scratch))
    env['PATH'] = str(tools) + os.pathsep + env.get('PATH', '')
    return scripts / 'sync-template-vault.sh', core, vault, env


def run_sync(fixture, mode):
    script, _core, _vault, env = fixture
    return subprocess.run(['bash', str(script), mode], env=env, text=True, capture_output=True)


@pytest.mark.parametrize('mode', ['--check', '--apply'])
def test_dev_sync_keeps_shared_link_stable_and_never_publishes_runtime_outputs(source_assembly, mode):
    _script, core, vault, _env = source_assembly
    before = {p.relative_to(core): p.read_bytes() for p in core.rglob('*') if p.is_file()}
    result = run_sync(source_assembly, mode)
    assert result.returncode == 0, result.stderr
    assert list(Path(source_assembly[3]['TMPDIR']).iterdir()) == []
    assert os.readlink(vault / '.brain-core') == '../src/brain-core'
    assert {p.relative_to(core): p.read_bytes() for p in core.rglob('*') if p.is_file()} == before
    assert (vault / '.brain/local/compiled-router.json').read_text() == 'actual-root-cache\n'
    assert (vault / 'Notes/local/Keep.md').read_text() == 'Keep exact local-named content\n'
    assert not (vault / 'NewStarterFolder').exists()
    if mode == '--check':
        assert not (vault / '_Config').exists()
        assert not (vault / '.brain/tracking.json').exists()
        assert 'in sync' in result.stdout
    else:
        assert (vault / '_Config/Updated.md').read_text() == 'Updated\n'
        assert json.loads((vault / '.brain/tracking.json').read_text()) == {}


@pytest.mark.parametrize('failure, mode', [('FAIL_SYNC', '--check'), ('FAIL_COMPILE', '--apply')])
def test_downstream_failure_keeps_shared_template_unchanged(source_assembly, failure, mode):
    _script, _core, vault, env = source_assembly
    env[failure] = '1'
    result = run_sync(source_assembly, mode)
    assert result.returncode != 0
    assert list(Path(env['TMPDIR']).iterdir()) == []
    assert os.readlink(vault / '.brain-core') == '../src/brain-core'
    assert not (vault / '_Config').exists()
    assert not (vault / '.brain/tracking.json').exists()
    assert (vault / '.brain/local/compiled-router.json').read_text() == 'actual-root-cache\n'


def test_apply_refuses_non_definition_output_before_publication(source_assembly):
    _script, _core, vault, env = source_assembly
    env['SYNC_TARGET'] = 'Notes/Unrelated.md'
    result = run_sync(source_assembly, '--apply')
    assert result.returncode != 0
    assert not (vault / 'Notes/Unrelated.md').exists()
    assert not (vault / '.brain/tracking.json').exists()


def test_dev_sync_rejects_a_different_core_link_before_assembly(source_assembly, tmp_path):
    _script, _core, vault, _env = source_assembly
    other = tmp_path / 'other-core'
    other.mkdir()
    (other / 'VERSION').write_text('Other source\n')
    (vault / '.brain-core').unlink()
    (vault / '.brain-core').symlink_to(other, target_is_directory=True)
    result = run_sync(source_assembly, '--check')
    assert result.returncode != 0
    assert 'not the repository Core source' in result.stderr
    assert (vault / '.brain-core').resolve() == other


def test_apply_refuses_redirected_managed_destination(source_assembly, tmp_path):
    _script, _core, vault, _env = source_assembly
    outside = tmp_path / 'outside'
    outside.mkdir()
    sentinel = outside / 'Updated.md'
    sentinel.write_text('Unrelated destination\n')
    (vault / '_Config').symlink_to(outside, target_is_directory=True)
    result = run_sync(source_assembly, '--apply')
    assert result.returncode != 0
    assert sentinel.read_text() == 'Unrelated destination\n'
    assert not (vault / '.brain/tracking.json').exists()


def test_later_unsafe_tracking_endpoint_creates_no_earlier_definition_directory(source_assembly, tmp_path):
    _script, _core, vault, _env = source_assembly
    sentinel = tmp_path / 'unrelated-tracking.json'
    sentinel.write_text('Unrelated tracking bytes\n')
    mode = sentinel.stat().st_mode
    (vault / '.brain/tracking.json').symlink_to(sentinel)
    assert not (vault / '_Config').exists()

    result = run_sync(source_assembly, '--apply')

    assert result.returncode != 0
    assert not (vault / '_Config').exists()
    assert (vault / '.brain/tracking.json').is_symlink()
    assert sentinel.read_text() == 'Unrelated tracking bytes\n'
    assert sentinel.stat().st_mode == mode
    assert os.readlink(vault / '.brain-core') == '../src/brain-core'


@pytest.mark.parametrize('target_kind', ['authored', 'unselected', 'outside'])
def test_source_alias_materialisation_requires_an_authored_regular_target(tmp_path, monkeypatch, target_kind):
    repo = tmp_path / 'repo'
    template = repo / 'template-vault'
    template.mkdir(parents=True)
    authored = template / 'AGENTS.md'
    authored.write_text('Authored bootstrap\n')
    target = authored
    if target_kind != 'authored':
        target = (repo if target_kind == 'unselected' else tmp_path) / 'Unrelated.md'
        target.write_text('Unrelated bytes\n')
    alias = template / 'CLAUDE.md'
    alias.symlink_to(target)
    inventory = b'template-vault/AGENTS.md\0template-vault/CLAUDE.md\0'
    monkeypatch.setattr(source_owner, 'subprocess', SimpleNamespace(check_output=lambda *_a: inventory))
    original_copy = shutil.copy2
    copied = []

    def admitted_copy(source, destination):
        assert Path(source) == authored, 'Unselected source reached content copying'
        copied.append(Path(source))
        return original_copy(source, destination)

    monkeypatch.setattr(source_owner, 'shutil', SimpleNamespace(copy2=admitted_copy))
    destination = tmp_path / 'assembled'
    if target_kind == 'authored':
        source_owner.assemble(repo, destination)
        materialised = destination / 'CLAUDE.md'
        assert not materialised.is_symlink()
        assert materialised.read_text() == 'Authored bootstrap\n'
        assert copied == [authored, authored]
    else:
        with pytest.raises(ValueError, match='selected repository inventory') as caught:
            source_owner.assemble(repo, destination)
        assert str(target) not in str(caught.value)
        assert not (destination / 'CLAUDE.md').exists()
    assert alias.is_symlink() and alias.resolve() == target
