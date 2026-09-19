"""Checked versioned CLI distribution installation and rollback."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest


CLI_ROOT = Path(__file__).resolve().parents[2] / "cli"
if str(CLI_ROOT) not in sys.path:
    sys.path.insert(0, str(CLI_ROOT))

import _distribution  # noqa: E402
from _distribution import (  # noqa: E402
    DistributionInstallError,
    InstalledDistribution,
    distribution_cutover_commit,
    install_distribution,
    verify_distribution,
)
from _local_cli.runtime import CLI_VERSION  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_VERSION = (REPO_ROOT / "src" / "brain-core" / "VERSION").read_text().strip()


def _install(tmp_path, **kwargs):
    return install_distribution(
        REPO_ROOT,
        tmp_path / "prefix" / "bin" / "brain",
        cli_version=CLI_VERSION,
        expected_brain_core_version=CORE_VERSION,
        **kwargs,
    )


def test_installed_pair_is_versioned_manifested_and_runnable(tmp_path):
    installed = _install(tmp_path)

    manifest = verify_distribution(installed.distribution_root)
    assert installed.cli_binary.stat().st_mode & 0o111
    assert installed.distribution_root == tmp_path / "prefix" / "lib" / "brain-cli" / CLI_VERSION
    assert manifest["cli_version"] == CLI_VERSION
    assert manifest["brain_core_version"] == CORE_VERSION
    assert manifest["fingerprint"] == installed.manifest_fingerprint
    assert not any(installed.distribution_root.rglob("__pycache__"))


def test_cli_capability_check_and_cutover_hold_registration_lock(tmp_path, monkeypatch):
    from _bootstrap import mcp_registration
    from _bootstrap.file_lock import exclusive_file_lock, MutationLockError

    checked = []
    def assert_locked(stage):
        path = mcp_registration.user_ledger_path(Path.home()).with_suffix(".lock")
        with pytest.raises(MutationLockError):
            with exclusive_file_lock(path, timeout=0, follow_symlinks=False):
                pytest.fail("registration lock was released during CLI replacement")
        checked.append(stage)

    monkeypatch.setattr(mcp_registration, "require_launcher_capability", lambda *args, **kwargs: assert_locked("capability"))
    _install(tmp_path, failpoint=lambda stage: assert_locked(stage) if stage == "after_cli_replace" else None)
    assert checked == ["capability", "after_cli_replace"]


def test_installed_cli_discovers_real_selected_brain_catalogue(
    tmp_path,
    command_vault_baseline,
):
    installed = _install(tmp_path)

    completed = subprocess.run(
        [
            str(installed.cli_binary),
            "--vault",
            str(command_vault_baseline.vault_root),
            "--json",
            "command",
            "list",
            "--owner",
            "application",
        ],
        cwd=command_vault_baseline.vault_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["schema"] == "brain.local-command-list/2"
    assert len(payload["entries"]) == 25
    deletion = next(entry for entry in payload["entries"] if entry["command_id"] == "artefact.delete")
    assert deletion["access"] == "denied"
    assert deletion["authority"] == "administrator"
    assert payload["catalogues"]["application"]["schema"] == "brain.command-catalogue/1"


def test_windows_target_selects_the_cmd_bootloader(tmp_path):
    installed = install_distribution(
        REPO_ROOT,
        tmp_path / "prefix" / "bin" / "brain.cmd",
        cli_version=CLI_VERSION,
        expected_brain_core_version=CORE_VERSION,
    )

    assert installed.cli_binary.read_bytes() == (REPO_ROOT / "cli" / "brain.cmd").read_bytes()
    assert verify_distribution(installed.distribution_root)["cli_version"] == CLI_VERSION


def test_windows_cmd_bootloader_does_not_require_posix_execute_bits(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    monkeypatch.setattr(_distribution, "sys", SimpleNamespace(**{**vars(sys), "platform": "win32"}))
    monkeypatch.setattr(
        _distribution.os,
        "chmod",
        lambda *_args, **_kwargs: None,
    )

    installed = install_distribution(
        REPO_ROOT,
        tmp_path / "prefix" / "bin" / "brain.cmd",
        cli_version=CLI_VERSION,
        expected_brain_core_version=CORE_VERSION,
    )

    assert installed.cli_binary.stat().st_mode & 0o111 == 0
    assert installed.cli_binary.read_bytes() == (REPO_ROOT / "cli" / "brain.cmd").read_bytes()


def test_source_core_version_must_match_requested_distribution(tmp_path):
    source = tmp_path / "source"
    (source / "cli").mkdir(parents=True)
    (source / "src" / "brain-core").mkdir(parents=True)
    (source / "template-vault").mkdir()
    declaration = (
        'BRAIN_CLI_VERSION="9.9.9"\n'
        'BRAIN_INSTALL_REF="v7.7.7"\n'
    )
    (source / "cli" / "brain").write_text(declaration, encoding="utf-8")
    (source / "cli" / "brain.cmd").write_text(
        'set "BRAIN_CLI_VERSION=9.9.9"\n'
        'set "BRAIN_INSTALL_REF=v7.7.7"\n',
        encoding="utf-8",
    )
    (source / "src" / "brain-core" / "VERSION").write_text(
        "7.7.7\n", encoding="utf-8"
    )
    (source / "install.sh").write_text("install\n", encoding="utf-8")
    (source / "install.ps1").write_text("install\n", encoding="utf-8")

    with pytest.raises(
        DistributionInstallError,
        match="Brain Core source version does not match",
    ):
        install_distribution(
            source,
            tmp_path / "prefix" / "bin" / "brain",
            cli_version="9.9.9",
            expected_brain_core_version="8.8.8",
        )


@pytest.mark.parametrize(
    "failure",
    (
        "after_stage",
        "after_cli_stage",
        "after_distribution_replace",
        "after_cli_replace",
        "after_verify",
    ),
)
def test_every_install_failpoint_restores_the_old_verified_pair(tmp_path, failure):
    installed = _install(tmp_path)
    old_manifest = verify_distribution(installed.distribution_root)["fingerprint"]
    old_binary = installed.cli_binary.read_bytes()

    def failpoint(name):
        if name == failure:
            raise OSError(f"injected {name}")

    with pytest.raises(DistributionInstallError) as caught:
        _install(tmp_path, failpoint=failpoint)

    assert caught.value.rollback_verified is True
    assert verify_distribution(installed.distribution_root)["fingerprint"] == old_manifest
    assert installed.cli_binary.read_bytes() == old_binary
    assert not tuple((tmp_path / "prefix").rglob("*.stage"))
    assert not tuple((tmp_path / "prefix").rglob("*.backup"))


def test_keyboard_interrupt_restores_the_old_verified_pair(tmp_path):
    installed = _install(tmp_path)
    old_manifest = verify_distribution(installed.distribution_root)["fingerprint"]
    old_binary = installed.cli_binary.read_bytes()

    def failpoint(name):
        if name == "after_distribution_replace":
            raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        _install(tmp_path, failpoint=failpoint)

    assert verify_distribution(installed.distribution_root)["fingerprint"] == old_manifest
    assert installed.cli_binary.read_bytes() == old_binary


@pytest.mark.parametrize("interruption", (KeyboardInterrupt, SystemExit))
@pytest.mark.parametrize("target_kind", ("distribution", "binary"))
def test_after_effect_interrupt_restores_the_old_verified_pair(
    tmp_path, monkeypatch, target_kind, interruption
):
    installed = _install(tmp_path)
    old_manifest = verify_distribution(installed.distribution_root)["fingerprint"]
    old_binary = installed.cli_binary.read_bytes()
    real_replace = _distribution.os.replace

    def replace_then_interrupt(source, destination):
        real_replace(source, destination)
        source = Path(source)
        destination = Path(destination)
        if target_kind == "distribution" and source.name.endswith(".stage") and (
            destination == installed.distribution_root
        ):
            raise interruption()
        if target_kind == "binary" and source.name.endswith(".stage") and (
            destination == installed.cli_binary
        ):
            raise interruption()

    monkeypatch.setattr(_distribution.os, "replace", replace_then_interrupt)

    with pytest.raises(interruption):
        _install(tmp_path)

    assert verify_distribution(installed.distribution_root)["fingerprint"] == old_manifest
    assert installed.cli_binary.read_bytes() == old_binary


@pytest.mark.parametrize("target_kind", ("distribution", "binary"))
def test_after_effect_interrupt_removes_a_new_install_pair(
    tmp_path,
    monkeypatch,
    target_kind,
):
    binary = tmp_path / "prefix" / "bin" / "brain"
    distribution = tmp_path / "prefix" / "lib" / "brain-cli" / CLI_VERSION
    real_replace = _distribution.os.replace

    def replace_then_interrupt(source, destination):
        real_replace(source, destination)
        source = Path(source)
        destination = Path(destination)
        if target_kind == "distribution" and source.name.endswith(".stage") and (
            destination == distribution
        ):
            raise KeyboardInterrupt()
        if target_kind == "binary" and source.name.endswith(".stage") and (
            destination == binary
        ):
            raise KeyboardInterrupt()

    monkeypatch.setattr(_distribution.os, "replace", replace_then_interrupt)

    with pytest.raises(KeyboardInterrupt):
        _install(tmp_path)

    assert not binary.exists()
    assert not distribution.exists()
    assert not tuple((tmp_path / "prefix").rglob("*.stage"))
    assert not tuple((tmp_path / "prefix").rglob("*.backup"))


def test_stage_cleanup_failure_preserves_the_initiating_install_error(
    tmp_path,
    monkeypatch,
):
    real_remove_tree = _distribution._remove_tree
    removed_files = []

    def fail_staged_tree(path):
        if path.name.endswith(".stage"):
            raise OSError("cleanup blocked")
        return real_remove_tree(path)

    def record_remove_file(path):
        removed_files.append(path)
        return path.unlink(missing_ok=True)

    monkeypatch.setattr(_distribution, "_remove_tree", fail_staged_tree)
    monkeypatch.setattr(_distribution, "_remove_file", record_remove_file)

    def failpoint(name):
        if name == "after_stage":
            raise OSError("initiating install failure")

    with pytest.raises(
        DistributionInstallError,
        match="initiating install failure",
    ) as caught:
        _install(tmp_path, failpoint=failpoint)

    assert caught.value.rollback_verified is True
    assert any(path.name.endswith(".stage") for path in caught.value.recovery_paths)
    assert removed_files


def test_keyboard_interrupt_during_backup_cleanup_is_committed(tmp_path):
    _install(tmp_path)

    def failpoint(name):
        if name == "cleanup_old_distribution":
            raise KeyboardInterrupt()

    installed = _install(tmp_path, failpoint=failpoint)

    assert verify_distribution(installed.distribution_root)["fingerprint"] == (
        installed.manifest_fingerprint
    )
    assert len(installed.cleanup_recovery_paths) == 1
    assert installed.cleanup_recovery_paths[0].name.endswith(".backup")


def test_interrupt_after_rollback_restore_effect_is_reconciled(
    tmp_path, monkeypatch
):
    installed = _install(tmp_path)
    installed.cli_binary.write_text("distinct old CLI\n", encoding="utf-8")
    installed.cli_binary.chmod(0o755)
    old_binary = installed.cli_binary.read_bytes()
    real_replace = _distribution.os.replace

    def replace_then_interrupt(source, destination):
        real_replace(source, destination)
        if (
            Path(source).name.endswith(".backup")
            and Path(destination) == installed.cli_binary
        ):
            raise KeyboardInterrupt()

    monkeypatch.setattr(_distribution.os, "replace", replace_then_interrupt)

    def failpoint(name):
        if name == "after_cli_replace":
            raise OSError("injected post-install failure")

    with pytest.raises(DistributionInstallError) as caught:
        _install(tmp_path, failpoint=failpoint)

    assert caught.value.rollback_verified is True
    assert caught.value.recovery_paths == ()
    assert installed.cli_binary.read_bytes() == old_binary


def test_final_rollback_verification_failure_is_explicitly_unverified(
    tmp_path, monkeypatch
):
    installed = _install(tmp_path)
    installed.cli_binary.write_text("distinct old CLI\n", encoding="utf-8")
    installed.cli_binary.chmod(0o755)
    real_fingerprint = _distribution._file_fingerprint
    target_reads = 0

    def fail_final_target_verification(path):
        nonlocal target_reads
        if Path(path) == installed.cli_binary:
            target_reads += 1
            if target_reads == 3:
                raise OSError("cannot verify restored CLI")
        return real_fingerprint(path)

    monkeypatch.setattr(
        _distribution,
        "_file_fingerprint",
        fail_final_target_verification,
    )

    def failpoint(name):
        if name == "after_cli_replace":
            raise OSError("injected post-install failure")

    with pytest.raises(DistributionInstallError) as caught:
        _install(tmp_path, failpoint=failpoint)

    assert caught.value.rollback_verified is False
    assert installed.cli_binary in caught.value.recovery_paths


def test_backup_cleanup_failure_is_committed_with_recovery_path(tmp_path):
    _install(tmp_path)

    def failpoint(name):
        if name == "cleanup_old_distribution":
            raise OSError("injected cleanup failure")

    installed = _install(tmp_path, failpoint=failpoint)

    assert verify_distribution(installed.distribution_root)["fingerprint"] == (
        installed.manifest_fingerprint
    )
    assert len(installed.cleanup_recovery_paths) == 1
    assert installed.cleanup_recovery_paths[0].name.endswith(".backup")
    assert installed.cleanup_recovery_paths[0].is_dir()


def test_standalone_projection_includes_cleanup_recovery_paths(
    tmp_path, monkeypatch, capsys
):
    recovery = (tmp_path / "old.backup").resolve()
    installed = InstalledDistribution(
        (tmp_path / "bin" / "brain").resolve(),
        (tmp_path / "lib" / "brain-cli" / CLI_VERSION).resolve(),
        CLI_VERSION,
        CORE_VERSION,
        "sha256:abc",
        (recovery,),
    )
    monkeypatch.setattr(_distribution, "install_from_source", lambda *_args, **_kwargs: installed)

    assert _distribution.main([str(REPO_ROOT), str(installed.cli_binary)]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["cleanup_recovery_paths"] == [str(recovery)]


def test_cutover_projection_has_one_canonical_shape(tmp_path):
    recovery = (tmp_path / "old.backup").resolve()
    installed = InstalledDistribution(
        (tmp_path / "bin" / "brain").resolve(),
        (tmp_path / "lib" / "brain-cli" / CLI_VERSION).resolve(),
        CLI_VERSION,
        CORE_VERSION,
        "sha256:abc",
        (recovery,),
    )

    assert distribution_cutover_commit(installed) == {
        "status": "committed",
        "cli_binary": str(installed.cli_binary),
        "distribution_root": str(installed.distribution_root),
        "manifest_fingerprint": "sha256:abc",
        "cleanup_recovery_paths": [str(recovery)],
    }


def test_unverified_old_distribution_is_reported_honestly_on_failure(tmp_path):
    binary = tmp_path / "prefix" / "bin" / "brain"
    distribution = tmp_path / "prefix" / "lib" / "brain-cli" / CLI_VERSION
    binary.parent.mkdir(parents=True)
    distribution.mkdir(parents=True)
    binary.write_text("old\n")
    (distribution / "corrupt").write_text("old\n")

    def failpoint(name):
        if name == "after_distribution_replace":
            raise OSError("injected failure")

    with pytest.raises(DistributionInstallError) as caught:
        _install(tmp_path, failpoint=failpoint)

    assert caught.value.rollback_verified is True
    assert binary.read_text() == "old\n"
    assert (distribution / "corrupt").read_text() == "old\n"


def test_rollback_failure_is_unverified_and_retains_recovery_material(tmp_path):
    installed = _install(tmp_path)
    installed.cli_binary.write_text("distinct old CLI\n", encoding="utf-8")
    installed.cli_binary.chmod(0o755)

    def failpoint(name):
        if name in {"after_cli_replace", "rollback_restore_old_cli"}:
            raise OSError(f"injected {name}")

    with pytest.raises(DistributionInstallError) as caught:
        _install(tmp_path, failpoint=failpoint)

    assert caught.value.rollback_verified is False
    assert caught.value.recovery_paths
    assert any(path.name.endswith(".backup") for path in caught.value.recovery_paths)
    assert all(path.exists() for path in caught.value.recovery_paths)
    assert installed.distribution_root.exists()


def test_cli_install_refuses_a_symlink_target(tmp_path):
    real = tmp_path / "real" / "brain"
    real.parent.mkdir(parents=True)
    real.write_text("old\n")
    link = tmp_path / "prefix" / "bin" / "brain"
    link.parent.mkdir(parents=True)
    link.symlink_to(real)

    with pytest.raises(DistributionInstallError, match="symlink") as caught:
        install_distribution(
            REPO_ROOT,
            link,
            cli_version=CLI_VERSION,
            expected_brain_core_version=CORE_VERSION,
        )

    assert caught.value.rollback_verified is True
    assert real.read_text() == "old\n"


def test_installed_cli_packages_and_uses_private_owner_transport(tmp_path, command_vault_baseline, monkeypatch):
    import os
    from _bootstrap.consent_owner import ConsentOwner
    from _bootstrap.owner_attachment import OwnerAttachment

    if os.name != 'posix':
        pytest.skip('private CLI job inheritance requires POSIX')
    installed = _install(tmp_path)
    packaged_scripts = installed.distribution_root / 'src/brain-core/scripts'
    for name in ('owner_attachment.py', 'consent_owner.py', 'consent_state.py', 'file_lock.py', 'paths.py'):
        assert (packaged_scripts / '_bootstrap' / name).is_file()
    isolated = subprocess.run(
        [sys.executable, '-I', '-c',
         f'import sys; sys.path.insert(0,{str(packaged_scripts)!r}); import _bootstrap.owner_attachment as owner; '
         f'assert owner.__file__.startswith({str(packaged_scripts)!r})'],
        cwd=tmp_path, capture_output=True, text=True, timeout=5,
    )
    assert isolated.returncode == 0, isolated.stderr
    owner = ConsentOwner(command_vault_baseline.vault_root)
    from types import SimpleNamespace
    from _local_cli.main import _initialise_selected_job
    from _local_cli.runtime import SelectedBrain
    _initialise_selected_job(SelectedBrain(command_vault_baseline.vault_root, None, "test"), owner, SimpleNamespace(operator_key=None))
    attachment = OwnerAttachment.for_job(owner)
    connections = []
    serve = owner.serve_connection
    def record(channel):
        connections.append(True)
        return serve(channel)
    monkeypatch.setattr(owner, 'serve_connection', record)
    try:
        env = dict(os.environ, PYTHONPATH='')
        completed = subprocess.run(
            [str(installed.cli_binary), '--vault', str(command_vault_baseline.vault_root),
             '--json', 'command', 'list', '--owner', 'application'],
            cwd=tmp_path, **attachment.forwarded_process(env),
            capture_output=True, text=True, timeout=20,
        )
        assert completed.returncode == 0, completed.stderr
        assert json.loads(completed.stdout)['schema'] == 'brain.local-command-list/2'
        assert connections, 'installed launcher did not attach its discovery child to the job owner'
    finally:
        attachment.close()
        owner.close()


def test_installed_public_job_delegates_authenticated_principal_without_credentials(tmp_path, command_vault_clone):
    import os
    from _common._yaml import dump_mapping_text
    from _common import hash_key

    if os.name != 'posix':
        pytest.skip('private CLI job inheritance requires POSIX')
    root = command_vault_clone.vault_root
    (root / '.brain/config.yaml').write_text(dump_mapping_text({
        'vault': {'operators': [
            {'id': 'job-user', 'profile': 'operator', 'auth': {'type': 'key', 'hash': hash_key('job-secret')}},
            {'id': 'other-user', 'profile': 'operator', 'auth': {'type': 'key', 'hash': hash_key('other-secret')}},
        ]}}))
    installed = _install(tmp_path)
    program = tmp_path / 'job.py'
    program.write_text("""import json,os,subprocess,sys
