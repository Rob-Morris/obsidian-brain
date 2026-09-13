"""MCP text projections of a structured result envelope.

Wire dicts stay stdlib-only so the proxy can emit them without importing the
MCP SDK on the fail-fast restart path.
"""

from __future__ import annotations

import json
from collections.abc import Mapping


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
