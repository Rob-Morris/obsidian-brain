"""Path and wikilink utilities — extraction, resolution, rewriting."""

import os
import re
from collections import namedtuple

from ._vault import is_system_dir, TEMPORAL_DIR
from ._filesystem import safe_write
from ._slugs import slug_to_title

# ---------------------------------------------------------------------------
# Wikilink pattern building and rewriting
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
                _dirnames[:] = []
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
INDEX_SKIP_DIRS = {".git", ".obsidian", ".venv", ".brain-core", "__pycache__", "_Archive"}


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
        dirnames[:] = [d for d in dirnames if d not in INDEX_SKIP_DIRS]

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


def temporal_display_name(stem):
    """Return the display-name portion after the tilde, or *None* if not temporal."""
    m = _DATED_PREFIX_RE.match(stem)
    return stem[m.end():] if m else None


def discover_temporal_prefixes(md_basenames):
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
        display = temporal_display_name(indexed_stem)
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
        temporal_prefixes = discover_temporal_prefixes(md_basenames)

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

    return Resolution("unresolvable", None, [], "none")
