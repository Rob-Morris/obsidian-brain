"""Slug generation and title-to-filename conversion."""

import secrets
import re
import unicodedata

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_VALID_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# Characters unsafe in filenames across macOS, Windows, and Linux
_UNSAFE_FILENAME_RE = re.compile(r'[/\\:*?"<>|]')
_MULTI_SPACE_RE = re.compile(r"  +")

SLUG_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
SLUG_SUFFIX_LENGTH = 3
SLUG_KEYWORD_MAX = 12
SLUG_SENTINEL = "husk"
SLUG_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "been", "being", "but",
        "by", "for", "from", "had", "has", "have", "he", "her", "hers",
        "him", "his", "i", "if", "in", "into", "is", "it", "its", "itself",
        "me", "my", "myself", "of", "on", "or", "our", "ours", "ourselves",
        "she", "so", "that", "the", "their", "theirs", "them", "themselves",
        "there", "these", "they", "this", "those", "to", "under", "up",
        "was", "we", "were", "what", "when", "where", "which", "who",
        "whom", "why", "with", "you", "your", "yours", "yourself",
        "yourselves",
    }
)


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


def is_valid_key(key):
    """Return whether *key* matches the canonical artefact key contract."""
    if not isinstance(key, str):
        return False
    if not (1 <= len(key) <= 64):
        return False
    return bool(_VALID_SLUG_RE.fullmatch(key))


def validate_key(key):
    """Raise ValueError when *key* does not match the canonical contract."""
    if is_valid_key(key):
        return key
    raise ValueError(
        "INVALID_KEY: key must match ^[a-z0-9]+(-[a-z0-9]+)*$ and be 1–64 characters long"
    )


def extract_slug_keyword(title):
    """Extract the deterministic keyword prefix for a contextual slug."""
    normalised = unicodedata.normalize("NFKD", title or "")
    ascii_only = normalised.encode("ascii", "ignore").decode("ascii").lower()
    cleaned = re.sub(r"[^a-z0-9\s]+", " ", ascii_only)
    tokens = [tok for tok in cleaned.split() if tok]
    if not tokens:
        return SLUG_SENTINEL

    non_stopwords = [tok for tok in tokens if tok not in SLUG_STOPWORDS]
    candidates = non_stopwords or tokens
    keyword = max(candidates, key=len)[:SLUG_KEYWORD_MAX]
    return keyword or SLUG_SENTINEL


def generate_slug_suffix(length=SLUG_SUFFIX_LENGTH, alphabet=SLUG_ALPHABET):
    """Generate a short confusable-free random suffix."""
    return "".join(secrets.choice(alphabet) for _ in range(length))


def generate_contextual_slug(title):
    """Generate a contextual ``{keyword}-{suffix}`` slug candidate."""
    return f"{extract_slug_keyword(title)}-{generate_slug_suffix()}"


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
