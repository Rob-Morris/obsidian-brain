"""MCP text projections of a structured result envelope.

Wire dicts stay stdlib-only so the proxy can emit them without importing the
MCP SDK on the fail-fast restart path.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict

from _bootstrap.result_metadata import Delivery


def result_text_wire(
    concise_text: str,
    structured_content: Mapping[str, object],
) -> list[dict[str, object]]:
    """JSON-RPC text blocks: envelope first, human one-liner second."""

    envelope = json.dumps(
        structured_content,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return [
        {
            "type": "text",
            "text": envelope,
            "annotations": {"audience": ["assistant"]},
        },
        {
            "type": "text",
            "text": concise_text,
            "annotations": {"audience": ["user"]},
        },
    ]


def result_text_content(
    concise_text: str,
    structured_content: Mapping[str, object],
):
    """Typed MCP text blocks for FastMCP CallToolResult."""

    from mcp.types import Annotations, TextContent

    return [
        TextContent(
            type="text",
            text=str(block["text"]),
            annotations=Annotations(audience=list(block["annotations"]["audience"])),
        )
        for block in result_text_wire(concise_text, structured_content)
    ]


CANONICAL_TOOL_RESULT_BYTES = 8000
ENCODED_TOOL_RESULT_BYTES = 32768


def canonical_tool_result_size(envelope: Mapping[str, object]) -> int:
    """Size of the assistant envelope, independent of transport correlation."""
    return len(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def encoded_tool_result_size(result: Mapping[str, object]) -> int:
    """Match the proxy writer's default ASCII JSON encoder, excluding RPC framing."""
    return len(json.dumps(result).encode("utf-8"))


def tool_result_fits(result: Mapping[str, object]) -> bool:
    envelope = result.get("structuredContent")
    if not isinstance(envelope, Mapping):
        envelope = result
    return (canonical_tool_result_size(envelope) <= CANONICAL_TOOL_RESULT_BYTES
            and encoded_tool_result_size(result) <= ENCODED_TOOL_RESULT_BYTES)


def delivery_descriptor(**overrides: object) -> dict[str, object]:
    """Transport-local descriptor; unknown counts never imply a fabricated page map."""
    descriptor = asdict(Delivery(status="complete", availability="available", backing="inline"))
    descriptor.update(overrides)
    return descriptor


def has_catalogue_notice(result: Mapping[str, object]) -> bool:
    """Recognise the proxy's actionable observation, including its bounded summary."""
    envelope = result.get("structuredContent")
    warnings = envelope.get("warnings", []) if isinstance(envelope, Mapping) else []
    warnings = warnings if isinstance(warnings, list) else []
    return any(isinstance(warning, Mapping)
               and warning.get("code") == "follow_up_required"
               and isinstance(warning.get("message"), str)
               and warning["message"].startswith("Brain MCP transport observation")
               for warning in warnings)


