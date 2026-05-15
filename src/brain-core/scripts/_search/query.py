"""Query execution and mode policy for lexical, semantic, and hybrid search."""

from __future__ import annotations

import json
import math
import os
import re

from _common import FM_RE, load_compiled_router

from ._lazy import LazyModuleProxy
from .index import OUTPUT_PATH
from .lexical import LEXICAL_ANCHOR_RE, tokenise


DEFAULT_TOP_K = 10
SNIPPET_LENGTH = 200
TITLE_BOOST = 3.0
RRF_K = 60
RRF_LEXICAL_WEIGHT = 1.0
RRF_SEMANTIC_WEIGHT = 1.0
RRF_CANDIDATE_MULTIPLIER = 3
SEMANTIC_CHAMPION_MARGIN = 0.05
SEMANTIC_CHAMPION_BONUS = 0.002
# Preserve strong exact-title lexical wins when the top lexical hit clearly
# outperforms the next lexical candidate and the query already contains that
# title phrase.
LEXICAL_TITLE_CHAMPION_BONUS = 0.002
LEXICAL_TITLE_CHAMPION_MIN_MARGIN = 1.2
LEXICAL_TITLE_CHAMPION_MIN_TOKENS = 2
# Strip the Brain product namespace before title-overlap heuristics so
# "Brain X" titles do not win merely for sharing the product prefix.
BRAIN_PRODUCT_NAMESPACE_PREFIX_TOKENS = frozenset({"brain"})
SEMANTIC_RESCUE_MIN_QUERY_TOKENS = 5
SEMANTIC_RESCUE_MAX_LEXICAL_TITLE_OVERLAP = 1
SEMANTIC_RESCUE_TOP_OVERLAP_WINDOW = 3
# Give clearly better semantic leaders a small lift when lexical and semantic
# disagree on long, low-overlap queries.
SEMANTIC_RESCUE_BONUS = 0.015
SEARCH_MODES = {"lexical", "semantic", "hybrid"}


_semantic_config = LazyModuleProxy("_semantic.config")
_semantic_model = LazyModuleProxy("_semantic.model")
_semantic = LazyModuleProxy("_semantic.runtime")


class SearchModeUnavailableError(ValueError):
    """Raised when the caller requests a retrieval mode that is unavailable."""


class IndexNotFoundError(FileNotFoundError):
    """Raised when the persisted lexical retrieval index is missing."""


def default_search_mode(
    vault_root,
    *,
    config=None,
    doc_embeddings=None,
    embeddings_meta=None,
):
    """Return the best-effort default search mode."""
    if (
        _semantic_config.semantic_retrieval_enabled(vault_root, config=config)
        and _semantic.semantic_engine_available(
            vault_root,
            config=config,
            doc_embeddings=doc_embeddings,
            embeddings_meta=embeddings_meta,
            skip_sidecar_check=True,
        )
    ):
        return "hybrid"
    return "lexical"


def resolve_search_mode(
    vault_root,
    mode,
    *,
    config=None,
    doc_embeddings=None,
    embeddings_meta=None,
):
    """Validate and resolve a requested search mode."""
    if mode is not None and mode not in SEARCH_MODES:
        valid = ", ".join(sorted(SEARCH_MODES))
        raise SearchModeUnavailableError(
            f"unknown search mode '{mode}'. Valid modes: {valid}"
        )

    resolved = (
        mode
        if mode is not None
        else default_search_mode(
            vault_root,
            config=config,
            doc_embeddings=doc_embeddings,
            embeddings_meta=embeddings_meta,
        )
    )

    if resolved in {"semantic", "hybrid"}:
        if not _semantic_config.semantic_retrieval_enabled(vault_root, config=config):
            raise SearchModeUnavailableError(
                "semantic retrieval is disabled; enable "
                "defaults.flags.semantic_retrieval or use mode='lexical'"
            )
        if not _semantic.semantic_engine_available(
            vault_root,
            config=config,
            doc_embeddings=doc_embeddings,
            embeddings_meta=embeddings_meta,
            skip_sidecar_check=True,
        ):
            raise SearchModeUnavailableError(
                "semantic retrieval is unavailable: semantic runtime is not "
                "installed or dependencies are unavailable"
            )

    return resolved


