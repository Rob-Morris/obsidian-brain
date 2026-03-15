#!/usr/bin/env python3
"""
build_index.py — Brain-core BM25 retrieval index builder

Walks all .md files in living + temporal type folders, extracts frontmatter
and body text, computes BM25 corpus stats and per-doc term frequencies,
and writes _Config/.retrieval-index.json.

Usage:
    python3 build_index.py           # write _Config/.retrieval-index.json
    python3 build_index.py --json    # output JSON to stdout
"""

import json
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TEMPORAL_DIR = "_Temporal"
OUTPUT_PATH = os.path.join("_Config", ".retrieval-index.json")
INDEX_VERSION = "1.0.0"
BM25_K1 = 1.5
BM25_B = 0.75

# ---------------------------------------------------------------------------
# Vault root discovery (duplicated from compile_router.py for portability)
# ---------------------------------------------------------------------------

def _is_vault_root(path):
    """Check if a directory is a Brain vault root."""
    return (path / ".brain-core" / "VERSION").is_file() or (path / "Agents.md").is_file()


def find_vault_root():
    """Find a Brain vault root — checks cwd first, then walks up from script location."""
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


def is_system_dir(name):
    """Convention: any folder starting with _ or . is infrastructure."""
    return name.startswith("_") or name.startswith(".")


# ---------------------------------------------------------------------------
# Type folder discovery (matches compile_router.py patterns)
# ---------------------------------------------------------------------------

def scan_living_types(vault_root):
    """Discover living artefact type folders from root-level non-system dirs."""
    types = []
    vault_str = str(vault_root)
    for entry in sorted(os.listdir(vault_str)):
        full = os.path.join(vault_str, entry)
        if not os.path.isdir(full):
            continue
        if is_system_dir(entry):
            continue
        key = entry.lower().replace(" ", "-")
        types.append({
            "folder": entry,
            "type": "living/" + key,
            "path": entry,
        })
    return types


def scan_temporal_types(vault_root):
    """Discover temporal artefact type folders from _Temporal/ subfolders."""
    vault_str = str(vault_root)
    temporal_dir = os.path.join(vault_str, TEMPORAL_DIR)
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
            "type": "temporal/" + key,
            "path": os.path.join(TEMPORAL_DIR, entry),
        })
    return types


# ---------------------------------------------------------------------------
# Markdown file discovery
# ---------------------------------------------------------------------------

def find_md_files(vault_root, type_info):
    """Recursively find all .md files within a type folder."""
    vault_str = str(vault_root)
    type_dir = os.path.join(vault_str, type_info["path"])
    if not os.path.isdir(type_dir):
        return []

    files = []
    for dirpath, dirnames, filenames in os.walk(type_dir):
        # Skip system subdirectories
        dirnames[:] = [d for d in dirnames if not is_system_dir(d)]
        for fname in filenames:
            if fname.endswith(".md"):
                abs_path = os.path.join(dirpath, fname)
                rel_path = os.path.relpath(abs_path, vault_str)
                files.append(rel_path)
    return files


# ---------------------------------------------------------------------------
# Frontmatter + body extraction
# ---------------------------------------------------------------------------

_FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


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

        if key == "tags" or (not value and key != "tags"):
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


def extract_title(body, filename):
    """Extract title from first # heading or fallback to filename stem."""
    for line in body.split("\n"):
        stripped = line.strip()
        if stripped.startswith("# ") and not stripped.startswith("## "):
            return stripped[2:].strip()
    return Path(filename).stem


# ---------------------------------------------------------------------------
# BM25 tokenisation
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenise(text):
    """Lowercase, split on non-alphanumeric, strip tokens < 2 chars."""
    return [t for t in _TOKEN_RE.findall(text.lower()) if len(t) >= 2]


# ---------------------------------------------------------------------------
# Index building
# ---------------------------------------------------------------------------

def build_index(vault_root):
    """Build the BM25 retrieval index for the vault."""
    vault_str = str(vault_root)
    version = read_version(vault_root)

    # Discover type folders
    all_types = scan_living_types(vault_root) + scan_temporal_types(vault_root)

    # Collect all .md files with their type info
    documents = []
    for type_info in all_types:
        md_files = find_md_files(vault_root, type_info)
        for rel_path in md_files:
            abs_path = os.path.join(vault_str, rel_path)
            try:
                with open(abs_path, "r", encoding="utf-8") as f:
                    text = f.read()
            except (OSError, UnicodeDecodeError):
                continue

            fields, body = parse_frontmatter(text)
            title = extract_title(body, rel_path)

            # Get file modification time
            try:
                mtime = os.path.getmtime(abs_path)
                modified = datetime.fromtimestamp(mtime, tz=timezone.utc).astimezone().isoformat()
            except OSError:
                modified = None

            # Tokenise body for BM25
            tokens = tokenise(body)
            tf = {}
            for token in tokens:
                tf[token] = tf.get(token, 0) + 1

            documents.append({
                "path": rel_path,
                "title": title,
                "type": fields.get("type", type_info["type"]),
                "tags": fields.get("tags", []),
                "status": fields.get("status"),
                "modified": modified,
                "doc_length": len(tokens),
                "tf": tf,
            })

    # Compute corpus stats
    total_docs = len(documents)
    total_length = sum(d["doc_length"] for d in documents)
    avg_dl = total_length / total_docs if total_docs > 0 else 0.0

    # Document frequency per term
    df = {}
    for doc in documents:
        for term in doc["tf"]:
            df[term] = df.get(term, 0) + 1

    index = {
        "meta": {
            "brain_core_version": version,
            "index_version": INDEX_VERSION,
            "built_at": datetime.now(timezone.utc).astimezone().isoformat(),
            "document_count": total_docs,
            "avg_doc_length": round(avg_dl, 1),
        },
        "bm25_params": {
            "k1": BM25_K1,
            "b": BM25_B,
        },
        "corpus_stats": {
            "total_docs": total_docs,
            "avg_dl": round(avg_dl, 1),
            "df": df,
        },
        "documents": documents,
    }

    return index


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    vault_root = find_vault_root()
    index = build_index(vault_root)

    json_output = json.dumps(index, indent=2, ensure_ascii=False)

    if "--json" in sys.argv:
        print(json_output)
    else:
        output_path = os.path.join(str(vault_root), OUTPUT_PATH)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(json_output + "\n")

        doc_count = index["meta"]["document_count"]
        term_count = len(index["corpus_stats"]["df"])
        print(
            f"Built retrieval index: {doc_count} documents, "
            f"{term_count} unique terms → {OUTPUT_PATH}",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
