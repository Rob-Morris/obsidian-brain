# DD-003: CLI Delegates to Scripts, Never Contains Unique Logic

**Status:** Accepted

## Context

The MCP server and any CLI entry points both need to perform vault operations. Without a clear boundary, logic can drift into the CLI layer, creating two implementations that diverge over time and can't be called programmatically without spawning a subprocess.

## Decision

The CLI is a thin dispatcher only. Every vault operation lives as an importable function in a script under `.brain-core/scripts/`. CLI entry points call those functions and format output — they never contain unique logic that isn't accessible via import.

## Consequences

- The MCP server imports the same functions as the CLI — one implementation, two access paths.
- Agents without MCP can call scripts directly and get identical behaviour.
- New operations are always implemented in scripts first; CLI and MCP exposure follow automatically.
- Cold-start cost applies to direct script calls (loading JSON from disk) vs. the MCP server's in-memory cache, but the logic is identical.
- Testing scripts tests both the CLI and MCP paths simultaneously.
