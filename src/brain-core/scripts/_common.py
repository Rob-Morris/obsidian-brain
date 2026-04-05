#!/usr/bin/env python3
"""
_common.py — Shared utilities for brain-core scripts.

Provides vault root discovery, version reading, filesystem scanning,
frontmatter parsing, serialisation, slug generation, and BM25 tokenisation.
All brain-core scripts import from this module rather than duplicating
these functions.
"""

import json
import os
import random
import re
import string
import sys
import tempfile
import unicodedata
from collections import namedtuple
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# _Temporal contains temporal artefacts — scanned separately from living types
TEMPORAL_DIR = "_Temporal"

_FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


# ---------------------------------------------------------------------------
# Vault root discovery
# ---------------------------------------------------------------------------

def _is_vault_root(path):
    """Check if a directory is a Brain vault root."""
    return (path / ".brain-core" / "VERSION").is_file() or (path / "Agents.md").is_file()


def find_vault_root(vault_arg=None):
    """Find a Brain vault root.

    If vault_arg is given, validates it directly. Otherwise checks cwd first,
    then walks up from script location.
    """
    if vault_arg:
        p = Path(vault_arg).resolve()
        if _is_vault_root(p):
            return p
        print(f"Error: {vault_arg} is not a vault root.", file=sys.stderr)
        sys.exit(1)

    # Check cwd first (allows running from dev repo: cd vault && python3 /path/to/script)
    cwd = Path(os.getcwd()).resolve()
    if _is_vault_root(cwd):
        return cwd

    # Walk up from script location (works when installed inside .brain-core/scripts/)
    current = Path(__file__).resolve().parent
    for _ in range(10):
        current = current.parent
        if _is_vault_root(current):
            return current
    print("Error: could not find vault root.", file=sys.stderr)
    sys.exit(1)


def read_version(vault_root):
    """Read brain-core version from the canonical VERSION file."""
    version_file = os.path.join(str(vault_root), ".brain-core", "VERSION")
    with open(version_file, "r", encoding="utf-8") as f:
        return f.read().strip()


# ---------------------------------------------------------------------------
# Timestamp utilities
# ---------------------------------------------------------------------------

def now_iso():
    """Return the current local datetime as an ISO 8601 string with timezone offset."""
    return datetime.now(timezone.utc).astimezone().isoformat()


_DATE_PLACEHOLDER_RE = re.compile(r"\{\{date:([^}]+)\}\}")

# Mapping from template date tokens to strftime codes.  Longest tokens
# first so ``YYYYMMDD`` is matched before ``YYYY``.
_DATE_TOKEN_MAP = [
    ("YYYYMMDD", "%Y%m%d"),
    ("YYYY-MM-DD", "%Y-%m-%d"),
    ("YYYY", "%Y"),
    ("ddd", "%a"),
    ("MM", "%m"),
    ("DD", "%d"),
]


def substitute_template_vars(content, template_vars=None, _now=None):
    """Replace template placeholders in *content*.

    Two kinds of substitution:

    1. **Date placeholders** — ``{{date:FORMAT}}`` where *FORMAT* uses
       tokens like ``YYYY``, ``MM``, ``DD``, ``ddd``.  Replaced with the
       formatted current datetime.
    2. **Custom variables** — arbitrary string → string pairs supplied via
       *template_vars*.  Applied longest-key-first to avoid partial matches
       (e.g. ``SOURCE_DOC_PATH|SOURCE_DOC_TITLE`` before ``SOURCE_DOC_PATH``).

    Pass *_now* to pin the datetime for deterministic tests.
    """
    if not content:
        return content

    now = _now if _now is not None else datetime.now(timezone.utc).astimezone()

    def _replace_date(m):
        fmt = m.group(1)
        for token, code in _DATE_TOKEN_MAP:
            fmt = fmt.replace(token, code)
        return now.strftime(fmt)

    content = _DATE_PLACEHOLDER_RE.sub(_replace_date, content)

    if template_vars:
        for key in sorted(template_vars, key=len, reverse=True):
            content = content.replace(key, template_vars[key])

    return content


def unique_filename(folder, stem, ext=".md"):
    """Return a filename in *folder* that doesn't collide with existing files.

    If ``folder/stem.ext`` doesn't exist, returns ``stem.ext``.
    Otherwise appends a random 3-char suffix: ``stem abc.ext``.
    """
    filename = f"{stem}{ext}"
    while os.path.isfile(os.path.join(folder, filename)):
        suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=3))
        filename = f"{stem} {suffix}{ext}"
    return filename


