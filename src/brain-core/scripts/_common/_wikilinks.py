"""Path and wikilink utilities — extraction, resolution, rewriting."""

import os
import re
from collections import namedtuple

from ._vault import is_system_dir, TEMPORAL_DIR
from ._filesystem import safe_write
from ._markdown import in_any_range, literal_ranges
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


def replace_wikilinks_in_text(text, pattern, replacement):
    """Apply *pattern* to *text*, rewriting only matches outside literal regions.

    Honours :func:`literal_ranges` (fences, inline code, HTML comments, math
    blocks, raw HTML). Literal wikilinks in those contexts are preserved
    verbatim — callers (rename, fix-links, edit) never mutate documentation
    examples. *replacement* may be a string or a callable (as per
    :func:`re.sub`).

    Returns ``(new_text, count)``.
    """
    skip = None
    count = 0
    out = []
    cursor = 0
    for m in pattern.finditer(text):
        if skip is None:
            skip = literal_ranges(text)
        start = m.start()
        if in_any_range(start, skip):
            continue
        out.append(text[cursor:start])
        out.append(replacement(m) if callable(replacement) else m.expand(replacement))
        cursor = m.end()
        count += 1
    if count == 0:
        return text, 0
    out.append(text[cursor:])
    return "".join(out), count


def replace_wikilinks_in_vault(vault_root, pattern, replacement):
    """Walk all .md files in the vault and apply a wikilink regex substitution.

    Skips system directories except _Temporal (which contains artefacts).
    replacement can be a string or a callable (as per re.sub). Substitutions
    only apply outside literal regions (code, comments, math, raw HTML) via
    :func:`replace_wikilinks_in_text`.

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
        new_content, count = replace_wikilinks_in_text(content, pattern, replacement)
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


def _iter_indexed_vault_files(vault_root):
    """Yield ``(rel_path, fname)`` for files included in file-index scans."""
    for dirpath, dirnames, filenames in os.walk(vault_root):
        dirnames[:] = [d for d in dirnames if d not in INDEX_SKIP_DIRS]
        for fname in filenames:
            yield os.path.relpath(os.path.join(dirpath, fname), vault_root), fname


def extract_wikilinks(text, literals="exclude"):
    """Extract all wikilinks from markdown text.

    Returns a list of dicts with keys:
        stem   — link target (without anchor or alias)
        anchor — heading/block ref including ``#``, or None
        alias  — display text (without leading ``|``), or None
        is_embed — True for ``![[…]]`` embeds
        start  — character offset of the match

    Skips same-file anchors (``[[#heading]]``) and template placeholders
    (``[[{{…}}]]``).

    *literals* controls how wikilinks inside literal-text contexts (fenced
    code, inline code, HTML comments, ``$$`` math blocks, raw HTML blocks)
    are treated:

    - ``"exclude"`` (default) — drop wikilinks inside literal regions; return
      only live links
    - ``"include"`` — return every match regardless of context
    - ``"only"`` — return only wikilinks inside literal regions
    """
    if literals not in ("exclude", "include", "only"):
        raise ValueError(
            f"literals must be 'exclude', 'include', or 'only'; got {literals!r}"
        )

    if literals == "include":
        skip = None
    else:
        skip = literal_ranges(text)

    results = []
    for m in _WIKILINK_EXTRACT_RE.finditer(text):
        if skip is not None:
            in_literal = in_any_range(m.start(), skip)
            if literals == "exclude" and in_literal:
                continue
            if literals == "only" and not in_literal:
                continue
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

    for rel_path, fname in _iter_indexed_vault_files(vault_root):
        basename_lower = fname.lower()
        all_basenames[basename_lower].append(rel_path)

        if fname.endswith(".md"):
            stem = os.path.splitext(fname)[0].lower()
            md_basenames[stem].append(rel_path)
            md_relpaths.add(strip_md_ext(rel_path).lower())

    return {
        "md_basenames": dict(md_basenames),
        "all_basenames": dict(all_basenames),
        "all_basenames_complete": True,
        "md_relpaths": md_relpaths,
    }


def build_vault_basename_index(vault_root):
    """Build only the basename lookup needed for bare asset resolution."""
    from collections import defaultdict

    all_basenames = defaultdict(list)
    for rel_path, fname in _iter_indexed_vault_files(vault_root):
        all_basenames[fname.lower()].append(rel_path)
    return dict(all_basenames)


def file_index_from_documents(documents, vault_root):
    """Build a wikilink file index from a documents list (no vault walk).

    Mirrors the shape of build_vault_file_index but only the markdown-derived
    lookups are complete. Non-markdown basenames are not present in the BM25
    retrieval index, so callers must treat ``all_basenames`` as partial.

    Args:
        documents: list of doc dicts with a "path" key (vault-relative .md path).
        vault_root: vault root path (kept for API symmetry; not used here).

    Returns:
        dict with md_basenames, partial all_basenames, md_relpaths.
    """
    from collections import defaultdict

    _ = vault_root  # unused; kept for API symmetry with build_vault_file_index

    md_basenames = defaultdict(list)
    md_relpaths = set()

    for doc in documents:
        rel_path = doc["path"]
        fname = os.path.basename(rel_path)
        stem = os.path.splitext(fname)[0].lower()
        md_basenames[stem].append(rel_path)
        md_relpaths.add(strip_md_ext(rel_path).lower())

    return {
        "md_basenames": dict(md_basenames),
        "all_basenames": {},
        "all_basenames_complete": False,
        "md_relpaths": md_relpaths,
    }


def clone_file_index(file_index):
    """Return a shallow-cloned file index safe to mutate per request."""
    return {
        "md_basenames": {
            stem: list(paths)
            for stem, paths in (file_index.get("md_basenames") or {}).items()
        },
        "all_basenames": {
            basename: list(paths)
            for basename, paths in (file_index.get("all_basenames") or {}).items()
        },
        "all_basenames_complete": file_index.get("all_basenames_complete", True),
        "md_relpaths": set(file_index.get("md_relpaths") or ()),
    }


def _drop_file_index_path(mapping, key, rel_path):
    """Remove *rel_path* from a file-index lookup bucket."""
    matches = mapping.get(key)
    if not matches:
        return
    kept = [item for item in matches if item != rel_path]
    if kept:
        mapping[key] = kept
    else:
        del mapping[key]


def remove_file_index_rel_path(file_index, rel_path):
    """Remove a markdown path from a mutable file index."""
    stem = strip_md_ext(os.path.basename(rel_path)).lower()
    _drop_file_index_path(file_index["md_basenames"], stem, rel_path)
    basename = os.path.basename(rel_path).lower()
    _drop_file_index_path(file_index["all_basenames"], basename, rel_path)
    file_index["md_relpaths"].discard(strip_md_ext(rel_path).lower())


def add_file_index_rel_path(file_index, rel_path):
    """Add a markdown path to a mutable file index."""
    stem = strip_md_ext(os.path.basename(rel_path)).lower()
    md_matches = file_index["md_basenames"].setdefault(stem, [])
    if rel_path not in md_matches:
        md_matches.append(rel_path)

    basename = os.path.basename(rel_path).lower()
    basename_matches = file_index["all_basenames"].setdefault(basename, [])
    if rel_path not in basename_matches:
        basename_matches.append(rel_path)

    file_index["md_relpaths"].add(strip_md_ext(rel_path).lower())


def overlay_file_index_result(file_index, result):
    """Return a cloned file index updated for the current mutation result."""
    cloned = clone_file_index(file_index)
    path = result.get("path")
    if not path:
        return cloned
    resolved_path = result.get("resolved_path")
    if resolved_path and resolved_path != path:
        remove_file_index_rel_path(cloned, resolved_path)
    add_file_index_rel_path(cloned, path)
    return cloned


def ensure_complete_file_index_basenames(file_index, vault_root):
    """Populate ``all_basenames`` in-place when a file index is partial.

    The BM25-derived index used by MCP writes is complete for markdown stems
    but not for asset basenames. When a bare asset embed needs basename
    resolution, fill that cache once and let later scans reuse it.
    """
    if file_index.get("all_basenames_complete", True):
        return file_index["all_basenames"]

    file_index["all_basenames"] = build_vault_basename_index(vault_root)
    file_index["all_basenames_complete"] = True
    return file_index["all_basenames"]


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


# File extensions that indicate a non-markdown link target (embeds, assets).
_ASSET_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp",
    ".pdf", ".mp3", ".mp4", ".wav", ".webm", ".mov",
    ".csv", ".json", ".xml", ".html", ".css", ".js",
}


def _has_file_extension(stem):
    """Return True if the stem ends with a known asset extension."""
    _, ext = os.path.splitext(stem)
    return ext.lower() in _ASSET_EXTENSIONS


def check_wikilinks_in_file(vault_root, rel_path, file_index=None, temporal_prefixes=None):
    """Check wikilinks in a single file against the vault file index.

    If file_index is not provided, builds one via build_vault_file_index().
    If temporal_prefixes is not provided, discovers them via discover_temporal_prefixes().
    Both are built from the same vault walk, so callers with pre-built values
    should pass both to avoid redundant walks. Building the index for a single
    call requires an os.walk of the vault — this is acceptable (milliseconds
    for typical vaults) but callers inside a loop should build once and pass in.

    Returns a list of findings, each a dict with:
        stem         — the wikilink target stem
        status       — "broken", "ambiguous", or "resolvable"
        resolved_to  — for resolvable findings, the corrected stem; else None
        strategy     — the resolution strategy name (or "none" / "ambiguous")
        candidates   — for ambiguous findings, list of matching paths; else []

    Clean (resolved) links are not returned. Only problem findings are reported.
    """
    if file_index is None:
        file_index = build_vault_file_index(vault_root)
    if temporal_prefixes is None:
        temporal_prefixes = discover_temporal_prefixes(file_index["md_basenames"])

    md_basenames = file_index["md_basenames"]
    all_basenames = file_index["all_basenames"]
    all_basenames_complete = file_index.get("all_basenames_complete", True)
    md_relpaths = file_index["md_relpaths"]

    fpath = os.path.join(vault_root, rel_path)
    try:
        with open(fpath, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return []

    findings = []
    for link in extract_wikilinks(text):
        stem = link["stem"]
        is_embed = link["is_embed"]
        resolved = False
        ambiguous = False
        ambiguous_matches = []

        if is_embed or _has_file_extension(stem):
            basename_key = os.path.basename(stem).lower()
            if basename_key in all_basenames:
                resolved = True
            elif "/" in stem and os.path.exists(os.path.join(vault_root, stem)):
                resolved = True
            elif not all_basenames_complete:
                all_basenames = ensure_complete_file_index_basenames(
                    file_index, vault_root
                )
                all_basenames_complete = True
                if basename_key in all_basenames:
                    resolved = True
        elif "/" in stem:
            stem_lower = strip_md_ext(stem).lower()
            if stem_lower in md_relpaths:
                resolved = True
            else:
                basename_key = os.path.splitext(os.path.basename(stem))[0].lower()
                if basename_key in md_basenames:
                    resolved = True
        else:
            stem_lower = stem.lower()
            matches = md_basenames.get(stem_lower, [])
            if matches:
                resolved = True
                if len(matches) > 1:
                    ambiguous = True
                    ambiguous_matches = matches

        if resolved and ambiguous:
            findings.append({
                "stem": stem,
                "status": "ambiguous",
                "resolved_to": None,
                "strategy": "ambiguous",
                "candidates": ambiguous_matches,
            })
        elif not resolved:
            resolution = resolve_broken_link(stem, file_index, temporal_prefixes)
            if resolution.status == "resolved":
                findings.append({
                    "stem": stem,
                    "status": "resolvable",
                    "resolved_to": resolution.resolved_to,
                    "strategy": resolution.strategy,
                    "candidates": resolution.candidates,
                })
            elif resolution.status == "ambiguous":
                findings.append({
                    "stem": stem,
                    "status": "ambiguous",
                    "resolved_to": None,
                    "strategy": resolution.strategy,
                    "candidates": resolution.candidates,
                })
            else:
                findings.append({
                    "stem": stem,
                    "status": "broken",
                    "resolved_to": None,
                    "strategy": "none",
                    "candidates": [],
                })

    return findings
