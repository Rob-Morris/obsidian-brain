#!/usr/bin/env python3
"""
process.py — Content classification, duplicate resolution, and ingestion

Provides three operations for the brain_process MCP tool:
  classify   — Determine the best artefact type for content.
  resolve    — Check if content should create or update an existing artefact.
  ingest     — Full pipeline: classify → resolve → create/update.
"""

import math
import os

from _common import title_to_filename, title_to_slug, tokenise

import build_index
import create as create_mod
import edit as edit_mod
import search_index


# ---------------------------------------------------------------------------
# Title inference
# ---------------------------------------------------------------------------

def infer_title(content):
    """Extract title from content: first H1 heading, else first non-empty line.

    Truncates to 60 characters.
    """
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()[:60]
    for line in content.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:60]
    return "Untitled"


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def classify_content(
    router, vault_root, content,
    index=None,
    type_embeddings=None, type_embeddings_meta=None,
    mode="auto",
):
    """Classify content against artefact types.

    Returns dict with {mode, type, key, confidence, reasoning, alternatives}
    or context_assembly payload when no scoring is possible.
    """
    if mode == "context_assembly":
        return _classify_context_assembly(router, vault_root)

    if mode == "embedding" or (mode == "auto" and type_embeddings is not None):
        result = _classify_embedding(
            content, type_embeddings, type_embeddings_meta,
        )
        if result is not None:
            return result

    if mode == "bm25_only" or (mode == "auto" and index is not None):
        result = _classify_bm25(router, vault_root, content, index)
        if result is not None:
            return result

    return _classify_context_assembly(router, vault_root)


def _classify_embedding(content, type_embeddings, type_embeddings_meta):
    """Classify via cosine similarity against type embeddings."""
    if type_embeddings is None or type_embeddings_meta is None:
        return None

    try:
        import numpy as np
        from sentence_transformers import SentenceTransformer
    except ImportError:
        return None

    model = SentenceTransformer(build_index.EMBEDDING_MODEL)
    query_vec = model.encode([content], normalize_embeddings=True)[0]

    # Cosine similarity (already L2-normalised)
    similarities = type_embeddings @ query_vec

    ranked = sorted(
        enumerate(similarities), key=lambda x: x[1], reverse=True,
    )

    meta_entries = type_embeddings_meta.get("types", [])
    if not ranked or not meta_entries:
        return None

    best_idx, best_score = ranked[0]
    best_entry = meta_entries[best_idx]

    alternatives = []
    for idx, score in ranked[1:6]:
        entry = meta_entries[idx]
        alternatives.append({
            "type": entry["type"],
            "key": entry["key"],
            "confidence": round(float(score) * 100, 1),
        })

    return {
        "mode": "embedding",
        "type": best_entry["type"],
        "key": best_entry["key"],
        "confidence": round(float(best_score) * 100, 1),
        "reasoning": f"Cosine similarity {best_score:.3f} against type description embedding",
        "alternatives": alternatives,
    }


def _classify_bm25(router, vault_root, content, index):
    """Classify via IDF-weighted token overlap against type descriptions."""
    if index is None:
        return None

    content_tokens = tokenise(content)
    if not content_tokens:
        return None

    artefacts = [a for a in router.get("artefacts", []) if a.get("configured")]
    if not artefacts:
        return None

    corpus_stats = index.get("corpus_stats", {})
    total_docs = corpus_stats.get("total_docs", 1)
    df = corpus_stats.get("df", {})

    scored = []
    for artefact in artefacts:
        desc = build_index.extract_type_description(vault_root, artefact)
        if not desc:
            continue

        desc_tokens = tokenise(desc)
        desc_token_set = set(desc_tokens)

        score = 0.0
        for token in content_tokens:
            if token in desc_token_set:
                token_df = df.get(token, 0)
                idf = math.log((total_docs - token_df + 0.5) / (token_df + 0.5) + 1)
                score += idf

        scored.append({
            "type": artefact["type"],
            "key": artefact["key"],
            "score": score,
        })

    if not scored:
        return None

    scored.sort(key=lambda x: x["score"], reverse=True)

    # Normalise scores to 0-100 confidence range
    max_score = scored[0]["score"]
    if max_score <= 0:
        return None

    best = scored[0]
    confidence = min(round(best["score"] / max_score * 100, 1), 100.0)

    alternatives = []
    for entry in scored[1:6]:
        alt_conf = min(round(entry["score"] / max_score * 100, 1), 100.0)
        alternatives.append({
            "type": entry["type"],
            "key": entry["key"],
            "confidence": alt_conf,
        })

    return {
        "mode": "bm25_only",
        "type": best["type"],
        "key": best["key"],
        "confidence": confidence,
        "reasoning": f"BM25 IDF-weighted token overlap score {best['score']:.2f}",
        "alternatives": alternatives,
    }