# ---------------------------------------------------------------------------
# Body file resolution
# ---------------------------------------------------------------------------

def resolve_body_file(body, body_file, *, vault_root=None):
    """Return body content, reading from body_file if provided.

    Raises ValueError if both are specified, the file cannot be read,
    or (when *vault_root* is set) the path resolves outside both the
    vault and the system temp directory.

    Returns (body, cleanup_path).  *cleanup_path* is set only when the
    file was read from the system temp directory (caller should delete);
    it is None for vault files or when body_file was not used.
    """
    if body_file and body:
        raise ValueError("Cannot specify both 'body' and 'body_file'. Use one or the other.")
    if not body_file:
        return body, None

    abs_path = os.path.realpath(body_file)

    in_tmp = False
    if vault_root is not None:
        tmp_root = os.path.realpath(tempfile.gettempdir())
        try:
            resolve_and_check_bounds(abs_path, tmp_root)
            in_tmp = True
        except ValueError:
            # Not in tmp — must be inside vault
            resolve_and_check_bounds(abs_path, vault_root)

    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            return f.read(), abs_path if in_tmp else None
    except FileNotFoundError:
        raise ValueError(f"body_file not found: {body_file}")
    except Exception as e:
        raise ValueError(f"Failed to read body_file: {e}")


# ---------------------------------------------------------------------------
# Filesystem scanning (DD-016)
# ---------------------------------------------------------------------------

def is_system_dir(name):
    """Check if a directory name is infrastructure (not a living artefact).

    Convention: any folder starting with _ or . is excluded from living type
    discovery. _Temporal contains artefacts but is scanned separately — it is
    still excluded here because its children are temporal, not living.
    """
    return name.startswith("_") or name.startswith(".")


def is_archived_path(path):
    """Return True if *path* sits inside an ``_Archive/`` directory."""
    return "/_Archive/" in path or path.startswith("_Archive/")


def scan_living_types(vault_root):
    """Discover living artefact types from root-level non-system directories."""
    types = []
    for entry in sorted(os.listdir(vault_root)):
        full = os.path.join(vault_root, entry)
        if not os.path.isdir(full):
            continue
        if is_system_dir(entry):
            continue
        key = entry.lower().replace(" ", "-")
        types.append({
            "folder": entry,
            "key": key,
            "classification": "living",
            "type": "living/" + key,
            "path": entry,
        })
    return types


def scan_temporal_types(vault_root):
    """Discover temporal artefact types from _Temporal/ subfolders."""
    temporal_dir = os.path.join(vault_root, TEMPORAL_DIR)
    if not os.path.isdir(temporal_dir):
        return []
    types = []
    for entry in sorted(os.listdir(temporal_dir)):
        full = os.path.join(temporal_dir, entry)
        if not os.path.isdir(full):
            continue
        if entry.startswith(".") or entry.startswith("_"):
            continue
        key = entry.lower().replace(" ", "-")
        types.append({
            "folder": entry,
            "key": key,
            "classification": "temporal",
            "type": "temporal/" + key,
            "path": os.path.join(TEMPORAL_DIR, entry),
        })
    return types


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------

def parse_frontmatter(text):
    """Extract frontmatter fields from markdown text. Returns (fields, body)."""
    m = _FM_RE.match(text)
    if not m:
        return {}, text

    fm_text = m.group(1)
    body = text[m.end():]
    fields = {}
    # Simple YAML parser for flat fields and list fields
    fm_lines = fm_text.split("\n")
    pending_list_key = None

    for line in fm_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # List item under a pending key
        if stripped.startswith("- ") and pending_list_key:
            fields[pending_list_key].append(stripped[2:].strip().strip("'\""))
            continue

        # Non-list-item ends any pending list collection
        if pending_list_key:
            pending_list_key = None

        colon_idx = stripped.find(":")
        if colon_idx < 0:
            continue

        key = stripped[:colon_idx].strip()
        value = stripped[colon_idx + 1:].strip()

        # Handle inline list: [item1, item2]
        if value.startswith("["):
            inner = value.strip("[]")
            fields[key] = [t.strip().strip("'\"") for t in inner.split(",") if t.strip()]
            continue

        if not value:
            # Empty value after colon — could be a multi-line list; collect on next lines
            fields[key] = []
            pending_list_key = key
            continue

        # Strip quotes
        if (value.startswith("'") and value.endswith("'")) or \
           (value.startswith('"') and value.endswith('"')):
            value = value[1:-1]

        fields[key] = value

    return fields, body


