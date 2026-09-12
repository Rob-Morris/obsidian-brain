"""Dependency-free grammar for portable relative paths."""

from __future__ import annotations

import re


_WINDOWS_FORBIDDEN_FILENAME_CHARS = frozenset('<>:"|?*')
_WINDOWS_RESERVED_FILE_STEMS = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "CONIN$",
        "CONOUT$",
        *(f"COM{number}" for number in range(1, 10)),
        *(f"LPT{number}" for number in range(1, 10)),
    }
)


def validate_windows_portable_filename_segment(name):
    """Return one filename segment that is valid on Windows filesystems."""

    if any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise ValueError("filename must not contain control characters")
    if set(name) & _WINDOWS_FORBIDDEN_FILENAME_CHARS:
        raise ValueError("filename contains a Windows-forbidden character")
    if name.endswith((".", " ")):
        raise ValueError("filename must not end with a dot or space")
    if name.split(".", 1)[0].upper() in _WINDOWS_RESERVED_FILE_STEMS:
        raise ValueError("filename uses a Windows-reserved name")
    return name


def validate_portable_relative_path(path, *, allow_trailing_slash=False):
    """Return *path* when it is a normalised portable relative path.

    Manifest paths ship across operating systems and later become filesystem
    write targets. Reject platform-specific separators and traversal before a
    caller joins the value to any trusted root.
    """

    if not isinstance(path, str) or not path:
        raise ValueError("path must be a non-empty string")
    if "\\" in path or path.startswith("/") or re.match(r"^[A-Za-z]:", path):
        raise ValueError(f"path must be portable and relative: {path!r}")

    trailing_slash = path.endswith("/")
    if trailing_slash and not allow_trailing_slash:
        raise ValueError(f"path must not end with '/': {path!r}")
    candidate = path[:-1] if trailing_slash else path
    parts = candidate.split("/")
    if not candidate or any(part in {"", ".", ".."} for part in parts):
        raise ValueError(
            f"path must be normalised without empty or dot segments: {path!r}"
        )
    for part in parts:
        try:
            validate_windows_portable_filename_segment(part)
        except ValueError as exc:
            raise ValueError(
                f"invalid path segment {part!r} in {path!r}: {exc}"
            ) from exc
    return path