def _classify_context_assembly(router, vault_root):
    """Assemble type descriptions for agent LLM classification."""
    artefacts = [a for a in router.get("artefacts", []) if a.get("configured")]
    type_descriptions = []

    for artefact in artefacts:
        desc = build_index.extract_type_description(vault_root, artefact)
        if desc:
            type_descriptions.append({
                "type": artefact["type"],
                "key": artefact["key"],
                "description": desc,
            })

    return {
        "mode": "context_assembly",
        "type_descriptions": type_descriptions,
        "instruction": (
            "Use the type descriptions above to classify the content. "
            "Return the best matching type key and your reasoning."
        ),
    }


# ---------------------------------------------------------------------------
# Duplicate resolution
# ---------------------------------------------------------------------------

def resolve_content(
    router, vault_root, type_key, title, content="",
    index=None,
    doc_embeddings=None, doc_embeddings_meta=None,
):
    """Determine if content should create a new artefact or update existing.

    Returns dict with {action, type, key, title, target_path, candidates, reasoning}.
    """
    # Validate type
    try:
        artefact = create_mod.resolve_type(router, type_key)
    except ValueError as e:
        return {"action": "error", "reasoning": str(e)}

    resolved_type = artefact["type"]
    resolved_key = artefact["key"]

    # Step 1: Filename match (generous + legacy slug)
    generous_name = title_to_filename(title)
    legacy_slug = title_to_slug(title)
    type_path = artefact["path"]
    abs_type_dir = os.path.join(str(vault_root), type_path)

    filename_match = _find_filename_match(abs_type_dir, generous_name, legacy_slug)
    if filename_match:
        rel_path = os.path.relpath(filename_match, str(vault_root))
        return {
            "action": "update",
            "type": resolved_type,
            "key": resolved_key,
            "title": title,
            "target_path": rel_path,
            "candidates": [rel_path],
            "reasoning": f"Filename match: {os.path.basename(filename_match)}",
        }

    # Step 2: BM25 search for same-type matches
    best_candidate = None
    best_score = 0.0
    candidates = []

    if index is not None:
        query = title
        if content:
            query = title + " " + content[:200]
        results = search_index.search(
            index, query, str(vault_root),
            type_filter=resolved_type, top_k=5,
        )
        for r in results:
            candidates.append(r["path"])
            if r["score"] > best_score:
                best_score = r["score"]
                best_candidate = r

    # Step 3: Embedding search for same-type docs
    if doc_embeddings is not None and doc_embeddings_meta is not None:
        emb_candidates = _embedding_search(
            title + " " + content[:200] if content else title,
            resolved_type, doc_embeddings, doc_embeddings_meta,
        )
        for ec in emb_candidates:
            if ec["path"] not in candidates:
                candidates.append(ec["path"])
            if ec["score"] > best_score:
                best_score = ec["score"]
                best_candidate = ec

    # Step 4: Decision
    if best_candidate and best_score > 0.90:
        target = best_candidate["path"]
        return {
            "action": "update",
            "type": resolved_type,
            "key": resolved_key,
            "title": title,
            "target_path": target,
            "candidates": candidates,
            "reasoning": f"High similarity match (score {best_score:.2f}): {target}",
        }

    if best_candidate and best_score >= 0.75:
        return {
            "action": "ambiguous",
            "type": resolved_type,
            "key": resolved_key,
            "title": title,
            "target_path": None,
            "candidates": candidates,
            "reasoning": f"Possible matches found (best score {best_score:.2f}) but below confidence threshold",
        }

    return {
        "action": "create",
        "type": resolved_type,
        "key": resolved_key,
        "title": title,
        "target_path": None,
        "candidates": candidates,
        "reasoning": "No existing artefact matches this content",
    }


