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
    assert payload["schema"] == "brain.local-command-list/1"
    assert len(payload["entries"]) == 78
    assert "artefact.delete" not in {
        entry["command_id"] for entry in payload["entries"]
    }
    assert payload["entries"][0]["catalogue_schema"] == "brain.command-catalogue/1"


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
    monkeypatch.setattr(_distribution.sys, "platform", "win32")
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


@pytest.mark.parametrize("target_kind", ("distribution", "binary"))
def test_after_effect_interrupt_restores_the_old_verified_pair(
    tmp_path, monkeypatch, target_kind
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
            raise KeyboardInterrupt()
        if target_kind == "binary" and source.name.endswith(".stage") and (
            destination == installed.cli_binary
        ):
            raise KeyboardInterrupt()

    monkeypatch.setattr(_distribution.os, "replace", replace_then_interrupt)

    with pytest.raises(KeyboardInterrupt):
        _install(tmp_path)

    assert verify_distribution(installed.distribution_root)["fingerprint"] == old_manifest
    assert installed.cli_binary.read_bytes() == old_binary


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
    monkeypatch.setattr(_distribution, "install_from_source", lambda *_args: installed)

    assert _distribution.main([str(REPO_ROOT), str(installed.cli_binary)]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["cleanup_recovery_paths"] == [str(recovery)]


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
