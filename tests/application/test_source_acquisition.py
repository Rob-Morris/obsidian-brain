"""Published source selection through the actual Git boundary."""
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'cli'))
from _acquisition import acquire, verify_acquired


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


@pytest.fixture
def origin(tmp_path):
    root = tmp_path / 'origin'
    root.mkdir()
    git(root, 'init', '-q', '-b', 'main')
    git(root, 'config', 'user.email', 'test@example.invalid')
    git(root, 'config', 'user.name', 'Test')
    (root / 'src/brain-core').mkdir(parents=True)
    (root / 'src/brain-core/VERSION').write_text('0.74.0\n')
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'Release')
    git(root, 'tag', '-a', 'v0.74.0', '-m', 'Release')
    return root


def test_latest_and_exact_select_same_commit_and_persist_identity(origin, tmp_path):
    exact = acquire('0.74.0', tmp_path / 'exact', repository=str(origin))
    latest = acquire('latest', tmp_path / 'latest', repository=str(origin))
    assert exact.receipt == latest.receipt
    assert exact.receipt['commit'] == git(origin, 'rev-parse', 'HEAD')
    assert verify_acquired(exact.source_root) == exact.receipt
    # A moving publication does not change the retained acquisition.
    (origin / 'src/brain-core/VERSION').write_text('0.75.0\n')
    git(origin, 'commit', '-qam', 'Next')
    assert verify_acquired(exact.source_root) == exact.receipt
    with pytest.raises(ValueError, match='acquisition failed'):
        acquire('latest', tmp_path / 'missing-tag', repository=str(origin))


def test_conflicting_version_tag_and_modified_retained_source_refused(origin, tmp_path):
    git(origin, 'tag', 'v0.75.0')
    with pytest.raises(ValueError, match='conflicts'):
        acquire('0.75.0', tmp_path / 'conflict', repository=str(origin))
    selected = acquire('0.74.0', tmp_path / 'selected', repository=str(origin))
    (selected.source_root / 'src/brain-core/VERSION').write_text('0.75.0\n')
    with pytest.raises(ValueError, match='modifications'):
        verify_acquired(selected.source_root)


def test_latest_rejects_main_version_tag_at_another_commit(origin, tmp_path):
    (origin / 'other').write_text('changed')
    git(origin, 'add', '.')
    git(origin, 'commit', '-qm', 'Unpublished changes')
    with pytest.raises(ValueError, match='different commits'):
        acquire('latest', tmp_path / 'unpublished', repository=str(origin))


@pytest.mark.parametrize('version', ['main', '../bad', '-x', 'v0.74.0', '0.74'])
def test_selectors_are_exact_versions_or_latest(version, tmp_path):
    with pytest.raises(ValueError, match='exact'):
        acquire(version, tmp_path / 'unused')
    assert not (tmp_path / 'unused').exists()


@pytest.mark.parametrize('ignored', [False, True])
def test_extra_deployable_files_invalidate_a_published_receipt(origin, tmp_path, ignored):
    if ignored:
        (origin / '.gitignore').write_text('src/brain-core/extra.py\n')
        git(origin, 'add', '.')
        git(origin, 'commit', '-qm', 'Ignore declaration')
        git(origin, 'tag', '-f', 'v0.74.0')
    selected = acquire('0.74.0', tmp_path / 'selected', repository=str(origin))
    (selected.source_root / 'src/brain-core/extra.py').write_text('untracked executable')
    with pytest.raises(ValueError, match='extra deployable'):
        verify_acquired(selected.source_root)


