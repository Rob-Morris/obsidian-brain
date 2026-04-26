"""Slug generation and title-to-filename conversion."""

import secrets
import re
import unicodedata

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_VALID_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_HAS_ALPHA_RE = re.compile(r"[a-z]")
_CLEAN_CHARS_RE = re.compile(r"[^a-z0-9\s]+")

# Characters unsafe in filenames across macOS, Windows, and Linux
_UNSAFE_FILENAME_RE = re.compile(r'[/\\:*?"<>|]')
_MULTI_SPACE_RE = re.compile(r"  +")

SLUG_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
SLUG_SUFFIX_LENGTH = 3
SLUG_SUFFIX_MAX_RETRIES = 100
SLUG_KEYWORD_BUDGET = 20
SLUG_TITLE_KEY_LIMIT = 20
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


def _to_ascii_lower(text):
    """Transliterate unicode to lowercase ASCII (e.g. é → e)."""
    normalised = unicodedata.normalize("NFKD", text or "")
    return normalised.encode("ascii", "ignore").decode("ascii").lower()


def title_to_slug(title):
    """Convert a human-readable title to a machine slug for hub tags.

    Lowercase, replace non-alphanumeric runs with hyphens, strip edges.
    Used for hub tags (project/{slug}, workspace/{slug}), not filenames.
    Output matches: [a-z0-9]+(?:-[a-z0-9]+)*
    """
    return _SLUG_RE.sub("-", _to_ascii_lower(title)).strip("-")


def is_valid_key(key):
    """Return whether *key* matches the canonical artefact key contract."""
    if not isinstance(key, str):
        return False
    if not (1 <= len(key) <= 64):
        return False
    if not _VALID_SLUG_RE.fullmatch(key):
        return False
    return bool(_HAS_ALPHA_RE.search(key))


def validate_key(key):
    """Raise ValueError when *key* does not match the canonical contract."""
    if is_valid_key(key):
        return key
    raise ValueError(
        "INVALID_KEY: key must match ^[a-z0-9]+(-[a-z0-9]+)*$, contain at least one letter, and be 1–64 characters long"
    )


def extract_slug_keywords(title, max_words=2, budget=SLUG_KEYWORD_BUDGET):
    """Pick up to ``max_words`` distinctive keywords joined by ``-``, fitting ``budget``."""
    cleaned = _CLEAN_CHARS_RE.sub(" ", _to_ascii_lower(title))
    tokens = [tok for tok in cleaned.split() if tok]
    if not tokens:
        return SLUG_SENTINEL

    non_stopwords = [tok for tok in tokens if tok not in SLUG_STOPWORDS]
    candidates = non_stopwords or tokens

    ranked = sorted(enumerate(candidates), key=lambda p: (p[1].isdigit(), -len(p[1]), p[0]))
    if max_words >= 2 and len(ranked) >= 2:
        (idx_a, word_a), (idx_b, word_b) = ranked[0], ranked[1]
        if len(word_a) + 1 + len(word_b) <= budget:
            first, second = ((word_a, word_b) if idx_a < idx_b else (word_b, word_a))
            return f"{first}-{second}"

    longest = ranked[0][1][:budget]
    return longest or SLUG_SENTINEL


def generate_slug_suffix(length=SLUG_SUFFIX_LENGTH, alphabet=SLUG_ALPHABET):
    """Generate a short confusable-free random suffix."""
    return "".join(secrets.choice(alphabet) for _ in range(length))


def generate_contextual_slug(title):
    """Generate a contextual ``{keywords}-{suffix}`` slug candidate."""
    return f"{extract_slug_keywords(title)}-{generate_slug_suffix()}"


def derive_distinctive_slug(title, taken):
    """Pick the clearest unique slug for a title.

    A multi-token pair wins by shape; otherwise the longest single
    keyword. The random-suffix slug is reserved for collision resolution.
    """
    pair = extract_slug_keywords(title, max_words=2, budget=64)
    if "-" in pair and is_valid_key(pair) and pair not in taken:
        return pair

    single = extract_slug_keywords(title, max_words=1)
    if is_valid_key(single) and single not in taken:
        return single

    keywords = extract_slug_keywords(title)
    for _ in range(SLUG_SUFFIX_MAX_RETRIES):
        candidate = f"{keywords}-{generate_slug_suffix()}"
        if candidate not in taken:
            return candidate
    raise RuntimeError(
        f"derive_distinctive_slug: could not find a free suffix for {title!r} "
        f"after {SLUG_SUFFIX_MAX_RETRIES} attempts"
    )


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