assert 'BRAIN_OPERATOR_KEY' not in os.environ
brain=sys.argv[1]
def call(key=None):
    argv=[brain,'access','status','--json']
    if key is not None: argv += ['--operator-key',key]
    result=subprocess.run(argv,capture_output=True,text=True,pass_fds=(int(os.environ['BRAIN_OWNER_CHANNEL'].rsplit(':',1)[1]),))
    return result.returncode,json.loads(result.stdout) if result.stdout else result.stderr
first=call()
other=call('other-secret')
last=call()
print(json.dumps({'first':first,'other':other,'last':last,'args':sys.argv[2:]}))
""")
    completed = subprocess.run([str(installed.cli_binary), '--vault', str(root), '--operator-key', 'job-secret',
                                'session', 'run', '--', sys.executable, str(program), str(installed.cli_binary),
                                '--json', '--operation', 'literal-program-argument'],
                               env=dict(os.environ, BRAIN_OPERATOR_KEY='ambient-secret'),
                               capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload['args'] == ['--json', '--operation', 'literal-program-argument']
    assert payload['first'][0] == payload['last'][0] == 0, payload
    assert payload['first'][1]['result']['identity']['principal'] == 'operator:job-user'
    assert payload['last'][1]['result']['identity']['principal'] == 'operator:job-user'
    assert payload['other'][0] != 0