def _find_filename_match(type_dir, generous_name, legacy_slug):
    """Find a file matching the generous filename or legacy slug (case-insensitive)."""
    if not os.path.isdir(type_dir):
        return None

    generous_lower = generous_name.lower()
    slug_lower = legacy_slug.lower()

    for entry in os.scandir(type_dir):
        if not entry.name.endswith(".md"):
            continue
        stem = entry.name[:-3].lower()
        if stem == generous_lower or stem == slug_lower:
            return entry.path

    return None


def _embedding_search(query, type_filter, doc_embeddings, doc_embeddings_meta):
    """Search for similar documents via embeddings, filtered by type."""
    try:
        import numpy as np
        from sentence_transformers import SentenceTransformer
    except ImportError:
        return []

    model = SentenceTransformer(build_index.EMBEDDING_MODEL)
    query_vec = model.encode([query], normalize_embeddings=True)[0]

    similarities = doc_embeddings @ query_vec

    doc_entries = doc_embeddings_meta.get("documents", [])
    results = []
    for i, score in enumerate(similarities):
        if i < len(doc_entries):
            entry = doc_entries[i]
            if type_filter and entry.get("type") != type_filter:
                continue
            results.append({
                "path": entry["path"],
                "score": float(score),
            })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:5]


# ---------------------------------------------------------------------------
# Ingestion pipeline
# ---------------------------------------------------------------------------

def ingest_content(
    router, vault_root, content,
    title=None, type_hint=None,
    index=None,
    type_embeddings=None, type_embeddings_meta=None,
    doc_embeddings=None, doc_embeddings_meta=None,
):
    """Full pipeline: classify → infer title → resolve → act.

    Returns dict with {action_taken, path, type, title, classification,
    resolution, needs_decision, message}.
    """
    vault_str = str(vault_root)

    # Step 1: Classify (or use hint)
    classification = None
    if type_hint:
        try:
            artefact = create_mod.resolve_type(router, type_hint)
            type_key = artefact["key"]
        except ValueError as e:
            return {
                "action_taken": "error",
                "path": None,
                "type": type_hint,
                "title": title,
                "classification": None,
                "resolution": None,
                "needs_decision": False,
                "message": str(e),
            }
    else:
        classification = classify_content(
            router, vault_str, content,
            index=index,
            type_embeddings=type_embeddings,
            type_embeddings_meta=type_embeddings_meta,
        )
        if classification.get("mode") == "context_assembly":
            return {
                "action_taken": "needs_classification",
                "path": None,
                "type": None,
                "title": title,
                "classification": classification,
                "resolution": None,
                "needs_decision": True,
                "message": "Cannot auto-classify — review type descriptions and choose a type",
            }
        type_key = classification["key"]

    # Step 2: Infer title
    if not title:
        title = infer_title(content)

    # Step 3: Resolve
    resolution = resolve_content(
        router, vault_str, type_key, title, content=content,
        index=index,
        doc_embeddings=doc_embeddings,
        doc_embeddings_meta=doc_embeddings_meta,
    )

    if resolution.get("action") == "error":
        return {
            "action_taken": "error",
            "path": None,
            "type": type_key,
            "title": title,
            "classification": classification,
            "resolution": resolution,
            "needs_decision": False,
            "message": resolution["reasoning"],
        }

    if resolution["action"] == "ambiguous":
        return {
            "action_taken": "ambiguous",
            "path": None,
            "type": resolution["type"],
            "title": title,
            "classification": classification,
            "resolution": resolution,
            "needs_decision": True,
            "message": "Ambiguous match — review candidates and decide",
        }

    # Step 4: Act
    if resolution["action"] == "create":
        result = create_mod.create_artefact(
            vault_str, router, type_key, title, body=content,
        )
        return {
            "action_taken": "created",
            "path": result["path"],
            "type": result["type"],
            "title": result["title"],
            "classification": classification,
            "resolution": resolution,
            "needs_decision": False,
            "message": f"Created {result['path']}",
        }

    if resolution["action"] == "update":
        target = resolution["target_path"]
        edit_mod.append_to_artefact(
            vault_str, router, target, content,
        )
        return {
            "action_taken": "updated",
            "path": target,
            "type": resolution["type"],
            "title": title,
            "classification": classification,
            "resolution": resolution,
            "needs_decision": False,
            "message": f"Updated {target}",
        }

    # Shouldn't reach here, but handle gracefully
    return {
        "action_taken": "error",
        "path": None,
        "type": type_key,
        "title": title,
        "classification": classification,
        "resolution": resolution,
        "needs_decision": False,
        "message": f"Unexpected resolution action: {resolution.get('action')}",
    }