def delivery_unavailable_projection(result: Mapping[str, object]) -> dict[str, object]:
    """Bound delivery without rewriting execution truth or losing issuance controls.

    Only the caller receives result-session credentials. They are never hashed,
    echoed in diagnostics, or treated as retained business-result content here.
    """
    from hashlib import sha256
    from _bootstrap.result_outcome import source_outcome_warnings

    original = result.get("structuredContent")
    original = original if isinstance(original, Mapping) else {}
    message = ("Final decorated result exceeds the delivery budget. "
               "Read the retained result reference where available; do not replay the command.")
    delivery = original.get("delivery")
    delivery = delivery if isinstance(delivery, Mapping) else {}
    capacity_limits = None
    capacity_exceeded = delivery.get("reason") == "capacity_exceeded"
    representation_too_large = delivery.get("reason") == "representation_too_large"
    if capacity_exceeded:
        from _bootstrap.result_capacity import capacity_recovery
        capacity_limits, message = capacity_recovery(delivery.get("capacity_limits"))
        message += " Do not rerun a mutation to recover output."
    elif representation_too_large:
        from _bootstrap.result_capacity import representation_recovery
        message = representation_recovery()
    failure_reason = ("capacity_exceeded" if capacity_exceeded else "representation_too_large"
                      if representation_too_large else "delivery_unavailable")
    unavailable = []

    def bounded(value, canonical=512, encoded=2048):
        return (canonical_tool_result_size({"value": value}) <= canonical
                and encoded_tool_result_size({"value": value}) <= encoded)

    envelope = {}
    for key in ("schema", "command", "tool", "command_version", "status"):
        if key in original:
            if bounded(original[key], 256, 512):
                envelope[key] = original[key]
            else:
                unavailable.append(key)
    envelope.setdefault("schema", "brain.command-result/1")
    # A malformed status is a metadata failure, never inferred successful execution.
    if not isinstance(envelope.get("status"), str):
        envelope["status"] = "error"
        if "status" in original and "status" not in unavailable:
            unavailable.append("status")
    for key in ("result_ref", "outcome_reference", "committed_effects",
                "committed_effect_summary"):
        if key in original:
            envelope[key] = original[key]
    session = original.get("result_session")
    if session is not None:
        if (isinstance(session, Mapping) and isinstance(session.get("credential"), str)
                and len(session["credential"]) <= 512 and bounded(session, 3072, 8192)):
            envelope["result_session"] = dict(session)
        else:
            unavailable.append("result_session")
    error = original.get("error")
    if isinstance(error, Mapping):
        envelope["error"] = {key: error[key] for key in ("code", "effects", "outcome_reference") if key in error}
    outcome = original.get("outcome")
    if isinstance(outcome, Mapping):
        envelope["outcome"] = {key: outcome[key] for key in ("execution", "receipt") if key in outcome}
    envelope["result"] = None
    warnings = source_outcome_warnings(original.get("warnings"))
    if has_catalogue_notice(result) and not has_catalogue_notice({"structuredContent": {"warnings": warnings}}):
        warnings = [*warnings, {"code": "follow_up_required", "message": (
            "Brain MCP transport observation: call brain_proxy_status({}) and follow its "
            "next_action before host discovery. Do not replay this application call.")}]
    envelope["warnings"] = warnings
    envelope["delivery"] = delivery_descriptor(**{**delivery,
        "status": "unavailable", "availability": "unavailable",
        "reason": failure_reason, "message": message})
    envelope["delivery"].pop("capacity_limits", None)
    if capacity_limits is not None:
        envelope["delivery"]["capacity_limits"] = capacity_limits
    # The original stored layout remains authoritative; this response serves no page.
    envelope["delivery"].update(continuation=None, outside_continuation=None)

    def project():
        if unavailable:
            envelope["metadata_unavailable"] = list(unavailable)
        return {"content": result_text_wire(message, envelope),
                "structuredContent": envelope, "isError": result.get("isError", False)}

    candidate = project()
    if not tool_result_fits(candidate):
        effects = envelope.pop("committed_effects", None)
        if effects is not None:
            envelope["committed_effect_summary"] = {
                "count": len(effects) if isinstance(effects, (list, dict)) else None,
                "sha256": sha256(json.dumps(effects, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest(),
                "detail": "Read full committed effects from the original retained outcome where available.",
            }
        candidate = project()
    if not tool_result_fits(candidate):
        result_id = delivery.get("result_id")
        if not bounded(result_id):
            result_id = None
            unavailable.append("delivery.result_id")
        envelope["delivery"] = delivery_descriptor(
            status="unavailable", availability="unavailable", result_id=result_id,
            reason=failure_reason,
            message=message + " Mandatory metadata could not be delivered.")
        if capacity_limits is not None:
            envelope["delivery"]["capacity_limits"] = capacity_limits
        candidate = project()
        # Identity, execution status, remediation and bounded issuance controls
        # have reserved space. Only optional outcome metadata may be removed.
        keys = sorted((key for key in ("outcome", "error", "result_ref", "outcome_reference",
                                      "committed_effect_summary") if key in envelope),
                      key=lambda key: canonical_tool_result_size({key: envelope[key]}), reverse=True)
        for key in keys:
            if tool_result_fits(candidate):
                break
            del envelope[key]
            unavailable.append(key)
            candidate = project()
    return candidate


def guard_tool_result(result: Mapping[str, object]) -> dict[str, object]:
    """Pure final guard, reusable by adapters before their own writes."""
    return dict(result) if tool_result_fits(result) else delivery_unavailable_projection(result)


_DEBUG_CONTROL_REDACTED = "[redacted transport control]"
_DEBUG_BODY_UNAVAILABLE = "[body omitted: invalid JSON transport frame]"


def redact_debug_wire_body(raw: bytes) -> str:
    """Redact reserved result-session controls only in the diagnostic copy.

    Unparseable frames cannot be safely separated into controls and business
    content. Never fall back to logging their original bytes or parser errors.
    """
    try:
        text = raw.decode("utf-8")
        frame = json.loads(text)
    except (UnicodeError, ValueError, RecursionError):
        return _DEBUG_BODY_UNAVAILABLE
    if not isinstance(frame, dict):
        return _DEBUG_BODY_UNAVAILABLE
    changed = False
    params = frame.get("params")
    arguments = params.get("arguments") if isinstance(params, dict) else None
    if isinstance(arguments, dict) and "brain_session" in arguments:
        # Replace invalid control values too; their type does not make them safe.
        arguments["brain_session"] = _DEBUG_CONTROL_REDACTED
        changed = True

    def redact_issuance(envelope):
        if not isinstance(envelope, dict) or "result_session" not in envelope:
            return False
        session = envelope["result_session"]
        if isinstance(session, dict):
            if "credential" not in session:
                return False
            session["credential"] = _DEBUG_CONTROL_REDACTED
        else:
            envelope["result_session"] = _DEBUG_CONTROL_REDACTED
        return True

    result = frame.get("result")
    if isinstance(result, dict):
        structured = result.get("structuredContent")
        issuance_present = isinstance(structured, dict) and "result_session" in structured
        session = structured.get("result_session") if issuance_present else None
        original_credential = session.get("credential") if isinstance(session, dict) else session
        changed = redact_issuance(structured) or changed
        content = result.get("content")
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict) or block.get("type") != "text" or not isinstance(block.get("text"), str):
                continue
            annotations = block.get("annotations")
            audience = annotations.get("audience", []) if isinstance(annotations, dict) else []
            assistant = isinstance(audience, list) and "assistant" in audience
            try:
                envelope = json.loads(block["text"])
            except (ValueError, RecursionError):
                if issuance_present and (assistant or block["text"].lstrip().startswith("{")):
                    block["text"] = _DEBUG_BODY_UNAVAILABLE
                    changed = True
                continue
            brain_envelope = (isinstance(envelope, dict)
                              and isinstance(envelope.get("schema"), str)
                              and envelope["schema"].startswith("brain."))
            text_session = envelope.get("result_session") if isinstance(envelope, dict) else None
            text_credential = text_session.get("credential") if isinstance(text_session, dict) else text_session
            duplicate = (issuance_present and isinstance(envelope, dict)
                         and "result_session" in envelope and text_credential == original_credential)
            if (assistant or brain_envelope or duplicate) and redact_issuance(envelope):
                block["text"] = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
                changed = True
    return json.dumps(frame, ensure_ascii=False, separators=(",", ":")) if changed else text.strip()