# ---------------------------------------------------------------------------
# Path and wikilink utilities
# ---------------------------------------------------------------------------

def strip_md_ext(path):
    """Strip .md extension from a path, returning the wikilink stem."""
    return path[:-3] if path.endswith(".md") else path


def build_wikilink_pattern(*stems):
    """Build a compiled regex matching wikilinks to any of the given stems.

    Matches all Obsidian wikilink forms: plain, with heading anchors,
    block references, aliases, and embed prefixes (``![[…]]``).
    Longer stems are tried first so a full-path stem is preferred over a
    filename-only stem when both could match.

    Named groups:
        ``prefix`` — ``[[`` or ``![[``
        ``stem``   — the stem that matched
        ``anchor`` — heading/block-ref including leading ``#``, or *None*
        ``alias``  — display text including leading ``|``, or *None*
    """
    sorted_stems = sorted(stems, key=len, reverse=True)
    alt = "|".join(re.escape(s) for s in sorted_stems)
    return re.compile(
        r"(?P<prefix>!?\[\[)(?P<stem>" + alt + r")"
        r"(?P<anchor>#[^\]|]*)?(?P<alias>\|[^\]]*)?\]\]"
    )


def make_wikilink_replacer(stem_map):
    """Return a :func:`re.sub` callable that rewrites matched wikilink stems.

    Preserves the embed prefix, heading anchor, and alias exactly as
    written — only the stem portion is replaced via *stem_map*.
    """
    def _replace(m):
        return (
            f"{m.group('prefix')}{stem_map[m.group('stem')]}"
            f"{m.group('anchor') or ''}{m.group('alias') or ''}]]"
        )
    return _replace


def _iter_vault_md_files(vault_root):
    """Yield ``(dirpath, filename)`` for every ``.md`` file in user-facing dirs.

    Skips system directories (``_Config``, ``.obsidian``, …) except
    ``_Temporal`` which contains artefacts.  ``_Archive/`` directories are
    intentionally skipped — archived files are frozen snapshots whose
    internal wikilinks are not updated on rename operations.
    """
    for dirpath, _dirnames, filenames in os.walk(vault_root):
        rel_dir = os.path.relpath(dirpath, vault_root)
        if rel_dir != "." and is_system_dir(os.path.basename(dirpath)):
            if not rel_dir.startswith(TEMPORAL_DIR):
                continue
        for fname in filenames:
            if fname.endswith(".md"):
                yield dirpath, fname


def find_duplicate_basenames(vault_root, basename_stem, limit=None):
    """Return relative paths of .md files whose basename stem matches.

    When *limit* is set the search stops as soon as that many matches are
    found, which avoids a full vault walk when the caller only needs to
    know whether the name is unique (``limit=2``).
    """
    matches = []
    for dirpath, fname in _iter_vault_md_files(vault_root):
        if os.path.splitext(fname)[0] == basename_stem:
            matches.append(
                os.path.relpath(os.path.join(dirpath, fname), vault_root)
            )
            if limit is not None and len(matches) >= limit:
                return matches
    return matches


def resolve_wikilink_stems(vault_root, old_path, new_path=None):
    """Build multi-stem matching data for wikilink rewriting.

    Returns ``(pattern, stem_map)`` where *pattern* is a compiled regex
    matching both full-path and (if unambiguous) filename-only wikilinks,
    and *stem_map* maps each matched stem to its replacement.

    When *new_path* is ``None`` (delete case) the map values are the
    basename stem (used for strikethrough display).
    """
    old_stem = strip_md_ext(old_path)
    old_basename = os.path.splitext(os.path.basename(old_path))[0]

    stems = [old_stem]
    stem_map = {}

    if new_path is not None:
        new_stem = strip_md_ext(new_path)
        new_basename = os.path.splitext(os.path.basename(new_path))[0]
        stem_map[old_stem] = new_stem
    else:
        stem_map[old_stem] = old_basename

    if old_basename != old_stem:
        if len(find_duplicate_basenames(vault_root, old_basename, limit=2)) <= 1:
            stems.append(old_basename)
            if new_path is not None:
                stem_map[old_basename] = new_basename
            else:
                stem_map[old_basename] = old_basename

    pattern = build_wikilink_pattern(*stems)
    return pattern, stem_map


