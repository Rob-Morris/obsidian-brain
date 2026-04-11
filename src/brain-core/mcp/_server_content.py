from __future__ import annotations

import json
from typing import Literal

import process

from _server_runtime import ServerRuntime


def _fmt_classify(result):
    if result.get("mode") == "context_assembly":
        lines = ["**Classify** → context_assembly (no scoring available)\n"]
        for td in result.get("type_descriptions", []):
            lines.append(f"**{td['key']}** ({td['type']})")
            lines.append(td["description"])
            lines.append("")
        lines.append(result.get("instruction", ""))
        return "\n".join(lines)

    alt_lines = []
    for alt in result.get("alternatives", []):
        alt_lines.append(f"- {alt['key']} ({alt['type']}) — {alt['confidence']}%")

    parts = [f"**Classified** ({result['mode']}) → {result['key']} ({result['confidence']}%)"]
    if result.get("reasoning"):
        parts.append(result["reasoning"])
    if alt_lines:
        parts.append("\nAlternatives:")
        parts.extend(alt_lines)
    return "\n".join(parts)


def _fmt_resolve(result):
    if result.get("action") == "error":
        return None

    action = result["action"]
    if action == "create":
        return f"**Resolve** → create {result['key']}: {result['title']}\n{result['reasoning']}"
    if action == "update":
        return f"**Resolve** → update {result['target_path']}\n{result['reasoning']}"
    if action == "ambiguous":
        lines = [f"**Resolve** → ambiguous ({len(result.get('candidates', []))} candidates)"]
        lines.append(result["reasoning"])
        lines.append("\nCandidates:")
        for c in result.get("candidates", []):
            lines.append(f"- {c}")
        return "\n".join(lines)
    return json.dumps(result, indent=2)


def _fmt_ingest(result):
    action = result.get("action_taken")
    if action == "created":
        return f"**Ingested** → created {result['type']}: {result['path']}"
    if action == "updated":
        return f"**Ingested** → updated {result['path']}"
    if action == "ambiguous":
        lines = ["**Ingest paused** — needs decision"]
        if result.get("resolution", {}).get("candidates"):
            lines.append("\nCandidates:")
            for c in result["resolution"]["candidates"]:
                lines.append(f"- {c}")
        return "\n".join(lines)
    if action == "needs_classification":
        return _fmt_classify(result.get("classification", {}))
    if action == "error":
        return None
    return json.dumps(result, indent=2)


def handle_brain_process(
    operation: Literal["classify", "resolve", "ingest"],
    content: str,
    type: str | None,
    title: str | None,
    mode: Literal["auto", "embedding", "bm25_only", "context_assembly"],
    runtime: ServerRuntime,
):
    runtime.check_version_drift()
    runtime.ensure_router_fresh()
    runtime.ensure_index_fresh()
    runtime.ensure_embeddings_fresh()

    denied = runtime.enforce_profile("brain_process")
    if denied:
        return denied

    state = runtime.get_state()
    if state.router is None or state.vault_root is None:
        return runtime.fmt_error("server not initialized")

    if operation == "classify":
        try:
            result = process.classify_content(
                state.router,
                state.vault_root,
                content,
                index=state.index,
                type_embeddings=state.type_embeddings,
                type_embeddings_meta=state.embeddings_meta,
                mode=mode,
            )
            return _fmt_classify(result)
        except OSError as e:
            return runtime.fmt_error(str(e))

    if operation == "resolve":
        if not type or not title:
            return runtime.fmt_error("resolve requires type and title parameters")
        try:
            result = process.resolve_content(
                state.router,
                state.vault_root,
                type,
                title,
                content=content,
                index=state.index,
                doc_embeddings=state.doc_embeddings,
                doc_embeddings_meta=state.embeddings_meta,
            )
            if result.get("action") == "error":
                return runtime.fmt_error(result["reasoning"])
            return _fmt_resolve(result)
        except (ValueError, OSError) as e:
            return runtime.fmt_error(str(e))

    if operation == "ingest":
        try:
            result = process.ingest_content(
                state.router,
                state.vault_root,
                content,
                title=title,
                type_hint=type,
                index=state.index,
                type_embeddings=state.type_embeddings,
                type_embeddings_meta=state.embeddings_meta,
                doc_embeddings=state.doc_embeddings,
                doc_embeddings_meta=state.embeddings_meta,
            )
            formatted = _fmt_ingest(result)
            if formatted is None:
                return runtime.fmt_error(result.get("message", "Unknown error"))
            if result.get("action_taken") in ("created", "updated") and result.get("path"):
                runtime.mark_index_pending(result["path"], type_hint=result.get("type"))
                runtime.ensure_index_fresh()
            return formatted
        except (ValueError, FileNotFoundError, OSError) as e:
            return runtime.fmt_error(str(e))

    return runtime.fmt_error(
        f"Unknown operation '{operation}'. Valid: classify, resolve, ingest"
    )
