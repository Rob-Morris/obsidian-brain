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
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from _common import (
    find_vault_root,
    is_system_dir,
    parse_frontmatter,
    read_version,
    scan_living_types,
    scan_temporal_types,
    tokenise,
)


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


def extract_title(body, filename):
    """Extract title from first # heading or fallback to filename stem."""
    for line in body.split("\n"):
        stripped = line.strip()
        if stripped.startswith("# ") and not stripped.startswith("## "):
            return stripped[2:].strip()
    return Path(filename).stem


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OUTPUT_PATH = os.path.join("_Config", ".retrieval-index.json")
INDEX_VERSION = "1.0.0"
BM25_K1 = 1.5
BM25_B = 0.75


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

            # Tokenise title separately for title boosting
            title_tokens = tokenise(title)
            title_tf = {}
            for token in title_tokens:
                title_tf[token] = title_tf.get(token, 0) + 1

            documents.append({
                "path": rel_path,
                "title": title,
                "type": fields.get("type", type_info["type"]),
                "tags": fields.get("tags", []),
                "status": fields.get("status"),
                "modified": modified,
                "doc_length": len(tokens),
                "tf": tf,
                "title_tf": title_tf,
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
