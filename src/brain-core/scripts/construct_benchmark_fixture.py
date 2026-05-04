#!/usr/bin/env python3
"""
construct_benchmark_fixture.py — derive a vault-native retrieval benchmark.

Scans an existing vault, mines candidate cases for lexical / semantic / hybrid /
cluster / filter-sensitive buckets, audits them against the live retrieval
implementation, and writes:

- a benchmark fixture JSON
- an audit JSON explaining admissions, shortfalls, and rejections

The constructor is deliberately conservative. If a vault cannot naturally
support enough good cases for one bucket, the audit records the shortfall
instead of padding the fixture with weak cases.

Usage:
    python3 construct_benchmark_fixture.py --fixture-out fixture.json
    python3 construct_benchmark_fixture.py --vault /path/to/vault --fixture-out fixture.json --audit-out audit.json
    python3 construct_benchmark_fixture.py --fixture-out fixture.json --hybrid-seed-file hybrid-seeds.json
    python3 construct_benchmark_fixture.py --fixture-out fixture.json --json
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

import retrieval_embeddings as _retrieval_embeddings
import search_index as si
from _common import (
    LEXICAL_ANCHOR_RE,
    build_vault_file_index,
    extract_wikilinks,
    find_vault_root,
    now_iso,
    read_artefact,
    resolve_artefact_path,
    safe_write_json,
    title_to_slug,
    tokenise,
)


HIT_KS = (1, 3, 5)
SEMANTIC_STRATEGY_LOCAL = "local"
SEMANTIC_STRATEGY_ASSISTED_ZERO_OVERLAP = "assisted-zero-overlap"
SEMANTIC_STRATEGY_SEED_FILE = "seed-file"
BUCKET_ORDER = (
    "lexical-expected",
    "semantic-expected",
    "hybrid-expected",
    "cluster-expected",
    "filter-sensitive",
)
DEFAULT_TARGETS = {
    "lexical-expected": 8,
    "semantic-expected": 8,
    "hybrid-expected": 8,
    "cluster-expected": 4,
    "filter-sensitive": 4,
}
DEFAULT_TOP_K = 5
MAX_SNIPPET_WORDS = 18
MAX_SEMANTIC_FRAGMENTS_PER_DOC = 3
MAX_HYBRID_FRAGMENTS_PER_DOC = 2
AUDIT_RESULT_FETCH_MULTIPLIER = 3
AUDIT_RESULT_FALLBACK_FETCH_MULTIPLIER = 5
MAX_ZERO_OVERLAP_REWRITE_ROUNDS = 3
MAX_ZERO_OVERLAP_VARIANTS = 8
ZERO_OVERLAP_TARGET_QUERY_LENGTH = 10
MAX_AUDIT_CANDIDATES = {
    "lexical-expected": 80,
    "semantic-expected": 160,
    "hybrid-expected": 160,
    "cluster-expected": 40,
    "filter-sensitive": 40,
}
STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "did",
    "do",
    "does",
    "as",
    "at",
    "be",
    "before",
    "between",
    "by",
    "can",
    "for",
    "from",
    "how",
    "if",
    "i",
    "in",
    "into",
    "is",
    "it",
    "its",
    "me",
    "my",
    "of",
    "on",
    "one",
    "or",
    "our",
    "rather",
    "she",
    "so",
    "than",
    "that",
    "the",
    "their",
    "them",
    "there",
    "these",
    "this",
    "to",
    "too",
    "under",
    "until",
    "us",
    "use",
    "uses",
    "using",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
    "without",
    "you",
    "your",
}
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
TYPE_SEGMENT_RE = re.compile(r"[^a-z0-9]+")
FAMILY_CODE_RE = re.compile(r"^([A-Z]{2,})[-]?\d")
INLINE_CODE_RE = re.compile(r"`[^`]+`")
WIKILINK_RE = re.compile(r"\[\[([^|\]]+)(?:\|([^\]]+))?\]\]")
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
FENCED_CODE_RE = re.compile(r"```.*?```", re.DOTALL)
LIST_MARKER_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+")
CHECKLIST_RE = re.compile(r"^\s*[-*+]\s+\[[ xX]\]\s+")
HEADING_RE = re.compile(r"^\s*#{1,6}\s+")
SECTION_RE = re.compile(r"^##+\s+([^\n]+)\n(.*?)(?=^##+\s+|\Z)", re.MULTILINE | re.DOTALL)
CODEY_TEXT_RE = re.compile(
    r"(?:\b[A-Za-z0-9_-]+\.(?:py|md|json|toml|yaml|yml|sh|css|js)\b|->|==|!=|<=|>=|"
    r"\b[a-z0-9_-]+/[a-z0-9_./-]+\b)"
)
BENCHMARK_PATH_MARKERS = (
    "/semantic benchmark/",
    "/benchmark/",
    "/benchmarks/",
    "/fixture/",
    "/fixtures/",
)
LOW_SIGNAL_SEMANTIC_TYPES = {
    "living/daily-note",
    "temporal/bug-log",
    "temporal/capture",
    "temporal/cookie",
    "temporal/decision-log",
    "temporal/friction-log",
    "temporal/idea-log",
    "temporal/ingestion",
    "temporal/log",
    "temporal/mockup",
    "temporal/observation",
    "temporal/snippet",
    "temporal/thought",
}
RELATION_SENTENCE_RE = re.compile(r"\bmy ([a-z][a-z -]{1,40})\.", re.IGNORECASE)
FIRST_MET_RE = re.compile(r"\bfirst met\b", re.IGNORECASE)
FIRST_MET_AT_RE = re.compile(r"\bfirst met\b.+?\bat\b", re.IGNORECASE)
RELATIONSHIP_BEGIN_RE = re.compile(
    r"\b(?:"
    r"started dating|began dating|dating since|got together|became a couple|"
    r"became official|officially together|relationship began|relationship started|"
    r"started our relationship|began our relationship|been together since|together since"
    r")\b",
    re.IGNORECASE,
)
RELATION_QUERY_ALIASES = {
    "girlfriend": ("partner", "gf", "girlfriend"),
    "boyfriend": ("partner", "bf", "boyfriend"),
    "wife": ("partner", "wife"),
    "husband": ("partner", "husband"),
}
MAX_SEMANTIC_VARIANTS_PER_SOURCE = 3
MAX_HYBRID_VARIANTS_PER_SOURCE = 2
QUERY_STYLE_QUESTION = "question"
QUERY_STYLE_FRAGMENT = "fragment"
QUERY_STYLE_ZERO_OVERLAP = "zero-overlap"
QUERY_STYLE_SEEDED = "seeded"
QUERY_STYLE_HYBRID_REWRITE = "hybrid-rewrite"
SEMANTIC_NOTE_PREFIX_BY_STYLE = {
    QUERY_STYLE_ZERO_OVERLAP: "Assisted zero-overlap semantic query",
    QUERY_STYLE_QUESTION: "Question-style semantic query",
    QUERY_STYLE_FRAGMENT: "Body-derived low-overlap paraphrase",
}
HYBRID_PREFIX_DROPPABLE_TOKENS = {
    "design",
    "idea",
    "ideas",
    "log",
    "logs",
    "mockup",
    "note",
    "notes",
    "plan",
    "plans",
    "presentation",
    "printable",
    "report",
    "reports",
    "research",
    "session",
    "sessions",
    "shaping",
    "transcript",
    "transcripts",
    "wiki",
}
ZERO_OVERLAP_TOKEN_ALIASES = {
    "agent": ("assistant", "operator"),
    "agents": ("assistants", "operators"),
    "artefact": ("document", "record"),
    "artefacts": ("documents", "records"),
    "bootstrap": ("handshake", "initialisation"),
    "builds": ("shipments", "releases"),
    "coding": ("programming",),
    "config": ("setup",),
    "configuration": ("setup",),
    "context": ("state", "background"),
    "documentation": ("writeup", "written-material"),
    "framework": ("structure", "scaffold"),
    "first": ("initial",),
    "girlfriend": ("partner", "gf"),
    "husband": ("partner",),
    "install": ("set-up", "provision"),
    "installation": ("setup", "provisioning"),
    "memory": ("recollection", "state"),
    "meet": ("encounter",),
    "met": ("encountered",),
    "mutation": ("change", "rewrite"),
    "mutations": ("changes", "rewrites"),
    "persistent": ("durable", "enduring"),
    "plugin": ("extension",),
    "protocol": ("ruleset",),
    "release": ("shipment", "launch"),
    "releases": ("shipments", "launches"),
    "router": ("dispatcher",),
    "search": ("lookup",),
    "semantic": ("meaning-based",),
    "session": ("run",),
    "sessions": ("runs",),
    "system": ("mechanism",),
    "systems": ("mechanisms",),
    "task": ("work-item",),
    "tasks": ("work-items",),
    "tool": ("mechanism",),
    "tools": ("mechanisms",),
    "vault": ("knowledgebase",),
    "wife": ("partner",),
    "workflow": ("process",),
}
ZERO_OVERLAP_DROPPABLE_TOKENS = {
    "design",
    "designs",
    "document",
    "documents",
    "implementation",
    "implementations",
    "note",
    "notes",
    "plan",
    "plans",
    "process",
    "processes",
    "project",
    "projects",
    "report",
    "reports",
    "research",
    "task",
    "tasks",
    "workflow",
    "workflows",
}


def _humanise_type(doc_type):
    if not doc_type:
        return ""
    tail = doc_type.split("/")[-1]
    return TYPE_SEGMENT_RE.sub(" ", tail).strip()


def _normalise_rel_path(rel_path):
    return f"/{str(rel_path).replace(os.sep, '/').lower().strip('/')}/"


def _is_benchmark_like_path(rel_path):
    normalised = _normalise_rel_path(rel_path)
    return any(marker in normalised for marker in BENCHMARK_PATH_MARKERS)


def _should_skip_doc(doc, *, bucket=None):
    rel_path = doc.get("path") or ""
    if not rel_path:
        return True
    if _is_benchmark_like_path(rel_path):
        return True
    if bucket in {"semantic-expected", "hybrid-expected"} and doc.get("type") in LOW_SIGNAL_SEMANTIC_TYPES:
        return True
    return False


def _read_body(vault_root, rel_path):
    fields, body = read_artefact(os.path.join(str(vault_root), rel_path))
    return fields, body


def _body_sentences(body):
    parts = []
    for sentence in SENTENCE_SPLIT_RE.split(body):
        cleaned = " ".join(sentence.split()).strip(" -*")
        if cleaned:
            parts.append(cleaned)
    return parts


def _replace_wikilink(match):
    target, label = match.groups()
    return label or target.split("/")[-1]


def _remove_wikilink(match):
    return " "


def _clean_markdown_line(line):
    line = line.strip()
    if not line:
        return None
    if line.startswith("|") or line.startswith(">"):
        return None
    if line.startswith("[!") or line.startswith("```"):
        return None
    if CHECKLIST_RE.match(line):
        return None
    if LIST_MARKER_RE.match(line):
        return None
    if HEADING_RE.match(line):
        return None
    if line.startswith("![]("):
        return None

    line = INLINE_CODE_RE.sub(" ", line)
    line = WIKILINK_RE.sub(_replace_wikilink, line)
    line = MARKDOWN_LINK_RE.sub(r"\1", line)
    line = line.replace("**", " ").replace("__", " ").replace("*", " ").replace("_", " ")
    line = " ".join(line.split())
    if not line:
        return None
    if CODEY_TEXT_RE.search(line):
        return None
    if sum(ch.isalpha() for ch in line) < 20:
        return None
    return line


def _semantic_paragraphs(body):
    body = FENCED_CODE_RE.sub("\n", body)
    paragraphs = []
    current = []
    for raw_line in body.splitlines():
        cleaned = _clean_markdown_line(raw_line)
        if cleaned is None:
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        current.append(cleaned)
    if current:
        paragraphs.append(" ".join(current))
    return paragraphs


def _markdown_to_text(text):
    text = FENCED_CODE_RE.sub("\n", text)
    text = INLINE_CODE_RE.sub(" ", text)
    text = WIKILINK_RE.sub(_replace_wikilink, text)
    text = MARKDOWN_LINK_RE.sub(r"\1", text)
    text = text.replace("**", " ").replace("__", " ").replace("*", " ").replace("_", " ")
    return " ".join(text.split())


def _section_text(body, heading_name):
    for heading, section_body in SECTION_RE.findall(body):
        if heading.strip().lower() == heading_name.lower():
            return section_body.strip()
    return None


def _dedupe_preserve_order(items):
    seen = set()
    result = []
    for item in items:
        if not item or item in seen:
            continue
        result.append(item)
        seen.add(item)
    return result


def _significant_tokens(text):
    return [token for token in tokenise(text) if token not in STOPWORDS]


def _compress_query(text, *, max_words=MAX_SNIPPET_WORDS):
    words = text.split()
    if len(words) <= max_words:
        return " ".join(words)
    return " ".join(words[:max_words])


def _title_overlap_ratio(query_text, title):
    query_tokens = set(_significant_tokens(query_text))
    if not query_tokens:
        return 1.0
    title_tokens = set(_significant_tokens(title))
    return round(len(query_tokens & title_tokens) / len(query_tokens), 4)


def _title_subject_variants(doc):
    raw_title = (doc.get("title") or "").split("~")[-1]
    raw_title = raw_title.split("—")[-1]
    tokens = [
        token
        for token in re.findall(r"[A-Za-z][A-Za-z0-9-]*", raw_title)
        if token.lower() not in STOPWORDS
    ]
    variants = []
    if tokens:
        variants.append(tokens[0].lower())
    if len(tokens) >= 2:
        variants.append(" ".join(tokens[:2]).lower())
    return _dedupe_preserve_order(variants)


def _relation_query_variants(relation):
    relation = " ".join(relation.split()).lower()
    variants = list(RELATION_QUERY_ALIASES.get(relation, (relation,)))
    variants.append(relation)
    return _dedupe_preserve_order(variants)


def _relationship_event_queries(body):
    section = _section_text(body, "Relationship") or body
    section_text = _markdown_to_text(section)
    relation_match = RELATION_SENTENCE_RE.search(section_text)
    has_first_met = bool(FIRST_MET_RE.search(section_text))
    has_relationship_begin = bool(RELATIONSHIP_BEGIN_RE.search(section_text))
    if not relation_match or (not has_first_met and not has_relationship_begin):
        return []
    relation = " ".join(relation_match.group(1).split()).lower()
    has_location = bool(FIRST_MET_AT_RE.search(section_text))
    queries = []
    for relation_variant in _relation_query_variants(relation):
        if has_first_met:
            queries.append(f"when did i first meet my {relation_variant}?")
        if has_relationship_begin:
            queries.append(f"when did my relationship with my {relation_variant} begin?")
        if has_location:
            queries.append(f"where did i first meet my {relation_variant}?")
    return queries


def _audience_variants(audience):
    audience = audience.strip()
    variants = [audience]
    without_ai = re.sub(r"^ai\s+", "", audience).strip()
    if without_ai:
        variants.append(without_ai)
    return _dedupe_preserve_order(variants)


def _definition_query_variants(remainder):
    variants = []
    if remainder:
        variants.append(f"what tool is {remainder}?")

    memory_match = re.search(r"(.+?\bmemory)\s+for\s+(.+)", remainder)
    if memory_match:
        memory_phrase = memory_match.group(1).strip(" .")
        audience = memory_match.group(2).strip(" .")
        for audience_variant in _audience_variants(audience):
            variants.append(f"what tool gives {audience_variant} {memory_phrase}?")
            variants.append(
                f"what system keeps {memory_phrase} for {audience_variant} across sessions?"
            )
            if "persistent" in memory_phrase:
                variants.append(
                    f"what tool gives {audience_variant} persistent memory between sessions?"
                )
    return variants


def _normalise_definition_remainder(remainder):
    if "created by " in remainder:
        remainder = remainder.split("created by ", 1)[0].strip(" .")
    if "designed as " in remainder:
        return remainder.split("designed as ", 1)[1].strip(" .")
    if "designed for " in remainder:
        return remainder.split("designed for ", 1)[1].strip(" .")
    return re.sub(r"^(?:a|an|the)\s+", "", remainder)


def _definition_queries(doc, body):
    subjects = _title_subject_variants(doc)
    if not subjects:
        return []
    subject_patterns = [
        re.compile(rf"\b{re.escape(subject)}\b(?:\s+\([^)]+\))?\s+is\s+(.+)")
        for subject in subjects
    ]
    title = doc.get("title") or ""
    queries = []
    seen = set()
    for paragraph in _semantic_paragraphs(body)[:3]:
        for sentence in _body_sentences(paragraph):
            sentence_lower = _markdown_to_text(sentence).lower()
            for pattern in subject_patterns:
                match = pattern.search(sentence_lower)
                if not match:
                    continue
                remainder = _normalise_definition_remainder(match.group(1).strip(" ."))
                for query in _definition_query_variants(remainder):
                    query = _compress_query(" ".join(query.split()).lower())
                    if query in seen:
                        continue
                    if (
                        len(_significant_tokens(query)) >= 6
                        and _title_overlap_ratio(query, title) <= 0.34
                    ):
                        queries.append(query)
                        seen.add(query)
    return queries


def _question_style_semantic_queries(doc, body):
    return _dedupe_preserve_order(
        _relationship_event_queries(body) + _definition_queries(doc, body)
    )


def _extract_lexical_anchors(doc):
    anchors = []
    seen = set()
    title = doc.get("title") or ""
    rel_path = doc.get("path") or ""
    for source in (title, rel_path):
        for match in LEXICAL_ANCHOR_RE.finditer(source):
            anchor = match.group(0)
            if anchor not in seen:
                anchors.append(anchor)
                seen.add(anchor)
    return anchors


def _semantic_sentence_candidates(doc, body):
    title_tokens = set(_significant_tokens(doc.get("title") or ""))
    candidates = []
    for paragraph_index, paragraph in enumerate(_semantic_paragraphs(body)):
        for sentence in _body_sentences(paragraph):
            sentence_tokens = _significant_tokens(sentence)
            if len(sentence_tokens) < 6:
                continue
            unique_tokens = set(sentence_tokens)
            overlap = len(unique_tokens & title_tokens)
            overlap_ratio = overlap / max(1, len(unique_tokens))
            if overlap_ratio > 0.34:
                continue
            digits = sum(1 for token in unique_tokens if any(ch.isdigit() for ch in token))
            score = (
                overlap_ratio,
                paragraph_index,
                digits / max(1, len(unique_tokens)),
                abs(len(unique_tokens) - 12),
                sentence.lower(),
            )
            candidates.append((score, sentence))
    candidates.sort(key=lambda item: item[0])
    return [sentence for _score, sentence in candidates]


def _semantic_fallback_fragment(doc, body):
    title_tokens = set(_significant_tokens(doc.get("title") or ""))
    fallback_tokens = []
    for token in _significant_tokens(body):
        if token in title_tokens:
            continue
        fallback_tokens.append(token)
        if len(fallback_tokens) >= MAX_SNIPPET_WORDS:
            break
    if len(fallback_tokens) < 6:
        return None
    return " ".join(fallback_tokens)


def _pick_semantic_fragments(doc, body, *, limit=MAX_SEMANTIC_FRAGMENTS_PER_DOC):
    fragments = []
    seen = set()
    for sentence in _semantic_sentence_candidates(doc, body):
        fragment = _compress_query(sentence.lower())
        if fragment and fragment not in seen:
            fragments.append(fragment)
            seen.add(fragment)
        if len(fragments) >= limit:
            break
    fallback = _semantic_fallback_fragment(doc, body)
    if fallback and fallback not in seen and len(fragments) < limit:
        fragments.append(fallback)
    return fragments


def _pick_hybrid_queries(doc, body, *, limit=MAX_HYBRID_FRAGMENTS_PER_DOC):
    title_tokens = [token for token in _significant_tokens(doc.get("title") or "") if len(token) >= 4]
    if not title_tokens:
        return []
    anchor = " ".join(title_tokens[: min(2, len(title_tokens))])
    queries = []
    seen = set()
    for fragment in _pick_semantic_fragments(doc, body, limit=limit):
        query = _compress_query(f"{anchor} {fragment}")
        if query not in seen:
            queries.append(query)
            seen.add(query)
    return queries


def _strip_leading_hybrid_prefix(query, title):
    words = query.split()
    if not words:
        return None
    trimmed = list(words)
    changed = False
    while trimmed and re.fullmatch(r"\d{4,8}", trimmed[0]):
        trimmed = trimmed[1:]
        changed = True
    while trimmed and trimmed[0].strip("—:,.").lower() in HYBRID_PREFIX_DROPPABLE_TOKENS:
        trimmed = trimmed[1:]
        changed = True

    title_tokens = set(_significant_tokens(title))
    removed_title_overlap = False
    while trimmed:
        token = trimmed[0].strip("—:,.").lower()
        if token in title_tokens:
            trimmed = trimmed[1:]
            changed = True
            removed_title_overlap = True
            continue
        break

    if not changed or len(_significant_tokens(" ".join(trimmed))) < 6:
        return None
    rewritten = _compress_query(" ".join(trimmed).lower())
    if removed_title_overlap and rewritten == query:
        return None
    return rewritten


def _hybrid_rewrite_variants(doc, query):
    variants = []
    stripped = _strip_leading_hybrid_prefix(query, doc.get("title") or "")
    if stripped:
        variants.append(stripped)
    return _dedupe_preserve_order(variants)


def _build_filtered_corpus_vocab(index):
    vocab = set()
    for doc in index.get("documents", []):
        if _is_benchmark_like_path(doc.get("path") or ""):
            continue
        vocab.update(token for token in (doc.get("tf") or {}).keys() if token not in STOPWORDS)
        vocab.update(token for token in (doc.get("title_tf") or {}).keys() if token not in STOPWORDS)
    return vocab


def _query_overlap_tokens(query, corpus_vocab):
    return sorted({token for token in _significant_tokens(query) if token in corpus_vocab})


def _replace_query_token(query, token, replacement):
    pattern = re.compile(rf"\b{re.escape(token)}\b", re.IGNORECASE)
    return pattern.sub(replacement, query)


def _drop_query_token(query, token):
    pattern = re.compile(rf"\b{re.escape(token)}\b", re.IGNORECASE)
    rewritten = pattern.sub(" ", query)
    rewritten = re.sub(r"\s+", " ", rewritten).strip(" ?")
    if not rewritten:
        return None
    return rewritten + ("?" if query.rstrip().endswith("?") else "")


def _zero_overlap_rewrites(query, corpus_vocab):
    significant_cache = {}
    overlap_cache = {}

    def significant(text):
        cached = significant_cache.get(text)
        if cached is None:
            cached = _significant_tokens(text)
            significant_cache[text] = cached
        return cached

    def overlap(text):
        cached = overlap_cache.get(text)
        if cached is None:
            cached = sorted({token for token in significant(text) if token in corpus_vocab})
            overlap_cache[text] = cached
        return cached

    best = [(query, overlap(query))]
    seen = {query}
    zero_overlap = []
    frontier = [query]

    def sort_key(item):
        candidate, overlap_tokens = item
        return (
            len(overlap_tokens),
            abs(len(significant(candidate)) - ZERO_OVERLAP_TARGET_QUERY_LENGTH),
            candidate,
        )

    for _round in range(MAX_ZERO_OVERLAP_REWRITE_ROUNDS):
        next_frontier = []

        def add_rewrite(rewritten):
            if not rewritten:
                return
            rewritten = _compress_query(rewritten.lower())
            if rewritten and rewritten not in seen:
                seen.add(rewritten)
                next_frontier.append(rewritten)

        for current in frontier:
            overlap_tokens = overlap(current)
            if not overlap_tokens:
                zero_overlap.append(current)
                continue
            for token in overlap_tokens:
                for replacement in ZERO_OVERLAP_TOKEN_ALIASES.get(token, ()):
                    add_rewrite(_replace_query_token(current, token, replacement))
                if token in ZERO_OVERLAP_DROPPABLE_TOKENS:
                    add_rewrite(_drop_query_token(current, token))
        scored = [(candidate, overlap(candidate)) for candidate in next_frontier]
        scored.sort(key=sort_key)
        if scored:
            best.extend(scored[:MAX_ZERO_OVERLAP_VARIANTS])
        frontier = [
            candidate for candidate, overlap_tokens in scored if len(overlap_tokens) > 0
        ][:MAX_ZERO_OVERLAP_VARIANTS]

    for candidate, overlap_tokens in best:
        if not overlap_tokens:
            zero_overlap.append(candidate)
    if zero_overlap:
        return _dedupe_preserve_order(zero_overlap)
    best.sort(key=sort_key)
    return [candidate for candidate, _overlap in best[:MAX_ZERO_OVERLAP_VARIANTS]]


def _normalise_case_id(text):
    return title_to_slug(text) or "case"


def _load_seed_candidates(seed_file, *, bucket, notes_default):
    payload = json.loads(Path(seed_file).read_text(encoding="utf-8"))
    items = payload.get("candidates") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise ValueError("seed file must be a JSON list or an object with a 'candidates' list")

    candidates = []
    for idx, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"seed candidate {idx} must be an object")
        query = " ".join(str(item.get("query") or "").split()).strip()
        if not query:
            raise ValueError(f"seed candidate {idx} is missing a query")
        relevant_paths = item.get("relevant_paths")
        if not relevant_paths and item.get("target_path"):
            relevant_paths = [item["target_path"]]
        if not isinstance(relevant_paths, list) or not relevant_paths or not all(isinstance(path, str) for path in relevant_paths):
            raise ValueError(f"seed candidate {idx} must provide relevant_paths or target_path")
        if any(_is_benchmark_like_path(path) for path in relevant_paths):
            raise ValueError(
                f"seed candidate {idx} targets a benchmark-like path; seeded candidates must point at real vault artefacts"
            )
        source_path = item.get("source_path")
        if source_path is not None and source_path not in relevant_paths:
            raise ValueError(
                f"seed candidate {idx} source_path must be one of relevant_paths when provided"
            )
        source_path = source_path or relevant_paths[0]
        notes = item.get("notes") or item.get("rationale") or notes_default
        candidates.append(
            {
                "id": item.get("id") or _normalise_case_id(f"{bucket} seed {source_path} {idx}"),
                "bucket": bucket,
                "query": query.lower(),
                "relevant_paths": relevant_paths,
                "filters": dict(item.get("filters") or {}),
                "source_path": source_path,
                "query_style": QUERY_STYLE_SEEDED,
                "notes": notes,
            }
        )
    return candidates


def _load_semantic_seed_candidates(seed_file):
    candidates = _load_seed_candidates(
        seed_file,
        bucket="semantic-expected",
        notes_default="Seeded semantic candidate",
    )
    for candidate in candidates:
        candidate["semantic_strategy"] = SEMANTIC_STRATEGY_SEED_FILE
    return candidates


def _load_hybrid_seed_candidates(seed_file):
    return _load_seed_candidates(
        seed_file,
        bucket="hybrid-expected",
        notes_default="Seeded hybrid candidate",
    )


def _annotate_semantic_candidate(candidate, corpus_vocab, docs_by_path):
    annotated = dict(candidate)
    overlap_tokens = _query_overlap_tokens(annotated["query"], corpus_vocab or set())
    annotated["lexical_overlap_tokens"] = overlap_tokens
    annotated["lexical_overlap_count"] = len(overlap_tokens)
    source_doc = docs_by_path.get(annotated.get("source_path") or "")
    if source_doc and "title_overlap_ratio" not in annotated:
        annotated["title_overlap_ratio"] = _title_overlap_ratio(
            annotated["query"], source_doc.get("title") or ""
        )
    return annotated


def _rank_for_target(results, relevant_set):
    for rank, result in enumerate(results, start=1):
        if result.get("path") in relevant_set:
            return rank
    return None


def _run_mode(
    index,
    query,
    vault_root,
    mode,
    *,
    filters=None,
    doc_embeddings=None,
    embeddings_meta=None,
    query_encoder=None,
    top_k=DEFAULT_TOP_K,
    exclude_predicate=None,
):
    filters = filters or {}

    def fetch(raw_top_k):
        if mode == "lexical":
            return si.search(
                index,
                query,
                vault_root,
                type_filter=filters.get("type"),
                tag_filter=filters.get("tag"),
                status_filter=filters.get("status"),
                top_k=raw_top_k,
            )
        if mode == "semantic":
            return si.search_semantic(
                query,
                vault_root,
                type_filter=filters.get("type"),
                tag_filter=filters.get("tag"),
                status_filter=filters.get("status"),
                top_k=raw_top_k,
                doc_embeddings=doc_embeddings,
                embeddings_meta=embeddings_meta,
                query_encoder=query_encoder,
            )
        return si.search_hybrid(
            index,
            query,
            vault_root,
            type_filter=filters.get("type"),
            tag_filter=filters.get("tag"),
            status_filter=filters.get("status"),
            top_k=raw_top_k,
            doc_embeddings=doc_embeddings,
            embeddings_meta=embeddings_meta,
            query_encoder=query_encoder,
        )

    if not exclude_predicate:
        return fetch(top_k)

    initial_raw_top_k = top_k * AUDIT_RESULT_FETCH_MULTIPLIER
    results = fetch(initial_raw_top_k)
    filtered = [
        result for result in results if not exclude_predicate(result.get("path") or "")
    ]
    if len(filtered) >= top_k or len(results) < initial_raw_top_k:
        return filtered[:top_k]

    fallback_raw_top_k = top_k * AUDIT_RESULT_FALLBACK_FETCH_MULTIPLIER
    results = fetch(fallback_raw_top_k)
    filtered = [
        result for result in results if not exclude_predicate(result.get("path") or "")
    ]
    return filtered[:top_k]


def _mode_available(vault_root, mode, *, config=None, doc_embeddings=None, embeddings_meta=None):
    try:
        si.resolve_search_mode(
            vault_root,
            mode,
            config=config,
            doc_embeddings=doc_embeddings,
            embeddings_meta=embeddings_meta,
        )
    except si.SearchModeUnavailableError as exc:
        return (False, str(exc))
    return (True, None)


def _candidate_quality_key(audit):
    bucket = audit["bucket"]
    ranks = audit["ranks"]
    if bucket == "lexical-expected":
        return (
            0 if ranks["semantic"] is None else 1,
            ranks["lexical"] or 999,
            audit["query"],
        )
    if bucket == "semantic-expected":
        return (
            0 if ranks["lexical"] is None else 1,
            ranks["semantic"] or 999,
            audit.get("title_overlap_ratio", 1.0),
            audit["query"],
        )
    if bucket == "hybrid-expected":
        return (
            ranks["hybrid"] or 999,
            max(ranks["lexical"] or 999, ranks["semantic"] or 999),
            (ranks["lexical"] or 999) + (ranks["semantic"] or 999),
            audit["query"],
        )
    if bucket == "cluster-expected":
        return (
            -(audit.get("cluster_recall_at_5") or 0.0),
            audit["ranks"]["lexical"] or 999,
            audit["query"],
        )
    if bucket == "filter-sensitive":
        return (
            audit["ranks"]["lexical"] or 999,
            -audit.get("unfiltered_distractor_count", 0),
            audit["query"],
        )
    return (audit["query"],)


def _semantic_query_variant_family(query):
    query = query.strip().lower()
    if query.startswith("when did i first meet my "):
        return "relationship-when"
    if query.startswith("where did i first meet my "):
        return "relationship-where"
    if query.startswith("what tool gives "):
        return "tool-gives"
    if query.startswith("what system keeps "):
        return "system-keeps"
    if query.startswith("what tool is "):
        return "tool-is"
    return _query_family_key(query)


def _pre_audit_candidate_key(candidate):
    bucket = candidate["bucket"]
    if bucket == "semantic-expected":
        return (
            0 if candidate.get("query_style") == QUERY_STYLE_SEEDED else 1,
            0 if candidate.get("query_style") == QUERY_STYLE_ZERO_OVERLAP else 1,
            0 if candidate.get("query_style") == QUERY_STYLE_QUESTION else 1,
            candidate.get("lexical_overlap_count", 999),
            candidate.get("title_overlap_ratio", 1.0),
            abs(len(_significant_tokens(candidate["query"])) - 12),
            candidate["query"],
        )
    if bucket == "hybrid-expected":
        return (
            0 if candidate.get("query_style") == QUERY_STYLE_SEEDED else 1,
            0 if candidate.get("query_style") == QUERY_STYLE_HYBRID_REWRITE else 1,
            candidate.get("title_overlap_ratio", 1.0),
            abs(len(_significant_tokens(candidate["query"])) - 12),
            candidate["query"],
        )
    if bucket == "lexical-expected":
        return (_query_family_key(candidate["query"]), candidate["query"])
    return (candidate["query"],)


def _query_family_key(query):
    match = FAMILY_CODE_RE.match(query)
    if match:
        return match.group(1)
    tokens = _significant_tokens(query)
    if tokens:
        return tokens[0]
    return query.lower()


def _candidate_family_key(audit):
    bucket = audit["bucket"]
    if bucket == "lexical-expected":
        return _query_family_key(audit["query"])
    return audit.get("source_path") or audit["id"]


def _pre_audit_family_key(candidate):
    bucket = candidate["bucket"]
    if bucket == "lexical-expected":
        return _query_family_key(candidate["query"])
    if bucket == "semantic-expected":
        return (
            candidate.get("source_path") or candidate["id"],
            _semantic_query_variant_family(candidate["query"]),
        )
    return candidate.get("source_path") or candidate["id"]


def _prune_candidates_for_audit(candidates):
    by_bucket = defaultdict(list)
    for candidate in candidates:
        by_bucket[candidate["bucket"]].append(candidate)

    pruned = []
    for bucket in BUCKET_ORDER:
        bucket_candidates = list(by_bucket.get(bucket, []))
        bucket_candidates.sort(key=_pre_audit_candidate_key)
        max_candidates = MAX_AUDIT_CANDIDATES[bucket]
        source_counts = defaultdict(int)
        max_variants_per_source = {
            "semantic-expected": MAX_SEMANTIC_VARIANTS_PER_SOURCE,
            "hybrid-expected": MAX_HYBRID_VARIANTS_PER_SOURCE,
        }.get(bucket)

        def source_capped(candidate):
            if max_variants_per_source is None:
                return False
            source_path = candidate.get("source_path")
            return bool(source_path) and source_counts[source_path] >= max_variants_per_source

        def admit(candidate, chosen):
            chosen.append(candidate)
            if max_variants_per_source is not None:
                source_path = candidate.get("source_path")
                if source_path:
                    source_counts[source_path] += 1

        chosen = []
        seen_families = set()
        for candidate in bucket_candidates:
            if source_capped(candidate):
                continue
            family = _pre_audit_family_key(candidate)
            if family in seen_families:
                continue
            admit(candidate, chosen)
            seen_families.add(family)
            if len(chosen) >= max_candidates:
                break
        if len(chosen) < max_candidates:
            chosen_ids = {candidate["id"] for candidate in chosen}
            for candidate in bucket_candidates:
                if candidate["id"] in chosen_ids or source_capped(candidate):
                    continue
                admit(candidate, chosen)
                if len(chosen) >= max_candidates:
                    break
        pruned.extend(chosen)
    return pruned


def _case_from_audit(audit):
    case = {
        "id": audit["id"],
        "query": audit["query"],
        "intent": audit["bucket"],
        "notes": audit["admission_reason"],
        "relevant_paths": audit["relevant_paths"],
    }
    if audit.get("filters"):
        case["filters"] = audit["filters"]
    return case


def _admit_lexical(audit):
    lex_rank = audit["ranks"]["lexical"]
    sem_rank = audit["ranks"]["semantic"]
    if lex_rank == 1 and sem_rank is None:
        return (True, "lexical rank 1 and semantic miss at top-5")
    return (False, "requires lexical rank 1 and semantic top-5 miss")


def _admit_semantic(audit):
    lex_rank = audit["ranks"]["lexical"]
    sem_rank = audit["ranks"]["semantic"]
    if (
        audit.get("semantic_strategy") == SEMANTIC_STRATEGY_ASSISTED_ZERO_OVERLAP
        and (audit.get("lexical_overlap_count") or 0) > 0
    ):
        return (False, "requires zero significant lexical overlap for assisted-zero-overlap semantic cases")
    if sem_rank == 1 and lex_rank is None:
        return (True, "semantic rank 1 and lexical miss at top-5")
    return (False, "requires semantic rank 1 and lexical top-5 miss")


def _admit_hybrid(audit):
    lex_rank = audit["ranks"]["lexical"]
    sem_rank = audit["ranks"]["semantic"]
    hybrid_rank = audit["ranks"]["hybrid"]
    if (
        lex_rank is not None
        and lex_rank <= DEFAULT_TOP_K
        and sem_rank is not None
        and sem_rank <= DEFAULT_TOP_K
        and hybrid_rank == 1
        and (lex_rank > 1 or sem_rank > 1)
    ):
        return (
            True,
            "hybrid rank 1 with lexical and semantic both hitting top-5, and at least one pure mode weaker than rank 1",
        )
    return (
        False,
        "requires hybrid rank 1, lexical and semantic top-5 hits, and at least one pure mode weaker than rank 1",
    )


def _admit_cluster(audit):
    if len(audit["relevant_paths"]) < 2:
        return (False, "requires at least two relevant artefacts")
    lex_rank = audit["ranks"]["lexical"]
    if lex_rank is None or lex_rank > 3:
        return (False, "requires first relevant result by top-3 in lexical mode")
    if (audit.get("cluster_recall_at_5") or 0.0) < 0.5:
        return (False, "requires cluster recall@5 >= 0.5 in lexical mode")
    return (True, "first lexical hit by top-3 and cluster recall@5 >= 0.5")


def _admit_filter(audit):
    lex_rank = audit["ranks"]["lexical"]
    if lex_rank != 1:
        return (False, "requires filtered lexical rank 1")
    if audit.get("unfiltered_distractor_count", 0) < 1:
        return (False, "requires at least one unfiltered top-3 distractor")
    return (True, "filtered lexical rank 1 with at least one unfiltered distractor")


def _admit_candidate(audit):
    bucket = audit["bucket"]
    if bucket == "lexical-expected":
        return _admit_lexical(audit)
    if bucket == "semantic-expected":
        return _admit_semantic(audit)
    if bucket == "hybrid-expected":
        return _admit_hybrid(audit)
    if bucket == "cluster-expected":
        return _admit_cluster(audit)
    if bucket == "filter-sensitive":
        return _admit_filter(audit)
    return (False, "unknown bucket")


def _is_near_pure_semantic(ranks):
    lex_rank = ranks["lexical"]
    sem_rank = ranks["semantic"]
    return sem_rank == 1 and lex_rank is not None and 2 <= lex_rank <= DEFAULT_TOP_K


def _semantic_variant_quality_key(audit):
    ranks = audit["ranks"]
    semantic_rank = ranks["semantic"] or 999
    lexical_rank = ranks["lexical"] or 999
    return (
        0 if audit["admitted"] else 1,
        0 if audit.get("near_pure_semantic") else 1,
        0 if ranks["semantic"] is not None else 1,
        semantic_rank,
        lexical_rank,
        audit.get("title_overlap_ratio", 1.0),
        audit["query"],
    )


def _annotate_semantic_variant_diagnostics(audits):
    semantic_groups = defaultdict(list)
    for audit in audits:
        if audit["bucket"] != "semantic-expected":
            continue
        audit["near_pure_semantic"] = _is_near_pure_semantic(audit["ranks"])
        semantic_groups[audit.get("source_path") or audit["id"]].append(audit)

    for group in semantic_groups.values():
        best = min(group, key=_semantic_variant_quality_key)
        for audit in group:
            audit["best_source_variant"] = audit is best


def _select_cases(audits, targets):
    by_bucket = defaultdict(list)
    for audit in audits:
        by_bucket[audit["bucket"]].append(audit)

    admitted_cases = []
    summary = {}
    for bucket in BUCKET_ORDER:
        candidates = [audit for audit in by_bucket.get(bucket, []) if audit["admitted"]]
        if bucket == "semantic-expected":
            candidates = [
                audit for audit in candidates if audit.get("best_source_variant", True)
            ]
        candidates.sort(key=_candidate_quality_key)
        chosen = []
        seen_families = set()
        for audit in candidates:
            family = _candidate_family_key(audit)
            if family in seen_families:
                continue
            chosen.append(audit)
            seen_families.add(family)
            if len(chosen) >= targets[bucket]:
                break
        if len(chosen) < targets[bucket]:
            chosen_ids = {audit["id"] for audit in chosen}
            for audit in candidates:
                if audit["id"] in chosen_ids:
                    continue
                chosen.append(audit)
                if len(chosen) >= targets[bucket]:
                    break
        for audit in chosen:
            admitted_cases.append(_case_from_audit(audit))
        summary[bucket] = {
            "target": targets[bucket],
            "admitted": len(chosen),
            "shortfall": max(0, targets[bucket] - len(chosen)),
            "candidate_count": len(by_bucket.get(bucket, [])),
            "admitted_candidate_count": len(candidates),
            "selected_case_ids": [audit["id"] for audit in chosen],
        }
        if bucket == "semantic-expected":
            summary[bucket]["near_pure_candidate_count"] = sum(
                1 for audit in by_bucket.get(bucket, []) if audit.get("near_pure_semantic")
            )
            summary[bucket]["best_variant_candidate_count"] = sum(
                1 for audit in by_bucket.get(bucket, []) if audit.get("best_source_variant")
            )
    return admitted_cases, summary


def _build_fixture(benchmark_name, admitted_cases):
    return {
        "version": 1,
        "description": (
            f"Vault-derived retrieval benchmark constructed by "
            f"construct_benchmark_fixture.py from {benchmark_name}."
        ),
        "hit_ks": list(HIT_KS),
        "cases": admitted_cases,
    }


def _audit_candidate(index, vault_root, candidate, *, config=None, doc_embeddings=None, embeddings_meta=None, query_encoder=None, semantic_available=False):
    filters = dict(candidate.get("filters", {}))
    relevant_set = set(candidate["relevant_paths"])
    ranks = {"lexical": None, "semantic": None, "hybrid": None}
    top_paths = {"lexical": [], "semantic": [], "hybrid": []}
    unfiltered_top_paths = []
    for mode in ("lexical", "semantic", "hybrid"):
        if mode in {"semantic", "hybrid"} and not semantic_available:
            continue
        results = _run_mode(
            index,
            candidate["query"],
            vault_root,
            mode,
            filters=filters,
            doc_embeddings=doc_embeddings,
            embeddings_meta=embeddings_meta,
            query_encoder=query_encoder,
            top_k=DEFAULT_TOP_K,
            exclude_predicate=_is_benchmark_like_path,
        )
        top_paths[mode] = [result["path"] for result in results[:DEFAULT_TOP_K]]
        ranks[mode] = _rank_for_target(results, relevant_set)

    cluster_recall_at_5 = None
    if len(candidate["relevant_paths"]) > 1 and top_paths["lexical"]:
        found = {
            path
            for path in top_paths["lexical"][:DEFAULT_TOP_K]
            if path in relevant_set
        }
        cluster_recall_at_5 = round(len(found) / len(candidate["relevant_paths"]), 4)

    unfiltered_distractor_count = None
    if candidate["bucket"] == "filter-sensitive":
        unfiltered = _run_mode(
            index,
            candidate["query"],
            vault_root,
            "lexical",
            filters={},
            doc_embeddings=doc_embeddings,
            embeddings_meta=embeddings_meta,
            query_encoder=query_encoder,
            top_k=3,
            exclude_predicate=_is_benchmark_like_path,
        )
        unfiltered_top_paths = [result["path"] for result in unfiltered[:3]]
        unfiltered_distractor_count = sum(
            1 for path in unfiltered_top_paths if path not in relevant_set
        )

    admitted, reason = _admit_candidate(
        {
            **candidate,
            "ranks": ranks,
            "cluster_recall_at_5": cluster_recall_at_5,
            "unfiltered_distractor_count": unfiltered_distractor_count,
        }
    )
    return {
        **candidate,
        "ranks": ranks,
        "top_paths": top_paths,
        "cluster_recall_at_5": cluster_recall_at_5,
        "unfiltered_top_paths": unfiltered_top_paths,
        "unfiltered_distractor_count": unfiltered_distractor_count,
        "admitted": admitted,
        "admission_reason": reason,
    }


def _mine_lexical_candidates(index):
    candidates = []
    seen_queries = set()
    for doc in index.get("documents", []):
        if _should_skip_doc(doc, bucket="lexical-expected"):
            continue
        for anchor in _extract_lexical_anchors(doc):
            query = anchor
            key = (query, tuple([doc["path"]]))
            if key in seen_queries:
                continue
            seen_queries.add(key)
            candidates.append(
                {
                    "id": _normalise_case_id(f"lexical {query}"),
                    "bucket": "lexical-expected",
                    "query": query,
                    "relevant_paths": [doc["path"]],
                    "filters": {},
                    "source_path": doc["path"],
                    "notes": f"Exact lexical anchor mined from {doc['title']}.",
                }
            )
            break
    return candidates


def _mine_semantic_candidates(
    vault_root,
    index,
    *,
    semantic_strategy=SEMANTIC_STRATEGY_LOCAL,
    corpus_vocab=None,
):
    candidates = []
    for doc in index.get("documents", []):
        if _should_skip_doc(doc, bucket="semantic-expected"):
            continue
        try:
            _fields, body = _read_body(vault_root, doc["path"])
        except (OSError, UnicodeDecodeError):
            continue
        question_queries = _question_style_semantic_queries(doc, body)
        if len(body) < 300 and not question_queries:
            continue
        ordered_queries = []
        seen_queries = set()
        for query in question_queries:
            if query not in seen_queries:
                ordered_queries.append((query, QUERY_STYLE_QUESTION))
                seen_queries.add(query)
        if semantic_strategy == SEMANTIC_STRATEGY_LOCAL or not question_queries:
            for query in _pick_semantic_fragments(doc, body):
                if query not in seen_queries:
                    ordered_queries.append((query, QUERY_STYLE_FRAGMENT))
                    seen_queries.add(query)
        if (
            semantic_strategy == SEMANTIC_STRATEGY_ASSISTED_ZERO_OVERLAP
            and corpus_vocab is not None
        ):
            assisted_queries = []
            assisted_seen = set()
            for query, _query_style in ordered_queries:
                for rewritten in _zero_overlap_rewrites(query, corpus_vocab):
                    if rewritten in assisted_seen:
                        continue
                    assisted_queries.append((rewritten, QUERY_STYLE_ZERO_OVERLAP))
                    assisted_seen.add(rewritten)
            ordered_queries = assisted_queries
        for idx, (query, query_style) in enumerate(ordered_queries, start=1):
            note_prefix = SEMANTIC_NOTE_PREFIX_BY_STYLE.get(
                query_style, SEMANTIC_NOTE_PREFIX_BY_STYLE[QUERY_STYLE_FRAGMENT]
            )
            type_suffix = (
                f" ({_humanise_type(doc.get('type'))})" if doc.get("type") else ""
            )
            candidates.append(
                {
                    "id": _normalise_case_id(f"semantic {doc['title']} {idx}"),
                    "bucket": "semantic-expected",
                    "query": query,
                    "relevant_paths": [doc["path"]],
                    "filters": {},
                    "source_path": doc["path"],
                    "query_style": query_style,
                    "semantic_strategy": semantic_strategy,
                    "notes": f"{note_prefix} {idx} for {doc['title']}{type_suffix}",
                }
            )
    return candidates


def _mine_link_context_semantic_candidates(vault_root, index):
    docs_by_path = {doc["path"]: doc for doc in index.get("documents", [])}
    file_index = build_vault_file_index(vault_root)
    candidates = []
    for doc in index.get("documents", []):
        if _should_skip_doc(doc, bucket="semantic-expected"):
            continue
        try:
            _fields, body = _read_body(vault_root, doc["path"])
        except (OSError, UnicodeDecodeError):
            continue
        for line_index, raw_line in enumerate(body.splitlines(), start=1):
            if "[[" not in raw_line:
                continue
            links = extract_wikilinks(raw_line)
            if not links:
                continue
            query = _clean_markdown_line(WIKILINK_RE.sub(_remove_wikilink, raw_line))
            if not query or len(_significant_tokens(query)) < 6:
                continue
            for link in links:
                try:
                    target_path = resolve_artefact_path(
                        link["stem"], vault_root, file_index=file_index
                    )
                except ValueError:
                    continue
                if target_path == doc["path"]:
                    continue
                target_doc = docs_by_path.get(target_path)
                if not target_doc or _should_skip_doc(
                    target_doc, bucket="semantic-expected"
                ):
                    continue
                candidates.append(
                    {
                        "id": _normalise_case_id(
                            f"semantic link {target_doc['title']} {doc['title']} {line_index}"
                        ),
                        "bucket": "semantic-expected",
                        "query": query.lower(),
                        "relevant_paths": [target_path],
                        "filters": {},
                        "source_path": target_path,
                        "context_path": doc["path"],
                        "title_overlap_ratio": _title_overlap_ratio(
                            query, target_doc["title"]
                        ),
                        "notes": (
                            f"Linked-context narrative from {doc['title']} for {target_doc['title']}"
                        ),
                    }
                )
    return candidates


def _mine_hybrid_candidates(vault_root, index):
    candidates = []
    for doc in index.get("documents", []):
        if _should_skip_doc(doc, bucket="hybrid-expected"):
            continue
        try:
            _fields, body = _read_body(vault_root, doc["path"])
        except (OSError, UnicodeDecodeError):
            continue
        if len(body) < 200:
            continue
        seen_queries = set()
        for idx, query in enumerate(_pick_hybrid_queries(doc, body), start=1):
            query_variants = [(query, None, f"Mixed anchor and concept query {idx} for {doc['title']}.")]
            for rewrite_idx, rewrite in enumerate(_hybrid_rewrite_variants(doc, query), start=1):
                query_variants.append(
                    (
                        rewrite,
                        QUERY_STYLE_HYBRID_REWRITE,
                        f"Hybrid rewrite variant {idx}.{rewrite_idx} for {doc['title']}.",
                    )
                )
            for query_text, query_style, notes in query_variants:
                if query_text in seen_queries:
                    continue
                seen_queries.add(query_text)
                candidates.append(
                    {
                        "id": _normalise_case_id(f"hybrid {doc['title']} {idx} {query_style or 'base'}"),
                        "bucket": "hybrid-expected",
                        "query": query_text,
                        "relevant_paths": [doc["path"]],
                        "filters": {},
                        "source_path": doc["path"],
                        "query_style": query_style,
                        "title_overlap_ratio": _title_overlap_ratio(query_text, doc["title"]),
                        "notes": notes,
                    }
                )
    return candidates


def _mine_cluster_candidates(index):
    docs = index.get("documents", [])
    by_parent = defaultdict(list)
    by_key = {}
    for doc in docs:
        if doc.get("key"):
            by_key[f"{doc.get('type')}/{doc.get('key')}"] = doc
        if doc.get("parent"):
            by_parent[doc["parent"]].append(doc)

    candidates = []
    for parent_key, children in by_parent.items():
        if children and _should_skip_doc(children[0], bucket="cluster-expected"):
            continue
        if len(children) < 2:
            continue
        parent_doc = by_key.get(parent_key)
        if parent_doc and _should_skip_doc(parent_doc, bucket="cluster-expected"):
            continue
        relevant = []
        if parent_doc:
            relevant.append(parent_doc["path"])
            query = parent_doc["title"].lower()
        else:
            query = children[0]["title"].lower()
        relevant.extend(child["path"] for child in children[:2])
        candidates.append(
            {
                "id": _normalise_case_id(f"cluster {query}"),
                "bucket": "cluster-expected",
                "query": query,
                "relevant_paths": relevant[:3],
                "filters": {},
                "source_path": parent_doc["path"] if parent_doc else children[0]["path"],
                "notes": f"Parent-linked cluster for {query}.",
            }
        )
    return candidates


def _mine_filter_candidates(index):
    docs = index.get("documents", [])
    by_title = defaultdict(list)
    for doc in docs:
        by_title[(doc.get("title") or "").lower()].append(doc)

    candidates = []
    for title_key, group in by_title.items():
        if any(_should_skip_doc(doc, bucket="filter-sensitive") for doc in group):
            continue
        if len(group) < 2:
            continue
        type_values = {doc.get("type") for doc in group if doc.get("type")}
        status_values = {doc.get("status") for doc in group if doc.get("status")}
        if len(type_values) < 2 and len(status_values) < 2:
            continue
        query = group[0]["title"]
        for doc in group:
            if len(type_values) >= 2 and doc.get("type"):
                candidates.append(
                    {
                        "id": _normalise_case_id(f"filter {query} {doc['type']}"),
                        "bucket": "filter-sensitive",
                        "query": query,
                        "relevant_paths": [doc["path"]],
                        "filters": {"type": doc["type"]},
                        "source_path": doc["path"],
                        "notes": f"Type-filtered ambiguity on {query}.",
                    }
                )
            elif len(status_values) >= 2 and doc.get("status"):
                candidates.append(
                    {
                        "id": _normalise_case_id(f"filter {query} {doc['status']}"),
                        "bucket": "filter-sensitive",
                        "query": query,
                        "relevant_paths": [doc["path"]],
                        "filters": {"status": doc["status"]},
                        "source_path": doc["path"],
                        "notes": f"Status-filtered ambiguity on {query}.",
                    }
                )
    return candidates


def mine_candidates(
    vault_root,
    index,
    *,
    semantic_strategy=SEMANTIC_STRATEGY_LOCAL,
    semantic_seed_file=None,
    hybrid_seed_file=None,
):
    candidates = []
    corpus_vocab = _build_filtered_corpus_vocab(index)
    docs_by_path = {doc.get("path"): doc for doc in index.get("documents", [])}
    candidates.extend(_mine_lexical_candidates(index))
    semantic_candidates = []
    semantic_candidates.extend(_mine_link_context_semantic_candidates(vault_root, index))
    semantic_candidates.extend(
        _mine_semantic_candidates(
            vault_root,
            index,
            semantic_strategy=semantic_strategy,
            corpus_vocab=corpus_vocab,
        )
    )
    if semantic_seed_file:
        semantic_candidates.extend(_load_semantic_seed_candidates(semantic_seed_file))
    candidates.extend(
        _annotate_semantic_candidate(candidate, corpus_vocab, docs_by_path)
        for candidate in semantic_candidates
    )
    hybrid_candidates = _mine_hybrid_candidates(vault_root, index)
    if hybrid_seed_file:
        hybrid_candidates.extend(
            _annotate_semantic_candidate(candidate, corpus_vocab, docs_by_path)
            for candidate in _load_hybrid_seed_candidates(hybrid_seed_file)
        )
    candidates.extend(hybrid_candidates)
    candidates.extend(_mine_cluster_candidates(index))
    candidates.extend(_mine_filter_candidates(index))
    return candidates


def construct_fixture(
    vault_root,
    *,
    fixture_out,
    audit_out=None,
    targets=None,
    index=None,
    config=None,
    doc_embeddings=None,
    embeddings_meta=None,
    query_encoder=None,
    semantic_strategy=SEMANTIC_STRATEGY_LOCAL,
    semantic_seed_file=None,
    hybrid_seed_file=None,
):
    vault_root = Path(vault_root)
    targets = {**DEFAULT_TARGETS, **(targets or {})}
    fixture_out = Path(fixture_out)
    if audit_out is None:
        fixture_name = fixture_out.name
        if fixture_name.endswith(".json"):
            audit_name = fixture_name[:-5] + ".audit.json"
        else:
            audit_name = fixture_name + ".audit.json"
        audit_out = fixture_out.with_name(audit_name)
    else:
        audit_out = Path(audit_out)

    if index is None:
        index = si.load_index(vault_root)
    if config is None:
        config = _retrieval_embeddings.load_config_best_effort(vault_root)

    semantic_available, semantic_error = _mode_available(
        vault_root,
        "semantic",
        config=config,
        doc_embeddings=doc_embeddings,
        embeddings_meta=embeddings_meta,
    )
    if semantic_available and (doc_embeddings is None or embeddings_meta is None):
        doc_embeddings, embeddings_meta = _retrieval_embeddings.load_doc_embeddings(vault_root)
    if semantic_available and query_encoder is None:
        query_encoder = _retrieval_embeddings.get_query_encoder()

    candidates = mine_candidates(
        vault_root,
        index,
        semantic_strategy=semantic_strategy,
        semantic_seed_file=semantic_seed_file,
        hybrid_seed_file=hybrid_seed_file,
    )
    candidates = _prune_candidates_for_audit(candidates)
    audits = [
        _audit_candidate(
            index,
            vault_root,
            candidate,
            config=config,
            doc_embeddings=doc_embeddings,
            embeddings_meta=embeddings_meta,
            query_encoder=query_encoder,
            semantic_available=semantic_available,
        )
        for candidate in candidates
    ]
    _annotate_semantic_variant_diagnostics(audits)
    fixture_cases, summary = _select_cases(audits, targets)
    fixture = _build_fixture(vault_root.name, fixture_cases)

    audit = {
        "generated_at": now_iso(),
        "vault_root": str(vault_root),
        "fixture_out": str(fixture_out),
        "audit_out": str(audit_out),
        "targets": targets,
        "semantic_available": semantic_available,
        "semantic_error": semantic_error,
        "semantic_strategy": semantic_strategy,
        "semantic_seed_file": str(semantic_seed_file) if semantic_seed_file else None,
        "hybrid_seed_file": str(hybrid_seed_file) if hybrid_seed_file else None,
        "summary": summary,
        "candidate_count": len(audits),
        "admitted_case_count": len(fixture_cases),
        "candidates": audits,
    }

    safe_write_json(str(fixture_out), fixture)
    safe_write_json(str(audit_out), audit)

    return {
        "fixture_out": str(fixture_out),
        "audit_out": str(audit_out),
        "fixture_case_count": len(fixture_cases),
        "summary": summary,
        "semantic_available": semantic_available,
        "semantic_error": semantic_error,
        "semantic_strategy": semantic_strategy,
        "semantic_seed_file": str(semantic_seed_file) if semantic_seed_file else None,
        "hybrid_seed_file": str(hybrid_seed_file) if hybrid_seed_file else None,
    }


def parse_args(argv):
    fixture_out = None
    audit_out = None
    vault_arg = None
    json_mode = False
    semantic_strategy = SEMANTIC_STRATEGY_LOCAL
    semantic_seed_file = None
    hybrid_seed_file = None
    targets = dict(DEFAULT_TARGETS)

    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg == "--fixture-out" and i + 1 < len(argv):
            fixture_out = argv[i + 1]
            i += 2
        elif arg == "--audit-out" and i + 1 < len(argv):
            audit_out = argv[i + 1]
            i += 2
        elif arg == "--vault" and i + 1 < len(argv):
            vault_arg = argv[i + 1]
            i += 2
        elif arg == "--target-lexical" and i + 1 < len(argv):
            targets["lexical-expected"] = int(argv[i + 1])
            i += 2
        elif arg == "--target-semantic" and i + 1 < len(argv):
            targets["semantic-expected"] = int(argv[i + 1])
            i += 2
        elif arg == "--target-hybrid" and i + 1 < len(argv):
            targets["hybrid-expected"] = int(argv[i + 1])
            i += 2
        elif arg == "--target-cluster" and i + 1 < len(argv):
            targets["cluster-expected"] = int(argv[i + 1])
            i += 2
        elif arg == "--target-filter" and i + 1 < len(argv):
            targets["filter-sensitive"] = int(argv[i + 1])
            i += 2
        elif arg == "--json":
            json_mode = True
            i += 1
        elif arg == "--semantic-strategy" and i + 1 < len(argv):
            semantic_strategy = argv[i + 1]
            i += 2
        elif arg == "--semantic-seed-file" and i + 1 < len(argv):
            semantic_seed_file = argv[i + 1]
            i += 2
        elif arg == "--hybrid-seed-file" and i + 1 < len(argv):
            hybrid_seed_file = argv[i + 1]
            i += 2
        else:
            i += 1

    allowed_strategies = (
        SEMANTIC_STRATEGY_LOCAL,
        SEMANTIC_STRATEGY_ASSISTED_ZERO_OVERLAP,
    )
    if semantic_strategy not in allowed_strategies:
        raise ValueError(
            "semantic strategy must be one of: " + ", ".join(allowed_strategies)
        )

    if not fixture_out:
        raise ValueError(
            "Usage: construct_benchmark_fixture.py --fixture-out PATH "
            "[--audit-out PATH] [--vault PATH] [--target-lexical N] "
            "[--target-semantic N] [--target-hybrid N] [--target-cluster N] "
            "[--target-filter N] [--semantic-strategy local|assisted-zero-overlap] "
            "[--semantic-seed-file PATH] [--hybrid-seed-file PATH] [--json]"
        )

    return (
        fixture_out,
        audit_out,
        vault_arg,
        targets,
        json_mode,
        semantic_strategy,
        semantic_seed_file,
        hybrid_seed_file,
    )


def main():
    try:
        (
            fixture_out,
            audit_out,
            vault_arg,
            targets,
            json_mode,
            semantic_strategy,
            semantic_seed_file,
            hybrid_seed_file,
        ) = parse_args(sys.argv)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    vault_root = find_vault_root(vault_arg)
    try:
        result = construct_fixture(
            vault_root,
            fixture_out=fixture_out,
            audit_out=audit_out,
            targets=targets,
            semantic_strategy=semantic_strategy,
            semantic_seed_file=semantic_seed_file,
            hybrid_seed_file=hybrid_seed_file,
        )
    except si.SearchModeUnavailableError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if json_mode:
        print(json.dumps(result, indent=2))
    else:
        print(
            f"Wrote {result['fixture_out']} and {result['audit_out']} "
            f"({result['fixture_case_count']} cases)",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
