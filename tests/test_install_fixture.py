"""Installer scenarios get authored source and independently writable copies."""

from pathlib import Path
import stat

from brain_test_support import copy_install_source


def test_install_source_has_one_engine_and_no_generated_bytecode(tmp_path):
    source = tmp_path / 'checkout'
    for directory in ('cli', 'template-vault/_Config', 'src/brain-core'):
        root = source / directory
        (root / '__pycache__').mkdir(parents=True)
        (root / '__pycache__/module.cpython-312.pyc').write_bytes(b'bytecode')
        (root / 'module.pyc').write_bytes(b'bytecode')
        (root / 'module.py').write_text('authored = True\n')
    (source / 'template-vault/.brain-core').mkdir()
    (source / 'template-vault/.brain-core/VERSION').write_text('duplicate engine\n')
    (source / 'src/brain-core/VERSION').write_text('1.0.0\n')
    (source / 'cli/brain').write_text('launcher\n')
    for name in ('install.sh', 'install.ps1'):
        (source / name).write_text('installer\n')
    (source / 'install.sh').chmod(0o755)
    destination = tmp_path / 'copy'
    destination.mkdir()
    copy_install_source(destination, source_root=source)

    assert (destination / 'src/brain-core/VERSION').is_file()
    assert (destination / 'cli/brain').is_file()
    for directory in ('cli', 'template-vault/_Config', 'src/brain-core'):
        assert (destination / directory / 'module.py').read_text() == 'authored = True\n'
    assert not (destination / 'template-vault/.brain-core').exists()
    assert not list(destination.rglob('__pycache__'))
    assert not list(destination.rglob('*.pyc'))
    assert stat.S_IMODE((destination / 'install.sh').stat().st_mode) == stat.S_IMODE((source / 'install.sh').stat().st_mode)


def test_install_source_mutations_do_not_affect_other_tests_or_checkout(tmp_path):
    first, second = tmp_path / 'first', tmp_path / 'second'
    first.mkdir()
    second.mkdir()
    copy_install_source(first)
    copy_install_source(second)
    source = Path(__file__).resolve().parents[1] / 'src/brain-core/VERSION'
    original = source.read_bytes()

    (first / 'src/brain-core/VERSION').write_text('99.0.0\n')
    (first / 'cli/brain').unlink()

    assert (second / 'src/brain-core/VERSION').read_bytes() == original
    assert (second / 'cli/brain').is_file()
    assert source.read_bytes() == original
