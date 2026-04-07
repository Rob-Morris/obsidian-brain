# DD-006: Mobile is First-Class

**Status:** Accepted

## Context

Obsidian runs on iOS and Android, where Python is unavailable, no shell is accessible, and MCP servers cannot run. A design that requires Python or a local server would silently degrade on mobile — vaults would open but agent-assistance features would not function.

## Decision

Mobile is treated as a first-class runtime target. No feature may require Python or a local process as a hard dependency. The lean router (DD-012) and wikilink-based agent reading flow give agents a fully functional path on mobile. Scripts and MCP are enhancements over this baseline, not replacements for it.

## Consequences

- Every agent-facing feature must have a mobile-compatible fallback.
- The lean router and wikilink traversal are permanently maintained, not deprecated in favour of the compiled router.
- Python scripts and MCP tools are the fast/rich path; markdown traversal is the universal path.
- Feature design always asks: "how does this work on mobile?"
