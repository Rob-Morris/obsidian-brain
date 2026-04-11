"""Slug generation and title-to-filename conversion."""

import re
import unicodedata

_SLUG_RE = re.compile(r"[^a-z0-9]+")

# Characters unsafe in filenames across macOS, Windows, and Linux
_UNSAFE_FILENAME_RE = re.compile(r'[/\\:*?"<>|]')
_MULTI_SPACE_RE = re.compile(r"  +")


def title_to_slug(title):
    """Convert a human-readable title to a machine slug for hub tags.

    Lowercase, replace non-alphanumeric runs with hyphens, strip edges.
    Used for hub tags (project/{slug}, workspace/{slug}), not filenames.
    Output matches: [a-z0-9]+(?:-[a-z0-9]+)*
    """
    # Transliterate unicode to ASCII approximations (e.g. é → e)
    normalised = unicodedata.normalize("NFKD", title)
    ascii_only = normalised.encode("ascii", "ignore").decode("ascii")
    slug = _SLUG_RE.sub("-", ascii_only.lower()).strip("-")
    # Collapse any remaining double hyphens
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug


def title_to_filename(title):
    """Convert a human-readable title to a filesystem-safe filename stem.

    Generous: preserves spaces, capitalisation, and unicode. Only strips
    characters unsafe on macOS/Windows/Linux filesystems. Trims whitespace
    and collapses multiple spaces.
    """
    result = _UNSAFE_FILENAME_RE.sub("", title)
    result = _MULTI_SPACE_RE.sub(" ", result).strip()
    return result


def slug_to_title(slug):
    """Convert a hyphenated slug to a human-readable title.

    Best-guess reverse of title_to_slug() — replaces hyphens with spaces
    and title-cases each word. Won't recover the original title exactly
    (e.g. acronyms, punctuation).
    """
    return slug.replace("-", " ").title()