def replace_wikilinks_in_vault(vault_root, pattern, replacement):
    """Walk all .md files in the vault and apply a wikilink regex substitution.

    Skips system directories except _Temporal (which contains artefacts).
    replacement can be a string or a callable (as per re.sub).

    Returns the total number of substitutions made.
    """
    total = 0
    for dirpath, fname in _iter_vault_md_files(vault_root):
        fpath = os.path.join(dirpath, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError:
            continue
        new_content, count = pattern.subn(replacement, content)
        if count > 0:
            safe_write(fpath, new_content, bounds=vault_root)
            total += count
    return total


# ---------------------------------------------------------------------------
# Wikilink extraction and file index
# ---------------------------------------------------------------------------

_WIKILINK_EXTRACT_RE = re.compile(r"(!?)\[\[([^\]]+)\]\]")

# Directories to skip entirely when building the vault file index
_INDEX_SKIP_DIRS = {".git", ".obsidian", ".venv", ".brain-core", "__pycache__", "_Archive"}


def extract_wikilinks(text):
    """Extract all wikilinks from markdown text.

    Returns a list of dicts with keys:
        stem   — link target (without anchor or alias)
        anchor — heading/block ref including ``#``, or None
        alias  — display text (without leading ``|``), or None
        is_embed — True for ``![[…]]`` embeds
        start  — character offset of the match (for code-block filtering)

    Skips same-file anchors (``[[#heading]]``) and template placeholders
    (``[[{{…}}]]``).
    """
    results = []
    for m in _WIKILINK_EXTRACT_RE.finditer(text):
        is_embed = m.group(1) == "!"
        inner = m.group(2)

        # Split off alias (last |)
        alias = None
        if "|" in inner:
            inner, alias = inner.rsplit("|", 1)

        # Split off anchor (first #)
        anchor = None
        if "#" in inner:
            inner, anchor_part = inner.split("#", 1)
            anchor = "#" + anchor_part

        stem = inner.strip()

        # Skip same-file anchors and template placeholders
        if not stem or stem.startswith("#"):
            continue
        if "{{" in stem:
            continue

        results.append({
            "stem": stem,
            "anchor": anchor,
            "alias": alias,
            "is_embed": is_embed,
            "start": m.start(),
        })
    return results


def build_vault_file_index(vault_root):
    """Build a complete file index for wikilink resolution.

    Walks the entire vault (skipping ``.git``, ``.obsidian``, ``.venv``,
    ``.brain-core``, ``__pycache__``).

    Returns a dict with:
        md_basenames  — ``{lowercase_stem: [rel_path, …]}`` for ``.md`` files
        all_basenames — ``{lowercase_basename_with_ext: [rel_path, …]}`` for all files
        md_relpaths   — ``set`` of lowercase relative path stems (no ``.md``) for
                        path-qualified resolution
    """
    from collections import defaultdict

    md_basenames = defaultdict(list)
    all_basenames = defaultdict(list)
    md_relpaths = set()

    for dirpath, dirnames, filenames in os.walk(vault_root):
        # Prune skipped directories in-place so os.walk doesn't descend
        dirnames[:] = [d for d in dirnames if d not in _INDEX_SKIP_DIRS]

        for fname in filenames:
            rel_path = os.path.relpath(os.path.join(dirpath, fname), vault_root)
            basename_lower = fname.lower()
            all_basenames[basename_lower].append(rel_path)

            if fname.endswith(".md"):
                stem = os.path.splitext(fname)[0].lower()
                md_basenames[stem].append(rel_path)
                md_relpaths.add(strip_md_ext(rel_path).lower())

    return {
        "md_basenames": dict(md_basenames),
        "all_basenames": dict(all_basenames),
        "md_relpaths": md_relpaths,
    }


# ---------------------------------------------------------------------------
# Broken wikilink resolution
# ---------------------------------------------------------------------------

Resolution = namedtuple("Resolution", ["status", "resolved_to", "candidates", "strategy"])

_DATED_PREFIX_RE = re.compile(r"^(\d{8})-([a-z][a-z0-9-]*)~", re.IGNORECASE)
_DATED_STEM_RE = re.compile(r"^(\d{8})-(.+)$")
_DOUBLEDASH_RE = re.compile(r"^(\d{8}-[a-z][a-z0-9-]*)--(.+)$", re.IGNORECASE)
_TILDE_SPACE_RE = re.compile(r"~\s+")


def _basename_stem(path):
    """Extract filename stem (no extension) from a path."""
    return os.path.splitext(os.path.basename(path))[0]


def _temporal_display_name(stem):
    """Return the display-name portion after the tilde, or *None* if not temporal."""
    m = _DATED_PREFIX_RE.match(stem)
    return stem[m.end():] if m else None


def _discover_temporal_prefixes(md_basenames):
    """Scan the file index to discover temporal artefact prefixes dynamically."""
    prefixes = set()
    for stem in md_basenames:
        m = _DATED_PREFIX_RE.match(stem)
        if m:
            prefixes.add(m.group(2).lower())
    return prefixes


def _lookup_basename(candidate_stem, md_basenames):
    """Look up files matching a basename stem (case-insensitive). Returns list of paths."""
    return md_basenames.get(candidate_stem.lower(), [])


def _lookup_temporal_display_name(stem, md_basenames):
    """Match *stem* against the display-name portion of temporal artefact stems.

    Users reference temporal artefacts by display name ("Colour Theory")
    but the file stem includes a dated prefix ("20260404-research~Colour Theory").
    """
    stem_lower = stem.lower()
    matches = []
    for indexed_stem, paths in md_basenames.items():
        display = _temporal_display_name(indexed_stem)
        if display is not None and display.lower() == stem_lower:
            matches.extend(paths)
    return matches


def resolve_artefact_path(name, vault_root, file_index=None):
    """Resolve a basename or partial path to a vault-relative artefact path.

    Tries case-insensitive basename lookup against the vault file index.
    Accepts names with or without .md extension, and with or without folder prefixes.
    Returns the single matching relative path.

    Args:
        file_index: optional pre-built index from build_vault_file_index() to
            avoid redundant vault walks.

    Raises:
        ValueError: if no match found or multiple matches (ambiguous).
    """
    stem = _basename_stem(name)
    if file_index is None:
        file_index = build_vault_file_index(vault_root)
    md_basenames = file_index["md_basenames"]

    matches = _lookup_basename(stem, md_basenames)
    if not matches:
        matches = _lookup_temporal_display_name(stem, md_basenames)

    if not matches:
        raise ValueError(f"No artefact found matching '{name}'")
    if len(matches) != 1:
        listing = "\n".join(f"  - {m}" for m in matches)
        raise ValueError(
            f"Basename '{stem}' matches multiple files:\n{listing}\n"
            "Use the full relative path to disambiguate."
        )
    return matches[0]


def _resolved(matches, strategy):
    """Build a resolved Resolution from a single-match list."""
    return Resolution("resolved", _basename_stem(matches[0]), matches, strategy)


def resolve_broken_link(target, file_index, temporal_prefixes=None):
    """Attempt to resolve a broken wikilink target.

    Tries a series of strategies to find the intended file when a wikilink
    target doesn't match any existing file by basename.

    Args:
        target: the wikilink stem (e.g. ``brain-inbox``, ``20260324-idea-log--x``)
        file_index: dict from ``build_vault_file_index()``
        temporal_prefixes: optional set of known prefixes (e.g. ``{"research", "plan"}``).
            If None, discovered automatically from the file index.

    Returns:
        Resolution(status, resolved_to, candidates, strategy)
    """
    md_basenames = file_index["md_basenames"]

    if temporal_prefixes is None:
        temporal_prefixes = _discover_temporal_prefixes(md_basenames)

    # Strategy 0: trailing backslash cleanup
    if target.endswith("\\"):
        cleaned = target.rstrip("\\")
        matches = _lookup_basename(cleaned, md_basenames)
        if len(matches) == 1:
            return _resolved(matches, "trailing_backslash")
        sub = resolve_broken_link(cleaned, file_index, temporal_prefixes)
        if sub.status == "resolved":
            return Resolution("resolved", sub.resolved_to, sub.candidates,
                              f"trailing_backslash+{sub.strategy}")

    working = target

    # Strategy 1: tilde-space normalization (run early, feeds into other strategies)
    if "~ " in working:
        normalised = _TILDE_SPACE_RE.sub("~", working)
        matches = _lookup_basename(normalised, md_basenames)
        if len(matches) == 1:
            return _resolved(matches, "tilde_space")
        working = normalised

    # Strategy 2: slug_to_title basename
    titled = slug_to_title(working)
    matches = _lookup_basename(titled, md_basenames)
    if len(matches) == 1:
        return _resolved(matches, "slug_to_title")
    elif len(matches) > 1:
        return Resolution("ambiguous", None, matches, "slug_to_title")

    # Strategy 3: dated double-dash → tilde
    dd_match = _DOUBLEDASH_RE.match(working)
    if dd_match:
        prefix_part = dd_match.group(1)
        slug_part = dd_match.group(2)
        candidate = f"{prefix_part}~{slug_to_title(slug_part)}"
        matches = _lookup_basename(candidate, md_basenames)
        if len(matches) == 1:
            return _resolved(matches, "doubledash_to_tilde")
        elif len(matches) > 1:
            return Resolution("ambiguous", None, matches, "doubledash_to_tilde")

    # Strategy 4: dated slug + temporal prefix
    dated_match = _DATED_STEM_RE.match(working)
    if dated_match and not dd_match:
        date_part = dated_match.group(1)
        slug_part = dated_match.group(2)
        titled_slug = slug_to_title(slug_part)
        for prefix in sorted(temporal_prefixes):
            candidate = f"{date_part}-{prefix}~{titled_slug}"
            matches = _lookup_basename(candidate, md_basenames)
            if len(matches) == 1:
                return _resolved(matches, f"dated_slug_prefix:{prefix}")

    # Strategy 5: path stripping — re-run strategies on the basename
    if "/" in working:
        basename_part = os.path.basename(working)
        matches = _lookup_basename(basename_part, md_basenames)
        if len(matches) == 1:
            return _resolved(matches, "path_strip")
        stripped = strip_md_ext(basename_part)
        matches = _lookup_basename(stripped, md_basenames)
        if len(matches) == 1:
            return _resolved(matches, "path_strip")
        sub = resolve_broken_link(stripped, file_index, temporal_prefixes)
        if sub.status == "resolved":
            return Resolution("resolved", sub.resolved_to, sub.candidates,
                              f"path_strip+{sub.strategy}")
        elif sub.status == "ambiguous":
            return Resolution("ambiguous", None, sub.candidates,
                              f"path_strip+{sub.strategy}")

    # Strategy 6: path segment title-casing
    if "/" in working:
        segments = working.split("/")
        basename_titled = slug_to_title(segments[-1])
        matches = _lookup_basename(basename_titled, md_basenames)
        if len(matches) == 1:
            return _resolved(matches, "path_segment_title")
        elif len(matches) > 1:
            return Resolution("ambiguous", None, matches, "path_segment_title")

    # Strategy 7: archive matching — check pre-filtered archive files
    archive_files = file_index.get("_archive_files")
    if archive_files is None:
        archive_files = [
            p for paths in md_basenames.values() for p in paths
            if is_archived_path(p)
        ]
    target_lower = os.path.basename(working).lower()
    archive_matches = []
    for p in archive_files:
        archived = _basename_stem(p).lower()
        if target_lower in archived or archived.endswith(target_lower):
            archive_matches.append(p)
    if len(archive_matches) == 1:
        return _resolved(archive_matches, "archive_match")
    elif len(archive_matches) > 1:
        return Resolution("ambiguous", None, archive_matches, "archive_match")

    return Resolution("unresolvable", None, [], "none")


# ---------------------------------------------------------------------------
# Slug generation
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Frontmatter serialisation
# ---------------------------------------------------------------------------

def serialize_frontmatter(fields, body=""):
    """Produce markdown with YAML frontmatter from a fields dict and body.

    Handles scalars and list fields (tags, aliases, cssclasses, etc.)
    as multi-line YAML lists (- item). Round-trips with parse_frontmatter().
    """
    lines = ["---"]
    for key, value in fields.items():
        if isinstance(value, list):
            if value:
                lines.append(f"{key}:")
                for item in value:
                    lines.append(f"  - {item}")
            else:
                lines.append(f"{key}: []")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    lines.append("")  # blank line after frontmatter

    fm_block = "\n".join(lines)
    if body:
        return fm_block + body
    return fm_block


# ---------------------------------------------------------------------------
# Markdown section parsing
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)[^\S\n]*$", re.MULTILINE)
_FENCE_RE = re.compile(r"^(`{3,}|~{3,})", re.MULTILINE)


