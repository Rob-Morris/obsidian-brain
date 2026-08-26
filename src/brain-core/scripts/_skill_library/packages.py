"""Validate, identify, and copy complete skill package trees."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import stat

from _common import parse_frontmatter, validate_portable_relative_path

from .models import PackageFile, PackageSnapshot


MAX_PACKAGE_FILES = 512
MAX_PACKAGE_BYTES = 32 * 1024 * 1024
_SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class PackageValidationError(ValueError):
    """A source or installed package violates the portable package contract."""


def validate_skill_name(name: str) -> str:
    """Validate and return a portable canonical skill name."""
    if not isinstance(name, str) or not _SKILL_NAME.fullmatch(name):
        raise PackageValidationError(
            "skill name must use lowercase letters, digits, and single hyphens"
        )
    return name


def inspect_package(root: str | Path, *, expected_name: str | None = None) -> PackageSnapshot:
    """Validate and hash a complete package tree without following links."""
    package_root = Path(root)
    if package_root.is_symlink() or not package_root.is_dir():
        raise PackageValidationError(f"skill package root is missing or unsafe: {package_root}")
    skill_file = package_root / "SKILL.md"
    if skill_file.is_symlink() or not skill_file.is_file():
        raise PackageValidationError("skill package requires a regular root SKILL.md")
    try:
        fields, _body = parse_frontmatter(skill_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as exc:
        raise PackageValidationError(f"cannot read skill package metadata: {exc}") from exc
    declared = fields.get("name")
    if not isinstance(declared, str):
        raise PackageValidationError("SKILL.md frontmatter requires a scalar name")
    name = validate_skill_name(declared)
    if expected_name is not None and name != expected_name:
        raise PackageValidationError(
            f"skill package declares {name!r}, expected {expected_name!r}"
        )

    files: list[PackageFile] = []
    folded_paths: dict[str, str] = {}
    total_bytes = 0
    for current, dirs, names in os.walk(package_root, topdown=True, followlinks=False):
        current_path = Path(current)
        if current_path == package_root and ".git" in dirs:
            dirs.remove(".git")
        for directory in tuple(dirs):
            path = current_path / directory
            relative = path.relative_to(package_root).as_posix()
            try:
                validate_portable_relative_path(relative)
            except ValueError as exc:
                raise PackageValidationError(str(exc)) from exc
            _record_portable_path(folded_paths, relative)
            if path.is_symlink():
                raise PackageValidationError(
                    f"skill package contains a symlink: {relative}"
                )
            mode = path.stat(follow_symlinks=False).st_mode
            if not stat.S_ISDIR(mode):
                raise PackageValidationError(f"skill package contains an unsupported entry: {path}")
        for filename in names:
            path = current_path / filename
            relative = path.relative_to(package_root).as_posix()
            try:
                validate_portable_relative_path(relative)
            except ValueError as exc:
                raise PackageValidationError(str(exc)) from exc
            _record_portable_path(folded_paths, relative)
            if path.is_symlink():
                raise PackageValidationError(f"skill package contains a symlink: {relative}")
            file_stat = path.stat(follow_symlinks=False)
            if not stat.S_ISREG(file_stat.st_mode):
                raise PackageValidationError(
                    f"skill package contains an unsupported file type: {relative}"
                )
            try:
                content = path.read_bytes()
            except OSError as exc:
                raise PackageValidationError(f"cannot read skill package file {relative}: {exc}") from exc
            total_bytes += len(content)
            if len(files) + 1 > MAX_PACKAGE_FILES:
                raise PackageValidationError(
                    f"skill package exceeds the {MAX_PACKAGE_FILES}-file limit"
                )
            if total_bytes > MAX_PACKAGE_BYTES:
                raise PackageValidationError(
                    f"skill package exceeds the {MAX_PACKAGE_BYTES}-byte limit"
                )
            files.append(
                PackageFile(
                    relative,
                    hashlib.sha256(content).hexdigest(),
                    len(content),
                    bool(file_stat.st_mode & 0o111),
                )
            )

    files.sort(key=lambda item: item.path)
    digest = hashlib.sha256()
    for item in files:
        path_bytes = item.path.encode("utf-8")
        digest.update(len(path_bytes).to_bytes(4, "big"))
        digest.update(path_bytes)
        digest.update(b"file\0")
        digest.update(bytes.fromhex(item.sha256))
        digest.update(b"\1" if item.executable else b"\0")
    return PackageSnapshot(name, package_root, digest.hexdigest(), tuple(files))


def copy_package(snapshot: PackageSnapshot, destination: str | Path) -> None:
    """Copy every validated file from a snapshot into a new directory."""
    target = Path(destination)
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"skill package destination already exists: {target}")
    target.mkdir(parents=True)
    try:
        for item in snapshot.files:
            source = snapshot.root / item.path
            output = target / item.path
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, output, follow_symlinks=False)
            if item.executable:
                output.chmod(output.stat().st_mode | 0o100)
    except BaseException:
        shutil.rmtree(target, ignore_errors=True)
        raise


def manifest_value(snapshot: PackageSnapshot) -> list[dict[str, object]]:
    """Serialise snapshot file identities for durable baseline tracking."""
    return [
        {
            "path": item.path,
            "sha256": item.sha256,
            "size": item.size,
            "executable": item.executable,
        }
        for item in snapshot.files
    ]


def _record_portable_path(folded_paths: dict[str, str], relative: str) -> None:
    folded = relative.casefold()
    previous = folded_paths.get(folded)
    if previous is not None and previous != relative:
        raise PackageValidationError(
            "skill package has a case-folding path collision: "
            f"{previous!r} and {relative!r}"
        )
    folded_paths[folded] = relative
