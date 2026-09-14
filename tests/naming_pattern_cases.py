"""Shared behavioural cases for the filename-only naming contract."""

UNSAFE_NAMING_PATH_PATTERNS = (
    "../{Title}.md",
    "sub/{Title}.md",
    "/tmp/{Title}.md",
    r"..\{Title}.md",
    r"sub\{Title}.md",
    r"\{Title}.md",
    r"C:\{Title}.md",
    "C:{Title}.md",
    r"\\server\share\{Title}.md",
)

UNSAFE_CACHED_NAMING_PATTERNS = UNSAFE_NAMING_PATH_PATTERNS + (".", "..")