def collect_headings(body):
    """Collect all markdown headings outside fenced code blocks.

    Returns list of (position, level, text, raw) tuples where:
    - position: character offset of the heading line start
    - level: heading level (1-6)
    - text: heading text (stripped, original case)
    - raw: full heading line (e.g. "## Alpha")
    """
    fenced = _fenced_ranges(body)
    headings = []
    for m in _HEADING_RE.finditer(body):
        if any(fs <= m.start() < fe for fs, fe in fenced):
            continue
        headings.append((
            m.start(),
            len(m.group(1)),
            m.group(2).strip(),
            m.group(0).strip(),
        ))
    return headings


def _fenced_ranges(body):
    """Return list of (start, end) character ranges for fenced code blocks."""
    fences = [(m.start(), m.end(), m.group(1)[0]) for m in _FENCE_RE.finditer(body)]
    ranges = []
    i = 0
    while i < len(fences):
        open_start, _, char = fences[i]
        close_idx = None
        for j in range(i + 1, len(fences)):
            if fences[j][2] == char:
                close_idx = j
                break
        if close_idx is not None:
            ranges.append((open_start, fences[close_idx][1]))
            i = close_idx + 1
        else:
            ranges.append((open_start, len(body)))
            i += 1
    return ranges


def find_section(body, heading, include_heading=False):
    """Find start/end of a markdown section by heading or callout title.

    Returns (start, end) character offsets into body, where:
    - start is the position after the heading/callout title line (including its newline)
    - end is the position before the next heading of same or higher level (or EOF);
      for callouts, end is the last contiguous blockquote line

    When include_heading=True, start points to the heading/callout line itself
    (i.e. the position of the '#' or '>' character). Useful for inserting content
    before a section.

    Matching is case-insensitive on the text.
    If heading includes # markers (e.g. "## Notes"), matches on level AND text.
    If heading starts with [! (e.g. "[!note] Status"), matches a callout title.
    If heading is plain text (e.g. "Notes"), matches on text at any level.
    Sub-headings are part of the parent section (lower-level headings don't end it).
    Headings inside fenced code blocks are ignored.

    Raises ValueError if heading/callout not found.
    """
    stripped = heading.strip()

    # Callout matching: [!type] title
    if stripped.startswith("[!"):
        return _find_callout_section(body, stripped, include_heading=include_heading)

    if stripped.startswith("#"):
        markers = stripped.split()[0]
        target_level = len(markers)
        target_text = stripped[len(markers):].strip().lower()
    else:
        target_level = None
        target_text = stripped.lower()

    fenced = _fenced_ranges(body)
    headings = []
    for m in _HEADING_RE.finditer(body):
        if any(fs <= m.start() < fe for fs, fe in fenced):
            continue
        headings.append((m, len(m.group(1)), m.group(2).strip().lower()))

    for idx, (m, level, text) in enumerate(headings):
        if text != target_text:
            continue
        if target_level is not None and level != target_level:
            continue

        if include_heading:
            start = m.start()
        else:
            start = m.end()
            if start < len(body) and body[start] == "\n":
                start += 1

        end = len(body)
        for m2, level2, _ in headings[idx + 1:]:
            if level2 <= level:
                end = m2.start()
                break

        return start, end

    raise ValueError(f"Section '{heading}' not found")