def test_failed_cli_attempt_retains_a_usable_pinned_retry(origin, tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace
    from _local_cli import main
    import json
    import _acquisition
    root = Path(__file__).resolve().parents[2]
    lease = tmp_path / 'retained-source'
    calls = []
    def from_origin(version, destination):
        calls.append(version)
        return acquire(version, destination, repository=str(origin))
    monkeypatch.setattr(_acquisition, 'acquire', from_origin)
    monkeypatch.setattr(main.tempfile, 'mkdtemp', lambda **kw: str(lease))
    monkeypatch.setattr(main.OwnerAttachment, 'capture', lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(main, '_trusted_distribution', lambda: (root / 'cli/brain', root))
    monkeypatch.setattr(main, '_run_selected_lifecycle', lambda *args: 3)
    payload = {'vault_root': str(tmp_path / 'vault'), 'brain_id': 'example', 'mcp_scope': 'skip'}
    assert main.run(['install', '--request-json', json.dumps(payload), '--workspace', str(tmp_path / 'workspace'), '--operator-key', 'test-key', '--dry-run']) == 3
    receipt = verify_acquired(lease)
    retry_text = capsys.readouterr().err
    assert str(lease) in retry_text
    assert '--workspace ' + str(tmp_path / 'workspace') in retry_text
    assert '--operator-key' in retry_text
    assert '<same operator key>' in retry_text
    assert 'test-key' not in retry_text
    assert '--dry-run' in retry_text
    assert 'cd ' in retry_text
    (origin / 'src/brain-core/VERSION').write_text('0.75.0\n')
    git(origin, 'commit', '-qam', 'Publication advanced')
    def retry(*args):
        assert verify_acquired(Path(args[2]['source_root'])) == receipt
        return 0
    monkeypatch.setattr(main, '_run_selected_lifecycle', retry)
    assert main.run(['install', '--request-json', json.dumps({**payload, 'source_root': str(lease)})]) == 0
    assert calls == ['latest']


def test_windows_retry_uses_ascii_stdin_json_and_preserves_all_selectors():
    import json
    from _local_cli.main import _pinned_retry_command
    payload = {"source_root": r"C:\retained source", "version": None, "vault_root": "C:/雪"}
    argv = [r"C:\Program Files\Brain\bin\brain.cmd", "upgrade", "--request-json", json.dumps(payload),
            "--vault", r"C:\vault", "--brain", "selected", "--workspace", r"C:\workspace", "--dry-run"]
    rendered = _pinned_retry_command(argv, "C:/O'Brien & work", {"BRAIN_VAULT_ROOT": "C:/env vault"}, platform="win32")
    assert rendered.startswith("Set-Location -LiteralPath 'C:/O''Brien & work'; ")
    assert "$env:BRAIN_VAULT_ROOT = 'C:/env vault'" in rendered
    assert "$retryExecutable = 'C:" in rendered
    assert " | & $retryExecutable @retryArguments" in rendered
    assert "'--request-json', '-'" in rendered
    assert "'--vault', 'C:" in rendered
    assert "'--brain', 'selected'" in rendered and "'--workspace', 'C:" in rendered
    assert "'--dry-run'" in rendered
    # The escaped request, including its fixed source pin, is entirely ASCII.
    request = rendered.split("; ")[-1].split(" | & ")[0]
    assert json.loads(request[1:-1].replace("''", "'")) == payload
    assert request.isascii()


def test_windows_retry_unicode_paths_survive_a_legacy_diagnostic_pipe():
    import base64
    import json
    import re
    from _local_cli.main import _pinned_retry_command
    directory = "C:/working 雪 😀"
    binary = "C:/ユーザー/Brain/bin/brain.cmd"
    vault = "C:/vault 雪"
    request = {"source_root": "C:/retained 雪", "version": None}
    rendered = _pinned_retry_command(
        [binary, "upgrade", "--request-json", json.dumps(request), "--vault", vault],
        directory, {"BRAIN_VAULT_ROOT": vault}, platform="win32")
    assert rendered.encode("cp1252").decode("cp1252") == rendered
    encoded_values = re.findall(r"FromBase64String\('([^']+)'\)", rendered)
    assert {base64.b64decode(value).decode("utf-8") for value in encoded_values} == {directory, binary, vault}
    assert "Remove-Item Env:BRAIN_WORKSPACE_DIR -ErrorAction SilentlyContinue" in rendered
