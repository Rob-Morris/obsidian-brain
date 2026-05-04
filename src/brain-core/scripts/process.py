#!/usr/bin/env python3
"""
process.py — Content classification, duplicate resolution, and ingestion

Provides three operations for the brain_process MCP tool:
  classify   — Determine the best artefact type for content.
  resolve    — Check if content should create or update an existing artefact.
  ingest     — Full pipeline: classify -> resolve -> create/update.
"""

import math
import os
from collections import Counter

from _common import (
    iter_artefact_paths,
    temporal_display_name,
    title_to_filename,
    title_to_slug,
    tokenise,
)

import build_index
import create as create_mod
import edit as edit_mod
import _semantic.runtime as _semantic
import search_index


def infer_title(content):
    """Extract title from content: first H1 heading, else first non-empty line.

    Truncates to 60 characters.
    """
    lines = content.splitlines()
    first_nonempty = None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()[:60]
        if first_nonempty is None and stripped:
            first_nonempty = stripped[:60]
    return first_nonempty or "Untitled"


def classify_content(
    router,
    vault_root,
    content,
    index=None,
    type_embeddings=None,
    type_embeddings_meta=None,
    mode="auto",
    query_encoder=None,
):
    """Classify content against artefact types."""
    if mode == "context_assembly":
        return _classify_context_assembly(router, vault_root)

    if mode == "embedding" or (mode == "auto" and type_embeddings is not None):
        result = _classify_embedding(
            content,
            type_embeddings,
            type_embeddings_meta,
            query_encoder=query_encoder,
        )
        if result is not None:
            return result

    if mode == "bm25_only" or (mode == "auto" and index is not None):
        result = _classify_bm25(router, vault_root, content, index)
        if result is not None:
            return result

    return _classify_context_assembly(router, vault_root)


def _classify_embedding(
    content,
    type_embeddings,
    type_embeddings_meta,
    *,
    query_encoder=None,
):
    """Classify via cosine similarity against type embeddings."""
    if type_embeddings is None or type_embeddings_meta is None:
        return None

    try:
        query_vec = _semantic.encode_query(
            content,
            query_encoder=query_encoder,
        )
    except ImportError:
        return None

    ranked = _semantic.rank_against(
        query_vec,
        type_embeddings,
        type_embeddings_meta.get("types", []),
        top_k=6,
    )
    if not ranked:
        return None

    best = ranked[0]
    alternatives = [
        {
            "type": entry["type"],
            "key": entry["key"],
            "confidence": round(entry["score"] * 100, 1),
        }
        for entry in ranked[1:]
    ]

    return {
        "mode": "embedding",
        "type": best["type"],
        "key": best["key"],
        "confidence": round(best["score"] * 100, 1),
        "reasoning": f"Cosine similarity {best['score']:.3f} against type description embedding",
        "alternatives": alternatives,
    }


def _classify_bm25(router, vault_root, content, index):
    """Classify via IDF-weighted token overlap against type descriptions."""
    if index is None:
        return None

    content_tokens = tokenise(content)
    if not content_tokens:
        return None
    content_token_counts = Counter(content_tokens)

    artefacts = [artefact for artefact in router.get("artefacts", []) if artefact.get("configured")]
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
        for token, count in content_token_counts.items():
            if token in desc_token_set:
                token_df = df.get(token, 0)
                idf = math.log((total_docs - token_df + 0.5) / (token_df + 0.5) + 1)
                score += idf * count

        scored.append({
            "type": artefact["type"],
            "key": artefact["key"],
            "score": score,
        })

    if not scored:
        return None

    scored.sort(key=lambda entry: entry["score"], reverse=True)
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
    artefacts = [artefact for artefact in router.get("artefacts", []) if artefact.get("configured")]
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