def _find_callout_section(body, target, include_heading=False):
    """Find start/end of an Obsidian callout by its [!type] title.

    The section includes all contiguous blockquote lines after the title.
    A non-blockquote line (including blank) ends the section.
    Callouts inside fenced code blocks are ignored.

    When include_heading=True, start points to the callout title line itself.
    """
    target_lower = target.lower()
    fenced = _fenced_ranges(body)
    lines = body.split("\n")
    pos = 0

    for i, line in enumerate(lines):
        if any(fs <= pos < fe for fs, fe in fenced):
            pos += len(line) + 1
            continue
        after_gt = line.lstrip()[1:].lstrip() if line.lstrip().startswith(">") else None
        if after_gt is not None and after_gt.lower().startswith(target_lower):
            content_start = pos + len(line) + 1
            if content_start > len(body):
                content_start = len(body)

            start = pos if include_heading else content_start
            end = content_start
            for j in range(i + 1, len(lines)):
                next_line = lines[j]
                if next_line.lstrip().startswith(">"):
                    end += len(next_line) + 1
                else:
                    break

            if end > len(body):
                end = len(body)
            return start, end

        pos += len(line) + 1

    raise ValueError(f"Section '{target}' not found")


# ---------------------------------------------------------------------------
# Artefact type matching
# ---------------------------------------------------------------------------


