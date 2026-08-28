from __future__ import annotations

import ctypes
import errno
import os
from pathlib import Path
import sys
import tempfile


_AT_FDCWD = -100
_RENAME_NOREPLACE = 0x00000001
_RENAME_EXCL = 0x00000004


def create_staging_directory(output: Path) -> Path:
    if os.path.lexists(output):
        raise FileExistsError(errno.EEXIST, "fixture output already exists", output)
    return Path(
        tempfile.mkdtemp(
            prefix=f".{output.name}.brain-lab-partial-",
            dir=output.parent,
        )
    )


def _raise_rename_error(source: Path, destination: Path) -> None:
    error_number = ctypes.get_errno()
    raise OSError(error_number, os.strerror(error_number), str(destination), str(source))


def publish_directory_exclusive(source: Path, destination: Path) -> None:
    library = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    if sys.platform == "darwin":
        rename = library.renamex_np
        rename.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
        rename.restype = ctypes.c_int
        if rename(source_bytes, destination_bytes, _RENAME_EXCL) != 0:
            _raise_rename_error(source, destination)
        return
    if sys.platform.startswith("linux"):
        try:
            rename = library.renameat2
        except AttributeError as exc:
            raise OSError(
                errno.ENOTSUP,
                "exclusive directory publication requires renameat2",
                str(destination),
            ) from exc
        rename.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        rename.restype = ctypes.c_int
        if (
            rename(
                _AT_FDCWD,
                source_bytes,
                _AT_FDCWD,
                destination_bytes,
                _RENAME_NOREPLACE,
            )
            != 0
        ):
            _raise_rename_error(source, destination)
        return
    raise OSError(
        errno.ENOTSUP,
        "exclusive directory publication is supported only on macOS and Linux",
        str(destination),
    )