def load_index(vault_root):
    """Load the pre-built retrieval index."""
    index_path = os.path.join(str(vault_root), OUTPUT_PATH)
    if not os.path.isfile(index_path):
        raise IndexNotFoundError(
            f"retrieval index not found at {OUTPUT_PATH}. Run build_index.py first."
        )

    with open(index_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _resolve_proxy_class(proxy, name):
    """Resolve an exception class from a lazy proxy without masking the original error."""
    try:
        return getattr(proxy, name)
    except (ImportError, AttributeError):
        return None


def _encode_query(vault_root, query, *, query_encoder=None):
    """Encode a query string for semantic retrieval."""
    try:
        return _semantic.encode_query(
            vault_root,
            query,
            query_encoder=query_encoder,
        )
    except ImportError as exc:
        raise SearchModeUnavailableError(
            f"semantic retrieval is unavailable: {exc}"
        ) from exc
    except Exception as exc:
        semantic_model_error = _resolve_proxy_class(
            _semantic_model, "SemanticModelError"
        )
        if semantic_model_error is not None and isinstance(exc, semantic_model_error):
            raise SearchModeUnavailableError(
                f"semantic retrieval is unavailable: {exc}"
            ) from exc
        raise


def load_doc_embeddings_or_unavailable(vault_root, *, loader=None):
    """Load semantic sidecars or raise the canonical unavailable error."""
    load = loader or _semantic.load_doc_embeddings
    try:
        doc_embeddings, meta = load(vault_root)
    except Exception as exc:
        embeddings_load_error = _resolve_proxy_class(
            _semantic, "SemanticEmbeddingsLoadError"
        )
        if embeddings_load_error is not None and isinstance(exc, embeddings_load_error):
            raise SearchModeUnavailableError(
                f"semantic retrieval is unavailable: {exc}"
            ) from exc
        raise
    if doc_embeddings is None or meta is None:
        return (doc_embeddings, meta)

    router = load_compiled_router(vault_root)
    if "error" in router:
        raise SearchModeUnavailableError(
            f"semantic retrieval is unavailable: {router['error']}"
        )
    try:
        if not _semantic.embeddings_meta_matches_router(meta, router):
            raise SearchModeUnavailableError(
                "semantic retrieval is unavailable: semantic embeddings were "
                "built for a different compiled router"
            )
    except Exception as exc:
        router_metadata_error = _resolve_proxy_class(
            _semantic, "RouterMetadataError"
        )
        if router_metadata_error is not None and isinstance(exc, router_metadata_error):
            raise SearchModeUnavailableError(
                f"semantic retrieval is unavailable: compiled router metadata is invalid: {exc}"
            ) from exc
        raise
    return (doc_embeddings, meta)


def _entry_matches_filters(entry, type_filter, tag_filter, status_filter):
    """Apply the standard artefact filters to an index or embedding entry."""
    if type_filter and entry.get("type") != type_filter:
        return False
    if tag_filter and tag_filter not in entry.get("tags", []):
        return False
    if status_filter and entry.get("status") != status_filter:
        return False
    return True


def extract_snippet(vault_root, rel_path, query_tokens, length=SNIPPET_LENGTH, *, body=None):
    """Extract a snippet centred on the first query-term match."""
    if body is None:
        abs_path = os.path.join(str(vault_root), rel_path)
        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                text = f.read()
        except (OSError, UnicodeDecodeError):
            return ""

        fm_match = FM_RE.match(text)
        body = text[fm_match.end():] if fm_match else text

    body = re.sub(r"\s+", " ", body).strip()
    if not body:
        return ""

    body_lower = body.lower()
    best_pos = None
    for token in query_tokens:
        pos = body_lower.find(token)
        if pos >= 0 and (best_pos is None or pos < best_pos):
            best_pos = pos

    if best_pos is None:
        snippet = body[:length]
    else:
        half = length // 2
        start = max(0, best_pos - half)
        end = min(len(body), start + length)

        if start > 0:
            space = body.rfind(" ", 0, start)
            if space >= 0 and (start - space) < 30:
                start = space + 1
        if end < len(body):
            space = body.find(" ", end)
            if space >= 0 and (space - end) < 30:
                end = space

        snippet = body[start:end]

    if not body.startswith(snippet):
        snippet = "…" + snippet
    if not body.endswith(snippet.lstrip("…")):
        snippet = snippet + "…"
    return snippet


def search(index, query, vault_root, type_filter=None, tag_filter=None, status_filter=None, top_k=DEFAULT_TOP_K, *, attach_snippets=True):
    """Score documents against query using BM25."""
    query_tokens = tokenise(query)
    if not query_tokens:
        return []

    corpus = index["corpus_stats"]
    params = index["bm25_params"]
    k1 = params["k1"]
    b = params["b"]
    total_docs = corpus["total_docs"]
    avg_dl = corpus["avg_dl"]
    df = corpus["df"]

    results = []
    for doc in index["documents"]:
        if not _entry_matches_filters(doc, type_filter, tag_filter, status_filter):
            continue

        score = 0.0
        dl = doc["doc_length"]
        tf = doc["tf"]
        title_tf = doc.get("title_tf", {})

        for term in query_tokens:
            term_df = df.get(term, 0)
            if term_df == 0:
                continue

            idf = math.log((total_docs - term_df + 0.5) / (term_df + 0.5) + 1)
            term_tf_val = tf.get(term, 0)
            if term_tf_val > 0 and avg_dl > 0:
                tf_norm = (term_tf_val * (k1 + 1)) / (term_tf_val + k1 * (1 - b + b * dl / avg_dl))
                score += idf * tf_norm

            if title_tf.get(term, 0) > 0:
                score += idf * TITLE_BOOST

        if score > 0:
            results.append(
                {
                    "path": doc["path"],
                    "title": doc["title"],
                    "type": doc["type"],
                    "status": doc.get("status"),
                    "score": round(score, 4),
                }
            )

    results.sort(key=lambda r: r["score"], reverse=True)
    top = results[:top_k]
    if attach_snippets:
        _attach_snippets(top, vault_root, query_tokens)
    return top


def dispatch_search(
    index,
    query,
    vault_root,
    mode,
    *,
    type_filter=None,
    tag_filter=None,
    status_filter=None,
    top_k=DEFAULT_TOP_K,
    doc_embeddings=None,
    embeddings_meta=None,
    query_encoder=None,
    attach_snippets=True,
):
    """Dispatch one query through the selected retrieval mode."""
    if mode == "lexical":
        return search(
            index,
            query,
            vault_root,
            type_filter=type_filter,
            tag_filter=tag_filter,
            status_filter=status_filter,
            top_k=top_k,
            attach_snippets=attach_snippets,
        )
    if mode == "semantic":
        return search_semantic(
            query,
            vault_root,
            type_filter=type_filter,
            tag_filter=tag_filter,
            status_filter=status_filter,
            top_k=top_k,
            doc_embeddings=doc_embeddings,
            embeddings_meta=embeddings_meta,
            query_encoder=query_encoder,
            attach_snippets=attach_snippets,
        )
    if mode == "hybrid":
        return search_hybrid(
            index,
            query,
            vault_root,
            type_filter=type_filter,
            tag_filter=tag_filter,
            status_filter=status_filter,
            top_k=top_k,
            doc_embeddings=doc_embeddings,
            embeddings_meta=embeddings_meta,
            query_encoder=query_encoder,
        )
    valid = ", ".join(sorted(SEARCH_MODES))
    raise ValueError(f"unknown search mode '{mode}'. Valid modes: {valid}")


def _attach_snippets(results, vault_root, query_tokens):
    """Attach a snippet to each result in-place."""
    for result in results:
        result["snippet"] = extract_snippet(vault_root, result["path"], query_tokens)


def search_semantic(
    query,
    vault_root,
    *,
    type_filter=None,
    tag_filter=None,
    status_filter=None,
    top_k=DEFAULT_TOP_K,
    doc_embeddings=None,
    embeddings_meta=None,
    query_encoder=None,
    attach_snippets=True,
):
    """Rank documents by cosine similarity against persisted document vectors."""
    query_tokens = tokenise(query)
    if not query_tokens:
        return []

    if doc_embeddings is None or embeddings_meta is None:
        doc_embeddings, embeddings_meta = load_doc_embeddings_or_unavailable(vault_root)
    if doc_embeddings is None or embeddings_meta is None:
        raise SearchModeUnavailableError(
            "semantic retrieval is unavailable: embeddings sidecars are missing"
        )

    # Tests in test_search_index.py and test_mcp_server.py monkeypatch
    # _encode_query with two-positional-arg lambdas; preserve that call shape.
    if query_encoder is None:
        query_vec = _encode_query(vault_root, query)
    else:
        query_vec = _encode_query(vault_root, query, query_encoder=query_encoder)
    ranked = _semantic.rank_against(
        query_vec,
        doc_embeddings,
        embeddings_meta.get("documents", []),
        filter_fn=lambda entry: _entry_matches_filters(
            entry, type_filter, tag_filter, status_filter
        ),
        top_k=top_k,
    )
    top = [
        {
            "path": entry["path"],
            "title": entry["title"],
            "type": entry["type"],
            "status": entry.get("status"),
            "score": round(entry["score"], 4),
        }
        for entry in ranked
    ]
    if attach_snippets:
        _attach_snippets(top, vault_root, query_tokens)
    return top


def _semantic_champion_bonus_applies(semantic_results):
    return (
        len(semantic_results) >= 2
        and semantic_results[0]["score"] - semantic_results[1]["score"] >= SEMANTIC_CHAMPION_MARGIN
    )


def _core_title_tokens(title):
    """Return title tokens with the known Brain product namespace stripped."""
    tokens = tokenise(title)
    while tokens and tokens[0] in BRAIN_PRODUCT_NAMESPACE_PREFIX_TOKENS:
        tokens = tokens[1:]
    return tokens


def _lexical_title_champion_bonus_applies(query_tokens, lexical_results, semantic_results):
    """Return whether hybrid should preserve a strong lexical title champion."""
    if not query_tokens or not lexical_results or not semantic_results:
        return False
    if lexical_results[0]["path"] == semantic_results[0]["path"]:
        return False

    title_tokens = _core_title_tokens(lexical_results[0]["title"])
    if len(title_tokens) < LEXICAL_TITLE_CHAMPION_MIN_TOKENS:
        return False

    query_text = " ".join(query_tokens)
    title_phrase = " ".join(title_tokens)
    if title_phrase not in query_text:
        return False

    if len(lexical_results) < 2:
        return True
    return (
        lexical_results[0]["score"]
        >= lexical_results[1]["score"] * LEXICAL_TITLE_CHAMPION_MIN_MARGIN
    )


def _semantic_rescue_applies(query_tokens, lexical_results, semantic_results):
    """Return whether hybrid should apply the stronger semantic rescue bonus."""
    query_token_set = set(query_tokens)
    if len(query_token_set) < SEMANTIC_RESCUE_MIN_QUERY_TOKENS:
        return False
    if not lexical_results or not semantic_results:
        return False

    lexical_title_tokens = set(tokenise(lexical_results[0]["title"]))
    title_overlap = len(query_token_set & lexical_title_tokens)
    if title_overlap > SEMANTIC_RESCUE_MAX_LEXICAL_TITLE_OVERLAP:
        return False

    lexical_top = {
        result["path"] for result in lexical_results[:SEMANTIC_RESCUE_TOP_OVERLAP_WINDOW]
    }
    semantic_top = {
        result["path"] for result in semantic_results[:SEMANTIC_RESCUE_TOP_OVERLAP_WINDOW]
    }
    return not (lexical_top & semantic_top)


def _fuse_rrf(lexical_results, semantic_results, *, top_k, query_tokens=()):
    """Fuse two ranked result lists with Reciprocal Rank Fusion."""
    fused = {}
    for weight, results in (
        (RRF_LEXICAL_WEIGHT, lexical_results),
        (RRF_SEMANTIC_WEIGHT, semantic_results),
    ):
        for rank, result in enumerate(results, start=1):
            path = result["path"]
            entry = fused.setdefault(
                path,
                {
                    "path": result["path"],
                    "title": result["title"],
                    "type": result["type"],
                    "status": result.get("status"),
                    "snippet": result.get("snippet", ""),
                    "score": 0.0,
                },
            )
            entry["score"] += weight / (RRF_K + rank)
    if _semantic_champion_bonus_applies(semantic_results):
        champion = fused.get(semantic_results[0]["path"])
        if champion is not None:
            champion["score"] += SEMANTIC_CHAMPION_BONUS
    if _lexical_title_champion_bonus_applies(query_tokens, lexical_results, semantic_results):
        champion = fused.get(lexical_results[0]["path"])
        if champion is not None:
            champion["score"] += LEXICAL_TITLE_CHAMPION_BONUS
    if _semantic_rescue_applies(query_tokens, lexical_results, semantic_results):
        rescue = fused.get(semantic_results[0]["path"])
        if rescue is not None:
            rescue["score"] += SEMANTIC_RESCUE_BONUS
    ranked = sorted(fused.values(), key=lambda result: result["score"], reverse=True)
    for result in ranked:
        result["score"] = round(result["score"], 6)
    return ranked[:top_k]


def search_hybrid(
    index,
    query,
    vault_root,
    *,
    type_filter=None,
    tag_filter=None,
    status_filter=None,
    top_k=DEFAULT_TOP_K,
    doc_embeddings=None,
    embeddings_meta=None,
    query_encoder=None,
):
    """Fuse lexical BM25 and semantic retrieval with RRF."""
    if LEXICAL_ANCHOR_RE.search(query):
        return search(
            index,
            query,
            vault_root,
            type_filter=type_filter,
            tag_filter=tag_filter,
            status_filter=status_filter,
            top_k=top_k,
        )
    query_tokens = tokenise(query)
    candidate_k = top_k * RRF_CANDIDATE_MULTIPLIER
    lexical_results = search(
        index,
        query,
        vault_root,
        type_filter=type_filter,
        tag_filter=tag_filter,
        status_filter=status_filter,
        top_k=candidate_k,
        attach_snippets=False,
    )
    semantic_results = search_semantic(
        query,
        vault_root,
        type_filter=type_filter,
        tag_filter=tag_filter,
        status_filter=status_filter,
        top_k=candidate_k,
        doc_embeddings=doc_embeddings,
        embeddings_meta=embeddings_meta,
        query_encoder=query_encoder,
        attach_snippets=False,
    )
    fused = _fuse_rrf(
        lexical_results,
        semantic_results,
        top_k=top_k,
        query_tokens=query_tokens,
    )
    _attach_snippets(fused, vault_root, query_tokens)
    return fused


_SEARCH_RESOURCE_MAP = {
    "skill": ("skills", "skill_doc", ()),
    "style": ("styles", "style_doc", ()),
    "memory": ("memories", "memory_doc", ("triggers",)),
    "trigger": ("triggers", None, ("condition", "target", "detail")),
    "plugin": ("plugins", "skill_doc", ()),
}

SEARCHABLE_RESOURCES = {"artefact"} | set(_SEARCH_RESOURCE_MAP)


def _read_file_body(vault_root, rel_path):
    """Read a file and return the body (frontmatter stripped)."""
    abs_path = os.path.join(str(vault_root), rel_path)
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError):
        return ""
    fm_match = FM_RE.match(text)
    return text[fm_match.end():] if fm_match else text