def match_artefact(artefacts, type_key):
    """Find an artefact dict matching type_key against key, type,
    or frontmatter_type.

    Accepts plural ("ideas"), singular ("idea"), full plural
    ("living/ideas"), or full singular ("living/idea").
    Returns the matched artefact dict, or None if no match found.
    """
    for a in artefacts:
        if type_key in (a["key"], a["type"], a["frontmatter_type"]):
            return a

    # Bare singular name: "idea" should match frontmatter_type "living/idea".
    # Only try this when the input has no slash (full paths already matched above).
    if "/" not in type_key:
        for a in artefacts:
            ft = a["frontmatter_type"]
            if ft.endswith("/" + type_key):
                return a

    return None


# ---------------------------------------------------------------------------
# BM25 tokenisation
# ---------------------------------------------------------------------------

def tokenise(text):
    """Lowercase, split on non-alphanumeric, strip tokens < 2 chars."""
    return [t for t in _TOKEN_RE.findall(text.lower()) if len(t) >= 2]


# ---------------------------------------------------------------------------
# Safe file writes — atomic, symlink-aware, bounds-checked
# ---------------------------------------------------------------------------

def resolve_and_check_bounds(path, bounds, *, follow_symlinks=True):
    """Resolve symlinks and verify the target is within *bounds*.

    Returns the resolved real path as a string.
    Raises ValueError if the target resolves outside bounds or if
    *follow_symlinks* is False and the path is a symlink.
    """
    target = str(path)
    if follow_symlinks:
        target = os.path.realpath(target)
    elif os.path.islink(target):
        raise ValueError(f"Refusing to follow symlink: {path}")

    real_bounds = os.path.realpath(str(bounds))
    # Append os.sep so "/home/foo" doesn't match "/home/foobar"
    if target != real_bounds and not target.startswith(real_bounds + os.sep):
        raise ValueError(
            f"Path {target} resolves outside allowed boundary {real_bounds}"
        )
    return target


