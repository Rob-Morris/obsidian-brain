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
import re
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

# Optional embedding dependencies — graceful degradation when missing
try:
    import numpy as np
    from sentence_transformers import SentenceTransformer
    _HAS_EMBEDDINGS = True
except ImportError:
    _HAS_EMBEDDINGS = False


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
    """Extract title from filename stem.

    In Obsidian, the filename is the canonical title — it's what users
    link to, search for, and see in the file explorer. The H1 heading
    is display text and may omit structural info (e.g. type prefixes).
    """
    return Path(filename).stem


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OUTPUT_PATH = os.path.join("_Config", ".retrieval-index.json")
TYPE_EMBEDDINGS_REL = os.path.join("_Config", ".type-embeddings.npy")
DOC_EMBEDDINGS_REL = os.path.join("_Config", ".doc-embeddings.npy")
EMBEDDINGS_META_REL = os.path.join("_Config", ".embeddings-meta.json")
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384
INDEX_VERSION = "1.0.0"
BM25_K1 = 1.5
BM25_B = 0.75


# ---------------------------------------------------------------------------
# Type description extraction (for classification + embeddings)
# ---------------------------------------------------------------------------

_SECTION_RE = re.compile(r"^## (.+)", re.MULTILINE)


def extract_type_description(vault_root, artefact):
    """Read taxonomy file and extract one-liner + Purpose + When To Use/Trigger.

    Returns a combined description string suitable for embedding or BM25
    classification. Returns empty string if taxonomy file missing.
    """
    taxonomy_file = artefact.get("taxonomy_file")
    if not taxonomy_file:
        return ""

    abs_path = os.path.join(str(vault_root), taxonomy_file)
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError):
        return ""

    parts = []

    # One-liner: first paragraph after H1 (single line, no DOTALL)
    h1_match = re.search(r"^# .+\n\n(.+)", content, re.MULTILINE)
    if h1_match:
        parts.append(h1_match.group(1).strip())

    # Extract named sections
    for section_name in ("Purpose", "When To Use", "Trigger"):
        body = _extract_section(content, section_name)
        if body:
            parts.append(body)

    return "\n\n".join(parts)


def _extract_section(content, heading):
    """Extract the body of a ## heading section, stopping at the next ## or EOF."""
    pattern = re.compile(
        rf"^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(content)
    if match:
        return match.group(1).strip()
    return ""


# ---------------------------------------------------------------------------
# Embedding building (optional)
# ---------------------------------------------------------------------------

def build_embeddings(vault_root, router, documents):
    """Compute embeddings for type descriptions and documents.

    Returns meta dict on success, None if embedding deps unavailable.
    """
    if not _HAS_EMBEDDINGS:
        return None

    vault_str = str(vault_root)
    artefacts = [a for a in router.get("artefacts", []) if a.get("configured")]

    # Extract type descriptions
    type_texts = []
    type_meta = []
    for i, artefact in enumerate(artefacts):
        desc = extract_type_description(vault_root, artefact)
        if not desc:
            desc = artefact.get("type", artefact.get("key", ""))
        type_texts.append(desc)
        type_meta.append({
            "index": i,
            "key": artefact["key"],
            "type": artefact["type"],
            "description": desc[:200],
        })

    # Build document texts (title + body)
    doc_texts = []
    doc_meta = []
    for i, doc in enumerate(documents):
        abs_path = os.path.join(vault_str, doc["path"])
        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                text = f.read()
            _, body = parse_frontmatter(text)
        except (OSError, UnicodeDecodeError):
            body = ""
        doc_texts.append(f"{doc['title']} {body[:500]}")
        doc_meta.append({"index": i, "path": doc["path"]})

    # Encode
    model = SentenceTransformer(EMBEDDING_MODEL)

    type_embeddings = model.encode(type_texts, normalize_embeddings=True) if type_texts else np.zeros((0, EMBEDDING_DIM))
    doc_embeddings = model.encode(doc_texts, normalize_embeddings=True) if doc_texts else np.zeros((0, EMBEDDING_DIM))

    # Save
    np.save(os.path.join(vault_str, TYPE_EMBEDDINGS_REL), type_embeddings)
    np.save(os.path.join(vault_str, DOC_EMBEDDINGS_REL), doc_embeddings)

    meta = {
        "model": EMBEDDING_MODEL,
        "dim": EMBEDDING_DIM,
        "built_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "types": type_meta,
        "documents": doc_meta,
    }
    meta_path = os.path.join(vault_str, EMBEDDINGS_META_REL)
    os.makedirs(os.path.dirname(meta_path), exist_ok=True)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
        f.write("\n")

    return meta


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

            # Tokenise title + type for title boosting
            # Type tokens (e.g. "temporal/idea-log" → ["temporal", "idea", "log"])
            # ensure searching by type name surfaces matching docs
            doc_type = fields.get("type", type_info["type"])
            title_tokens = tokenise(title) + tokenise(doc_type)
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