def search_resource(router, vault_root, resource, query, top_k=DEFAULT_TOP_K):
    """Search a non-artefact resource by text matching on name + file content."""
    if resource == "artefact":
        raise ValueError("Use search() for artefact search, not search_resource().")
    if resource not in _SEARCH_RESOURCE_MAP:
        _searchable = sorted(SEARCHABLE_RESOURCES)
        raise ValueError(
            f"Resource '{resource}' is not searchable. "
            f"Searchable resources: {', '.join(_searchable)}"
        )

    router_key, doc_field, extra_fields = _SEARCH_RESOURCE_MAP[resource]
    items = router.get(router_key, [])
    query_lower = query.lower()
    query_tokens = tokenise(query)

    results = []
    for item in items:
        name = item.get("name", "")
        parts = [name]
        for field in extra_fields:
            val = item.get(field)
            if isinstance(val, list):
                parts.extend(val)
            elif val:
                parts.append(val)
        searchable = " ".join(parts)

        file_body = ""
        if doc_field and item.get(doc_field):
            file_body = _read_file_body(vault_root, item[doc_field])
            searchable += " " + file_body

        score = searchable.lower().count(query_lower)
        if score <= 0:
            continue

        snippet = extract_snippet(
            vault_root, item.get(doc_field) or "", query_tokens, body=file_body or searchable
        )

        results.append(
            {
                "path": item.get(doc_field, "") if doc_field else "",
                "title": name,
                "type": resource,
                "status": None,
                "score": round(score, 4),
                "snippet": snippet,
            }
        )

    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:top_k]