def check_not_in_brain_core(path, vault_root):
    """Raise ValueError if path resolves inside .brain-core/."""
    real = os.path.realpath(os.path.join(vault_root, path) if not os.path.isabs(path) else path)
    protected = os.path.realpath(os.path.join(vault_root, ".brain-core"))
    if real == protected or real.startswith(protected + os.sep):
        raise ValueError(f"Cannot modify files inside .brain-core/: {path}")


def safe_write(path, content, *, encoding="utf-8", bounds=None,
               follow_symlinks=True, exclusive=False):
    """Atomic file write with optional symlink resolution and bounds checking.

    Writes *content* to a temporary file in the same directory, fsyncs, then
    atomically replaces the target via ``os.replace``.  Returns the resolved
    path that was actually written to.
    """
    if bounds is not None:
        target = resolve_and_check_bounds(path, bounds,
                                          follow_symlinks=follow_symlinks)
    elif follow_symlinks:
        target = os.path.realpath(str(path))
    else:
        target = str(path)
        if os.path.islink(target):
            raise ValueError(f"Refusing to follow symlink: {path}")

    if exclusive and os.path.exists(target):
        raise FileExistsError(f"File already exists: {target}")

    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)

    tmp_path = f"{target}.{os.getpid()}.tmp"
    try:
        with open(tmp_path, "w", encoding=encoding) as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, target)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    return target


def safe_write_json(path, data, *, indent=2, bounds=None,
                    follow_symlinks=True):
    """Atomic JSON write.  Serialises *data* and delegates to ``safe_write``."""
    content = json.dumps(data, indent=indent, ensure_ascii=False) + "\n"
    return safe_write(path, content, bounds=bounds,
                      follow_symlinks=follow_symlinks)