def resolve_content(
    router,
    vault_root,
    type_key,
    title,
    content="",
    index=None,
    doc_embeddings=None,
    doc_embeddings_meta=None,
    query_encoder=None,
):
    """Determine if content should create a new artefact or update existing."""
    try:
        artefact = create_mod.resolve_type(router, type_key)
    except ValueError as e:
        return {"action": "error", "reasoning": str(e)}

    resolved_type = artefact["frontmatter_type"]
    resolved_key = artefact["key"]

    generous_name = title_to_filename(title)
    legacy_slug = title_to_slug(title)
    type_path = artefact["path"]
    abs_type_dir = os.path.join(str(vault_root), type_path)

    filename_match = _find_filename_match(vault_root, artefact, generous_name, legacy_slug)
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

    best_candidate = None
    best_score = 0.0
    candidates = []

    if index is not None:
        query = title
        if content:
            query = title + " " + content[:200]
        results = search_index.search(
            index,
            query,
            str(vault_root),
            type_filter=resolved_type,
            top_k=5,
        )
        for result in results:
            candidates.append(result["path"])
            if result["score"] > best_score:
                best_score = result["score"]
                best_candidate = result

    if doc_embeddings is not None and doc_embeddings_meta is not None:
        emb_candidates = _embedding_search(
            title + " " + content[:200] if content else title,
            resolved_type,
            doc_embeddings,
            doc_embeddings_meta,
            query_encoder=query_encoder,
        )
        for candidate in emb_candidates:
            if candidate["path"] not in candidates:
                candidates.append(candidate["path"])
            if candidate["score"] > best_score:
                best_score = candidate["score"]
                best_candidate = candidate

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
            "reasoning": (
                f"Possible matches found (best score {best_score:.2f}) "
                "but below confidence threshold"
            ),
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


def _find_filename_match(vault_root, artefact, generous_name, legacy_slug):
    """Find a same-type file matching the modern stem, legacy slug, or temporal display name."""
    type_dir = os.path.join(str(vault_root), artefact["path"])
    if not os.path.isdir(type_dir):
        return None

    generous_lower = generous_name.lower()
    slug_lower = legacy_slug.lower()

    for rel_path in iter_artefact_paths(vault_root, artefact, include_status_folders=True):
        stem = os.path.splitext(os.path.basename(rel_path))[0]
        candidates = {stem.lower()}
        display_name = temporal_display_name(stem)
        if display_name is not None:
            candidates.add(display_name.lower())
        if generous_lower in candidates or slug_lower in candidates:
            return os.path.join(str(vault_root), rel_path)

    return None


def _embedding_search(
    query,
    type_filter,
    doc_embeddings,
    doc_embeddings_meta,
    *,
    query_encoder=None,
):
    """Search for similar documents via embeddings, filtered by type."""
    try:
        query_vec = _semantic.encode_query(
            query,
            query_encoder=query_encoder,
        )
    except ImportError:
        return []
    filter_fn = (
        (lambda entry: entry.get("type") == type_filter) if type_filter else None
    )
    ranked = _semantic.rank_against(
        query_vec,
        doc_embeddings,
        doc_embeddings_meta.get("documents", []),
        filter_fn=filter_fn,
        top_k=5,
    )
    return [{"path": entry["path"], "score": entry["score"]} for entry in ranked]


def ingest_content(
    router,
    vault_root,
    content,
    title=None,
    type_hint=None,
    index=None,
    type_embeddings=None,
    type_embeddings_meta=None,
    doc_embeddings=None,
    doc_embeddings_meta=None,
    query_encoder=None,
):
    """Full pipeline: classify -> infer title -> resolve -> act."""
    vault_str = str(vault_root)

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
            router,
            vault_str,
            content,
            index=index,
            type_embeddings=type_embeddings,
            type_embeddings_meta=type_embeddings_meta,
            query_encoder=query_encoder,
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

    if not title:
        title = infer_title(content)

    resolution = resolve_content(
        router,
        vault_str,
        type_key,
        title,
        content=content,
        index=index,
        doc_embeddings=doc_embeddings,
        doc_embeddings_meta=doc_embeddings_meta,
        query_encoder=query_encoder,
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

    if resolution["action"] == "create":
        result = create_mod.create_artefact(vault_str, router, type_key, title, body=content)
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
            vault_str,
            router,
            target,
            content,
            target=":body",
            scope="section",
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
