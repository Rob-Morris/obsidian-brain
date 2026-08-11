"""Checked installation of the versioned machine-global CLI distribution."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import uuid


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


def install_from_source(source_root: Path, cli_binary: Path) -> InstalledDistribution:
    source = source_root.resolve()
    bootloader = _source_bootloader(source, cli_binary).read_text(encoding="utf-8")
    match = _declared_version(bootloader, "BRAIN_CLI_VERSION")
    if match is None:
        raise DistributionInstallError(
            "CLI source does not declare one canonical version",
            rollback_verified=True,
        )
    brain_core_version = (source / "src" / "brain-core" / "VERSION").read_text(
        encoding="utf-8"
    ).strip()
    return install_distribution(
        source,
        cli_binary,
        cli_version=match,
        expected_brain_core_version=brain_core_version,
    )


def install_distribution(
    source_root: Path,
    cli_binary: Path,
    *,
    cli_version: str,
    expected_brain_core_version: str,
    failpoint=None,
) -> InstalledDistribution:
    """Stage, validate and atomically replace one CLI binary/distribution pair."""

    source = source_root.resolve()
    binary = Path(os.path.abspath(cli_binary.expanduser()))
    _validate_source(source, cli_version, expected_brain_core_version)
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
    replaced_distribution = False
    replaced_binary = False
    try:
        _copy_distribution(source, stage)
        manifest = _write_manifest(
            stage,
            cli_version=cli_version,
            brain_core_version=expected_brain_core_version,
        )
        _fire(failpoint, "after_stage")
        _validate_staged(stage, manifest)
        source_binary = _source_bootloader(source, binary)
        shutil.copyfile(source_binary, binary_stage)
        os.chmod(binary_stage, 0o755)
        _fire(failpoint, "after_cli_stage")
        if distribution.exists():
            os.replace(distribution, backup)
        os.replace(stage, distribution)
        replaced_distribution = True
        _fire(failpoint, "after_distribution_replace")
        if binary.exists():
            os.replace(binary, binary_backup)
        os.replace(binary_stage, binary)
        replaced_binary = True
        _fire(failpoint, "after_cli_replace")
        _verify_pair(
            distribution,
            binary,
            manifest,
            source_binary,
        )
        _fire(failpoint, "after_verify")
    except Exception as exc:
        rollback_verified, recovery_paths = _rollback(
            distribution=distribution,
            distribution_backup=backup,
            replaced_distribution=replaced_distribution,
            expected_distribution_fingerprint=old_distribution_fingerprint,
            binary=binary,
            binary_backup=binary_backup,
            replaced_binary=replaced_binary,
            expected_binary_fingerprint=old_binary,
            failpoint=failpoint,
        )
        if rollback_verified:
            _remove_tree(stage)
            _remove_file(binary_stage)
        else:
            recovery_paths = tuple(
                path
                for path in (*recovery_paths, stage, binary_stage)
                if path.exists() or path.is_symlink()
            )
        raise DistributionInstallError(
            f"CLI distribution install failed: {exc}",
            rollback_verified=rollback_verified,
            recovery_paths=recovery_paths,
        ) from exc
    _remove_tree(backup)
    _remove_file(binary_backup)
    return InstalledDistribution(
        binary,
        distribution,
        cli_version,
        expected_brain_core_version,
        manifest["fingerprint"],
    )


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
    bootloaders = (source / "cli" / "brain", source / "cli" / "brain.cmd")
    if any(not path.is_file() for path in bootloaders):
        raise DistributionInstallError(
            "CLI distribution is missing a platform bootloader",
            rollback_verified=True,
        )
    declarations = [path.read_text(encoding="utf-8") for path in bootloaders]
    if any(_declared_version(text, "BRAIN_CLI_VERSION") != cli_version for text in declarations):
        raise DistributionInstallError(
            "CLI bootloader version does not match the requested distribution",
            rollback_verified=True,
        )
    if any(_declared_version(text, "BRAIN_INSTALL_REF", prefix="v") != brain_core_version for text in declarations):
        raise DistributionInstallError(
            "CLI install ref does not match the Brain Core distribution",
            rollback_verified=True,
        )
    actual_core = (source / "src" / "brain-core" / "VERSION").read_text(
        encoding="utf-8"
    ).strip()
    if actual_core != brain_core_version:
        raise DistributionInstallError(
            "Brain Core source version does not match the requested distribution",
            rollback_verified=True,
        )


def _source_bootloader(source: Path, binary: Path) -> Path:
    return source / "cli" / ("brain.cmd" if binary.suffix.casefold() == ".cmd" else "brain")


def _declared_version(text: str, name: str, *, prefix: str = "") -> str | None:
    match = re.search(
        rf'^(?:set ")?{name}="?{re.escape(prefix)}([0-9]+\.[0-9]+\.[0-9]+)"?$',
        text,
        re.MULTILINE | re.IGNORECASE,
    )
    return match.group(1) if match is not None else None


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
    replaced_distribution: bool,
    expected_distribution_fingerprint: str | None,
    binary: Path,
    binary_backup: Path,
    replaced_binary: bool,
    expected_binary_fingerprint: str | None,
    failpoint=None,
) -> tuple[bool, tuple[Path, ...]]:
    errors = []

    def attempt(name, action) -> None:
        try:
            _fire(failpoint, name)
            action()
        except Exception as exc:
            errors.append(f"{name}: {exc}")

    if replaced_binary:
        attempt("rollback_remove_new_cli", lambda: _remove_file(binary))
    if binary_backup.exists():
        attempt(
            "rollback_restore_old_cli",
            lambda: os.replace(binary_backup, binary),
        )
    if replaced_distribution:
        attempt(
            "rollback_remove_new_distribution",
            lambda: _remove_tree(distribution),
        )
    if distribution_backup.exists():
        attempt(
            "rollback_restore_old_distribution",
            lambda: os.replace(distribution_backup, distribution),
        )
    verified = not errors and (
        _installed_fingerprint(distribution) == expected_distribution_fingerprint
        and _file_fingerprint(binary) == expected_binary_fingerprint
    )
    recovery_paths = tuple(
        path
        for path in (distribution_backup, binary_backup)
        if path.exists() or path.is_symlink()
    )
    return verified, recovery_paths


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
    parser = argparse.ArgumentParser()
    parser.add_argument("source_root", type=Path)
    parser.add_argument("cli_binary", type=Path)
    args = parser.parse_args(argv)
    try:
        result = install_from_source(args.source_root, args.cli_binary)
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
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
