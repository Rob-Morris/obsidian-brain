# DD-026: MCP Response Readability — Plain Text over JSON Blobs

**Status:** Implemented (v0.14.4, polished v0.14.5)

## Context

MCP tool results are displayed inline in agent UIs (Claude Code, Cursor, etc.). Tools initially returned `json.dumps({...})` strings. The MCP SDK wraps these in a single `TextContent` block, which clients render as a collapsed JSON blob — readable by the agent but opaque to the human watching the session.

## Decision

Tools return plain text or `list[TextContent]` multi-block responses, not JSON blobs, wherever human readability adds value. Design rules by response type:

- **Confirmations** (`brain_create`, `brain_edit`, simple `brain_action`) → plain text, one line, human-scannable.
- **Content retrieval** (`brain_read` artefact/file) → plain text (already optimal).
- **Lists and search** → multi-block: bold metadata block + results as a readable text list.
- **Structured data** (router dumps, compliance arrays) → JSON only where structure genuinely aids comprehension.
- **Errors** → `"Error: {message}"` plain text, never a JSON wrapper.
- **Session** → unchanged compact JSON (agent-consumed, not human-read).

This is a presentation concern only — underlying scripts still return dicts/lists.

## Consequences

- Tool outputs are readable to the human observing an agent session without expanding JSON blobs.
- Tests that called `json.loads()` on tool results required updating to match plain-text format.
- The FastMCP SDK's `list[TextContent]` return produces multiple independently renderable content blocks.
- No changes to scripts, CLI, or compiled router — the MCP server layer handles formatting.
