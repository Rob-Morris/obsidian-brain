#!/usr/bin/env python3
"""
_common.py — Shared utilities for brain-core scripts.

Provides vault root discovery, version reading, filesystem scanning,
frontmatter parsing, serialisation, slug generation, and BM25 tokenisation.
All brain-core scripts import from this module rather than duplicating
these functions.
"""

import os
import re
import sys
import unicodedata
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
# Filesystem scanning (DD-016)
# ---------------------------------------------------------------------------

def is_system_dir(name):
    """Check if a directory name is infrastructure (not a living artefact).

    Convention: any folder starting with _ or . is excluded from living type
    discovery. _Temporal contains artefacts but is scanned separately — it is
    still excluded here because its children are temporal, not living.
    """
    return name.startswith("_") or name.startswith(".")


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

    # Simple YAML parser for flat fields — handles type, status, tags
    for line in fm_text.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        colon_idx = line.find(":")
        if colon_idx < 0:
            continue

        key = line[:colon_idx].strip()
        value = line[colon_idx + 1:].strip()

        if key == "tags":
            # Handle inline list: [tag1, tag2] or multi-line
            if value.startswith("["):
                inner = value.strip("[]")
                fields["tags"] = [t.strip().strip("'\"") for t in inner.split(",") if t.strip()]
            elif not value:
                # Multi-line tags follow; collect them
                fields["tags"] = []
            continue

        if not value:
            continue

        # Strip quotes
        if (value.startswith("'") and value.endswith("'")) or \
           (value.startswith('"') and value.endswith('"')):
            value = value[1:-1]

        fields[key] = value

    # Handle multi-line tags (- tag format)
    if "tags" in fields and fields["tags"] == []:
        tags = []
        in_tags = False
        for line in fm_text.split("\n"):
            stripped = line.strip()
            if stripped.startswith("tags:"):
                in_tags = True
                continue
            if in_tags:
                if stripped.startswith("- "):
                    tags.append(stripped[2:].strip().strip("'\""))
                elif stripped and not stripped.startswith("-"):
                    break
        fields["tags"] = tags

    return fields, body


# ---------------------------------------------------------------------------
# Path and wikilink utilities
# ---------------------------------------------------------------------------

def strip_md_ext(path):
    """Strip .md extension from a path, returning the wikilink stem."""
    return path[:-3] if path.endswith(".md") else path


def build_wikilink_pattern(stem):
    """Build a compiled regex matching wikilinks to the given stem.

    Matches [[stem]] and [[stem|alias]], capturing the optional alias group.
    """
    return re.compile(
        r'\[\[' + re.escape(stem) + r'(\|[^\]]*)?'r'\]\]'
    )


def replace_wikilinks_in_vault(vault_root, pattern, replacement):
    """Walk all .md files in the vault and apply a wikilink regex substitution.

    Skips system directories except _Temporal (which contains artefacts).
    replacement can be a string or a callable (as per re.sub).

    Returns the total number of substitutions made.
    """
    total = 0
    for dirpath, _dirnames, filenames in os.walk(vault_root):
        rel_dir = os.path.relpath(dirpath, vault_root)
        if rel_dir != "." and is_system_dir(os.path.basename(dirpath)):
            if not rel_dir.startswith(TEMPORAL_DIR):
                continue
        for fname in filenames:
            if not fname.endswith(".md"):
                continue
            fpath = os.path.join(dirpath, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    content = f.read()
            except OSError:
                continue
            new_content, count = pattern.subn(replacement, content)
            if count > 0:
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(new_content)
                total += count
    return total


# ---------------------------------------------------------------------------
# Slug generation
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def title_to_slug(title):
    """Convert a human-readable title to a filename slug.

    Lowercase, replace non-alphanumeric runs with hyphens, strip edges.
    Output matches the {slug} regex from check.py: [a-z0-9]+(?:-[a-z0-9]+)*
    """
    # Transliterate unicode to ASCII approximations (e.g. é → e)
    normalised = unicodedata.normalize("NFKD", title)
    ascii_only = normalised.encode("ascii", "ignore").decode("ascii")
    slug = _SLUG_RE.sub("-", ascii_only.lower()).strip("-")
    # Collapse any remaining double hyphens
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug


# ---------------------------------------------------------------------------
# Frontmatter serialisation
# ---------------------------------------------------------------------------

def serialize_frontmatter(fields, body=""):
    """Produce markdown with YAML frontmatter from a fields dict and body.

    Handles scalars and `tags` as a multi-line list (- tag).
    Round-trips with parse_frontmatter().
    """
    lines = ["---"]
    for key, value in fields.items():
        if key == "tags" and isinstance(value, list):
            if value:
                lines.append("tags:")
                for tag in value:
                    lines.append(f"  - {tag}")
            else:
                lines.append("tags: []")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    lines.append("")  # blank line after frontmatter

    fm_block = "\n".join(lines)
    if body:
        return fm_block + body
    return fm_block


# ---------------------------------------------------------------------------
# BM25 tokenisation
# ---------------------------------------------------------------------------

def tokenise(text):
    """Lowercase, split on non-alphanumeric, strip tokens < 2 chars."""
    return [t for t in _TOKEN_RE.findall(text.lower()) if len(t) >= 2]
