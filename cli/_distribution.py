"""Checked installation of the versioned machine-global CLI distribution."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import uuid

from _version_contract import SourceVersions, parse_source_versions


MANIFEST_NAME = ".brain-cli-distribution.json"
SOURCE_ENTRIES = (
    "cli",
    "src/brain-core",
    "template-vault",
    "install.sh",
    "install.ps1",
)


class DistributionInstallError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        rollback_verified: bool,
        recovery_paths: tuple[Path, ...] = (),
    ) -> None:
        super().__init__(message)
        self.rollback_verified = rollback_verified
        self.recovery_paths = recovery_paths


@dataclass(frozen=True, slots=True)
class InstalledDistribution:
    cli_binary: Path
    distribution_root: Path
    cli_version: str
    brain_core_version: str
    manifest_fingerprint: str
    cleanup_recovery_paths: tuple[Path, ...] = ()


def distribution_cutover_commit(installed: InstalledDistribution) -> dict[str, object]:
    """Project one installed distribution into the shared cutover contract."""

    return {
        "status": "committed",
        "cli_binary": str(installed.cli_binary),
        "distribution_root": str(installed.distribution_root),
        "manifest_fingerprint": installed.manifest_fingerprint,
        "cleanup_recovery_paths": [
            str(path) for path in installed.cleanup_recovery_paths
        ],
    }


def source_versions(source_root: Path) -> SourceVersions:
    """Read the canonical CLI/Core pair from one complete source tree."""

    source = source_root.resolve()
    bootloaders = (source / "cli" / "brain", source / "cli" / "brain.cmd")
    try:
        declarations = [path.read_text(encoding="utf-8") for path in bootloaders]
        brain_core_version = (source / "src" / "brain-core" / "VERSION").read_text(
            encoding="utf-8"
        )
    except OSError as exc:
        raise ValueError(f"CLI source version declarations are unreadable: {exc}") from exc
    return parse_source_versions(
        core=brain_core_version,
        unix_cli=declarations[0],
        windows_cli=declarations[1],
    )


def install_from_source(source_root: Path, cli_binary: Path, *, bootstrap_python: Path | None = None) -> InstalledDistribution:
    source = source_root.resolve()
    try:
        versions = source_versions(source)
    except ValueError as exc:
        raise DistributionInstallError(str(exc), rollback_verified=True) from exc
    return install_distribution(
        source,
        cli_binary,
        cli_version=versions.cli_version,
        expected_brain_core_version=versions.brain_core_version,
        bootstrap_python=bootstrap_python,
    )


def install_distribution(
    source_root: Path,
    cli_binary: Path,
    *,
    cli_version: str,
    expected_brain_core_version: str,
    bootstrap_python: Path | None = None,
    failpoint=None,
) -> InstalledDistribution:
    """Serialize CLI capability replacement with native registration mutations."""
    from _bootstrap.mcp_registration import registration_lock
    from _launcher.approval_lifecycle import distribution_cutover

    with registration_lock(Path.home()):
        return distribution_cutover(source_root, cli_binary, lambda: _install_distribution(
            source_root, cli_binary, cli_version=cli_version,
            expected_brain_core_version=expected_brain_core_version,
            bootstrap_python=bootstrap_python, failpoint=failpoint,
        ))


def _install_distribution(
    source_root: Path,
    cli_binary: Path,
    *,
    cli_version: str,
    expected_brain_core_version: str,
    bootstrap_python: Path | None,
    failpoint,
) -> InstalledDistribution:
    """Stage, validate and atomically replace one CLI binary/distribution pair."""

    source = source_root.resolve()
    binary = Path(os.path.abspath(cli_binary.expanduser()))
    _validate_source(source, cli_version, expected_brain_core_version)
    from _bootstrap.mcp_registration import require_launcher_capability

    require_launcher_capability(binary, supports_stdio=(source / "cli/_mcp_stdio.py").is_file())
    base_python = validate_bootstrap_python(bootstrap_python)
    if binary.is_symlink():
        raise DistributionInstallError(
            "CLI binary cannot be a symlink", rollback_verified=True
        )
    distribution = _distribution_path(binary, cli_version)
    parent = distribution.parent
    parent.mkdir(parents=True, exist_ok=True)
    binary.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    stage = parent / f".{cli_version}.{token}.stage"
    backup = parent / f".{cli_version}.{token}.backup"
    binary_stage = binary.parent / f".{binary.name}.{token}.stage"
    binary_backup = binary.parent / f".{binary.name}.{token}.backup"
    old_distribution_fingerprint = _installed_fingerprint(distribution)
    old_binary = _file_fingerprint(binary)
    distribution_cutover_started = False
    binary_cutover_started = False
    new_distribution_fingerprint = None
    new_binary_fingerprint = None
    try:
        _copy_distribution(source, stage)
        (stage / ".bootstrap-python").write_text(str(base_python) + "\n", encoding="utf-8")
        manifest = _write_manifest(
            stage,
            cli_version=cli_version,
            brain_core_version=expected_brain_core_version,
        )
        _fire(failpoint, "after_stage")
        _validate_staged(stage, manifest)
        new_distribution_fingerprint = str(manifest["fingerprint"])
        source_binary = _source_bootloader(source, binary)
        shutil.copyfile(source_binary, binary_stage)
        os.chmod(binary_stage, 0o755)
        new_binary_fingerprint = _file_fingerprint(binary_stage)
        _fire(failpoint, "after_cli_stage")
        distribution_cutover_started = True
        if distribution.exists():
            os.replace(distribution, backup)
        os.replace(stage, distribution)
        _fire(failpoint, "after_distribution_replace")
        binary_cutover_started = True
        if binary.exists():
            os.replace(binary, binary_backup)
        os.replace(binary_stage, binary)
        _fire(failpoint, "after_cli_replace")
        _verify_pair(
            distribution,
            binary,
            manifest,
            source_binary,
        )
        _fire(failpoint, "after_verify")
    except BaseException as exc:
        rollback_verified, recovery_paths = _rollback(
            distribution=distribution,
            distribution_backup=backup,
            distribution_cutover_started=distribution_cutover_started,
            expected_distribution_fingerprint=old_distribution_fingerprint,
            replacement_distribution_fingerprint=new_distribution_fingerprint,
            binary=binary,
            binary_backup=binary_backup,
            binary_cutover_started=binary_cutover_started,
            expected_binary_fingerprint=old_binary,
            replacement_binary_fingerprint=new_binary_fingerprint,
            failpoint=failpoint,
        )
        cleanup_failures = []
        if rollback_verified:
            for name, path, cleanup in (
                ("cleanup_staged_distribution", stage, _remove_tree),
                ("cleanup_staged_cli", binary_stage, _remove_file),
            ):
                try:
                    cleanup(path)
                except BaseException as cleanup_exc:
                    cleanup_failures.append(f"{name}: {cleanup_exc}")
                    if path.exists() or path.is_symlink():
                        recovery_paths = (*recovery_paths, path)
        else:
            recovery_paths = tuple(
                path
                for path in (*recovery_paths, stage, binary_stage)
                if path.exists() or path.is_symlink()
            )
        recovery_paths = tuple(dict.fromkeys(recovery_paths))
        error = DistributionInstallError(
            f"CLI distribution install failed: {exc}",
            rollback_verified=rollback_verified,
            recovery_paths=recovery_paths,
        )
        for failure in cleanup_failures:
            error.add_note(failure)
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            exc.rollback_verified = rollback_verified
            exc.recovery_paths = recovery_paths
            exc.add_note(str(error))
            for failure in cleanup_failures:
                exc.add_note(failure)
            raise
        raise error from exc
    cleanup_recovery_paths = []
    for name, path, cleanup in (
        ("cleanup_old_distribution", backup, _remove_tree),
        ("cleanup_old_cli", binary_backup, _remove_file),
    ):
        try:
            _fire(failpoint, name)
            cleanup(path)
        except BaseException:
            if path.exists() or path.is_symlink():
                cleanup_recovery_paths.append(path)
    return InstalledDistribution(
        binary,
        distribution,
        cli_version,
        expected_brain_core_version,
        manifest["fingerprint"],
        tuple(cleanup_recovery_paths),
    )


def validate_bootstrap_python(python: Path | None = None) -> Path:
    """Admit an absolute base interpreter independently of rotating Brain environments."""
    candidate = python or Path(getattr(sys, "_base_executable", sys.executable))
    if not candidate.is_absolute():
        raise ValueError("Bootstrap Python must be an absolute path")
    candidate = candidate.resolve(strict=True)
    result = subprocess.run(
        [str(candidate), "-I", "-c", "import sys; print(sys.prefix == sys.base_prefix); raise SystemExit(sys.version_info < (3, 12))"],
        capture_output=True, text=True, timeout=10, check=False,
    )
    if result.returncode or result.stdout.strip() != "True" or "venvs" in candidate.parts and ".brain" in candidate.parts:
        raise ValueError("Bootstrap Python must be Python 3.12+ outside managed dependency environments")
    return candidate


def verify_distribution(root: Path) -> dict[str, object]:
    manifest_path = root / MANIFEST_NAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"CLI distribution manifest is unreadable: {exc}") from exc
    _validate_staged(root, manifest)
    return manifest


def _distribution_path(binary: Path, cli_version: str) -> Path:
    if binary.parent.name != "bin":
        raise DistributionInstallError(
            "CLI binary must be installed below a bin directory",
            rollback_verified=True,
        )
    return binary.parent.parent / "lib" / "brain-cli" / cli_version


def _validate_source(source: Path, cli_version: str, brain_core_version: str) -> None:
    for entry in SOURCE_ENTRIES:
        if not (source / entry).exists():
            raise DistributionInstallError(
                f"CLI distribution source is missing {entry}",
                rollback_verified=True,
            )
    try:
        versions = source_versions(source)
    except ValueError as exc:
        raise DistributionInstallError(str(exc), rollback_verified=True) from exc
    if versions.cli_version != cli_version:
        raise DistributionInstallError(
            "CLI bootloader version does not match the requested distribution",
            rollback_verified=True,
        )
    if versions.brain_core_version != brain_core_version:
        raise DistributionInstallError(
            "Brain Core source version does not match the requested distribution",
            rollback_verified=True,
        )


def _source_bootloader(source: Path, binary: Path) -> Path:
    return source / "cli" / ("brain.cmd" if binary.suffix.casefold() == ".cmd" else "brain")


def _copy_distribution(source: Path, stage: Path) -> None:
    stage.mkdir(mode=0o755)
    for entry in SOURCE_ENTRIES:
        origin = source / entry
        destination = stage / entry
        destination.parent.mkdir(parents=True, exist_ok=True)
        if origin.is_dir() and not origin.is_symlink():
            shutil.copytree(
                origin,
                destination,
                symlinks=True,
                ignore=_ignored_distribution_entries,
            )
        elif origin.is_symlink():
            destination.symlink_to(os.readlink(origin))
        else:
            shutil.copy2(origin, destination)


def _write_manifest(
    root: Path,
    *,
    cli_version: str,
    brain_core_version: str,
) -> dict[str, object]:
    entries = _tree_entries(root)
    fingerprint = _manifest_fingerprint(entries)
    manifest = {
        "schema": "brain.cli-distribution/1",
        "cli_version": cli_version,
        "brain_core_version": brain_core_version,
        "entries": entries,
        "fingerprint": fingerprint,
    }
    (root / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return manifest


def _ignored_distribution_entries(_directory: str, names: list[str]) -> set[str]:
    return {
        name
        for name in names
        if name == "__pycache__" or name.endswith((".pyc", ".pyo", ".DS_Store"))
    }


def _validate_staged(root: Path, manifest: dict[str, object]) -> None:
    if manifest.get("schema") != "brain.cli-distribution/1":
        raise ValueError("CLI distribution manifest schema is unsupported")
    entries = manifest.get("entries")
    if not isinstance(entries, dict):
        raise ValueError("CLI distribution manifest entries are invalid")
    actual = _tree_entries(root)
    if actual != entries:
        raise ValueError("CLI distribution files do not match their manifest")
    if manifest.get("fingerprint") != _manifest_fingerprint(actual):
        raise ValueError("CLI distribution manifest fingerprint is invalid")


def _verify_pair(
    distribution: Path,
    binary: Path,
    manifest: dict[str, object],
    source_binary: Path,
) -> None:
    _validate_staged(distribution, manifest)
    if _file_fingerprint(binary) != _file_fingerprint(source_binary):
        raise ValueError("installed CLI binary does not match the distribution")
    if sys.platform == "win32":
        if binary.suffix.casefold() != ".cmd":
            raise ValueError("installed Windows CLI binary must use the .cmd bootloader")
    elif stat.S_IMODE(binary.stat().st_mode) != 0o755:
        raise ValueError("installed CLI binary is not executable")


def _tree_entries(root: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if relative == MANIFEST_NAME:
            continue
        if path.is_symlink():
            entries[relative] = "symlink:" + os.readlink(path)
        elif path.is_file():
            entries[relative] = "sha256:" + _sha256(path)
        elif path.is_dir():
            entries[relative + "/"] = "directory"
        else:
            raise ValueError(f"unsupported CLI distribution entry: {relative}")
    return entries


def _manifest_fingerprint(entries: dict[str, str]) -> str:
    encoded = json.dumps(entries, separators=(",", ":"), sort_keys=True).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _installed_fingerprint(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        return str(verify_distribution(path)["fingerprint"])
    except ValueError:
        return "raw-sha256:" + _raw_tree_fingerprint(path)


def _raw_tree_fingerprint(root: Path) -> str:
    entries = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            entries[relative] = "symlink:" + os.readlink(path)
        elif path.is_file():
            entries[relative] = "sha256:" + _sha256(path)
        elif path.is_dir():
            entries[relative + "/"] = "directory"
    return hashlib.sha256(
        json.dumps(entries, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


def _file_fingerprint(path: Path) -> str | None:
    return "sha256:" + _sha256(path) if path.is_file() else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rollback(
    *,
    distribution: Path,
    distribution_backup: Path,
    distribution_cutover_started: bool,
    expected_distribution_fingerprint: str | None,
    replacement_distribution_fingerprint: str | None,
    binary: Path,
    binary_backup: Path,
    binary_cutover_started: bool,
    expected_binary_fingerprint: str | None,
    replacement_binary_fingerprint: str | None,
    failpoint=None,
) -> tuple[bool, tuple[Path, ...]]:
    errors = []
    recovery_paths: list[Path] = []

    def restore_target(
        *,
        name: str,
        target: Path,
        backup_path: Path,
        started: bool,
        expected_fingerprint: str | None,
        replacement_fingerprint: str | None,
        fingerprint,
        remove,
        remove_name: str,
        restore_name: str,
    ) -> None:
        if not started and not backup_path.exists():
            return
        try:
            current = fingerprint(target)
            if current == expected_fingerprint:
                if backup_path.exists():
                    _fire(failpoint, remove_name)
                    remove(backup_path)
                return
            if backup_path.exists():
                if fingerprint(backup_path) != expected_fingerprint:
                    raise OSError("backup does not match the recorded original state")
                _fire(failpoint, remove_name)
                remove(target)
                _fire(failpoint, restore_name)
                os.replace(backup_path, target)
                return
            if expected_fingerprint is None and current == replacement_fingerprint:
                _fire(failpoint, remove_name)
                remove(target)
                return
            raise OSError(
                "target differs from the recorded original and no verified backup can restore it"
            )
        except BaseException as exc:
            try:
                restored = fingerprint(target) == expected_fingerprint
                backup_survives = backup_path.exists() or backup_path.is_symlink()
            except BaseException:
                restored = False
                backup_survives = True
            if restored and not backup_survives:
                return
            errors.append(f"{name}: {exc}")
            for path in (target, backup_path):
                if path.exists() or path.is_symlink():
                    recovery_paths.append(path)

    restore_target(
        name="rollback_cli",
        target=binary,
        backup_path=binary_backup,
        started=binary_cutover_started,
        expected_fingerprint=expected_binary_fingerprint,
        replacement_fingerprint=replacement_binary_fingerprint,
        fingerprint=_file_fingerprint,
        remove=_remove_file,
        remove_name="rollback_remove_new_cli",
        restore_name="rollback_restore_old_cli",
    )
    restore_target(
        name="rollback_distribution",
        target=distribution,
        backup_path=distribution_backup,
        started=distribution_cutover_started,
        expected_fingerprint=expected_distribution_fingerprint,
        replacement_fingerprint=replacement_distribution_fingerprint,
        fingerprint=_installed_fingerprint,
        remove=_remove_tree,
        remove_name="rollback_remove_new_distribution",
        restore_name="rollback_restore_old_distribution",
    )
    def verify_target(name: str, path: Path, fingerprint, expected) -> bool:
        try:
            return fingerprint(path) == expected
        except BaseException as exc:
            errors.append(f"{name}: {exc}")
            if path.exists() or path.is_symlink():
                recovery_paths.append(path)
            return False

    distribution_verified = verify_target(
        "verify_restored_distribution",
        distribution,
        _installed_fingerprint,
        expected_distribution_fingerprint,
    )
    binary_verified = verify_target(
        "verify_restored_cli",
        binary,
        _file_fingerprint,
        expected_binary_fingerprint,
    )
    verified = not errors and distribution_verified and binary_verified
    recovery_paths.extend(
        path
        for path in (distribution_backup, binary_backup)
        if path.exists() or path.is_symlink()
    )
    return verified, tuple(dict.fromkeys(recovery_paths))


def _remove_tree(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or not path.is_dir():
        raise OSError(f"refusing to recursively remove unsafe path: {path}")
    shutil.rmtree(path)


def _remove_file(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _fire(failpoint, name: str) -> None:
    if failpoint is not None:
        failpoint(name)


def main(argv: list[str] | None = None) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src/brain-core/scripts"))
    parser = argparse.ArgumentParser()
    parser.add_argument("source_root", type=Path)
    parser.add_argument("cli_binary", type=Path)
    parser.add_argument("--bootstrap-python", type=Path, help="Absolute Python 3.12+ base interpreter for non-interactive MCP startup")
    args = parser.parse_args(argv)
    try:
        result = install_from_source(args.source_root, args.cli_binary, bootstrap_python=args.bootstrap_python)
    except (DistributionInstallError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "cli_binary": str(result.cli_binary),
                "distribution_root": str(result.distribution_root),
                "cli_version": result.cli_version,
                "brain_core_version": result.brain_core_version,
                "manifest_fingerprint": result.manifest_fingerprint,
                "cleanup_recovery_paths": [
                    str(path) for path in result.cleanup_recovery_paths
                ],
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
