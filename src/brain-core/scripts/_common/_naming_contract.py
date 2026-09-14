"""Host-independent validation for taxonomy filename patterns."""

from _portable_path import has_windows_drive_prefix


def validate_naming_pattern(pattern):
    """Require one filename pattern, independent of the host's path syntax.

    Folders belong to the separate filing contract. Literal dots within a
    filename are valid; dot path segments, separators and drive prefixes are not.
    """
    if (
        not isinstance(pattern, str)
        or not pattern
        or pattern in {".", ".."}
        or "/" in pattern
        or "\\" in pattern
        or has_windows_drive_prefix(pattern)
    ):
        raise ValueError(
            f"Naming pattern must be a single filename without a path, "
            f"drive prefix or traversal: {pattern!r}"
        )
