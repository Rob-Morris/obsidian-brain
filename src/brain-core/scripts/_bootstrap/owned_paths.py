"""Fixed Brain-owned filesystem namespaces, independent of content policy."""

from __future__ import annotations

import os
from pathlib import Path
import stat


class UnsafeFilesystemLayoutError(OSError):
    """Content-safe refusal of a redirected or unexpected owned object."""

    def __init__(self, relative: str | Path):
        super().__init__(
            f"Unsafe Brain-owned filesystem layout at {relative}. "
            "Restore an ordinary directory/file at this location before retrying; "
            "Brain has not repaired or followed the redirected object."
        )


def _redirected(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _relative_parts(relative: str | Path) -> tuple[str, ...]:
    path = Path(relative)
    if path.anchor or not path.parts or any(
        part in {".", ".."} for part in path.parts
    ):
        raise ValueError("An owned filesystem capability requires a relative path")
    return path.parts


def _validate_directory(info: os.stat_result, relative: str | Path) -> None:
    if _redirected(info) or not stat.S_ISDIR(info.st_mode):
        raise UnsafeFilesystemLayoutError(relative)


def owned_directory(
    root: str | Path, relative: str | Path, *, create: bool = False
) -> Path:
    """Admit ordinary owned components below a canonical selected root.

    Host aliases above the selected root are allowed. Missing components are
    created only when requested; observations never create or repair a layout.
    This is a static-layout check, not hostile concurrent-replacement confinement.
    """
    parts = _relative_parts(relative)
    selected = Path(root).resolve()
    if not selected.is_dir():
        raise FileNotFoundError("The selected filesystem root is unavailable")
    current = selected
    for part in parts:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            if not create:
                raise
            try:
                current.mkdir()
            except FileExistsError:
                pass
            info = current.lstat()
        _validate_directory(info, relative)
    return current


def owned_file(
    root: str | Path, relative: str | Path, *, missing_ok: bool = False,
    single_link: bool = False,
) -> Path:
    """Admit one regular owned record without following directory/endpoint links."""
    parts = _relative_parts(relative)
    if len(parts) < 2:
        raise ValueError("Owned records require an owned parent directory")
    parent = owned_directory(root, Path(*parts[:-1]))
    path = parent / parts[-1]
    try:
        info = path.lstat()
    except FileNotFoundError:
        if missing_ok:
            return path
        raise
    validate_owned_record(info, relative, single_link=single_link)
    return path


def validate_owned_record(
    info: os.stat_result, relative: str | Path, *, single_link: bool = False
) -> None:
    """Check the opened/lstat record before changing its bytes or permissions."""
    if (_redirected(info) or not stat.S_ISREG(info.st_mode)
            or (single_link and info.st_nlink != 1)):
        raise UnsafeFilesystemLayoutError(relative)


def owned_output_paths(root: str | Path, relatives) -> tuple[Path, ...]:
    """Admit a complete fixed output set without creating missing components.

    Owners may create approved parents only after every destination passes;
    a refused later endpoint must not leave earlier readiness changes behind.
    """
    selected = Path(root).resolve()
    if not selected.is_dir():
        raise FileNotFoundError("The selected filesystem root is unavailable")
    approved = []
    for relative in relatives:
        parts = _relative_parts(relative)
        current = selected
        missing = False
        for part in parts[:-1]:
            current /= part
            if missing:
                continue
            try:
                info = current.lstat()
            except FileNotFoundError:
                missing = True
                continue
            _validate_directory(info, relative)
        target = current / parts[-1]
        if not missing:
            try:
                info = target.lstat()
            except FileNotFoundError:
                pass
            else:
                validate_owned_record(info, relative, single_link=True)
        approved.append(target)
    return tuple(approved)
