# DD-027: MCP Tool Resilience Conventions

**Status:** Accepted

## Context

The MCP server is a long-running process serving multiple agents across unpredictable vault states. Corrupted JSON caches, missing files, and unexpected data shapes are normal operating conditions, not exceptional ones. Before this decision was codified, an unhandled exception in a tool handler would crash the server and orphan any active agent sessions.

## Decision

Every tool handler follows a mandatory three-layer exception strategy:

1. **Preventive type guards** — after any deserialization (`json.load`, `yaml.safe_load`), `isinstance()` checks confirm the loaded data is the expected type before accessing keys. Corrupted caches can parse as valid JSON of the wrong type.
2. **Inner domain catches** — `try/except` around specific operations, catching expected failures (`ValueError`, `KeyError`, `FileNotFoundError`) with actionable error messages via `_fmt_error()`.
3. **Outer catch-all** — every tool's top-level handler has a final `except Exception` that logs the full traceback and returns a generic `_fmt_error()`. This is the safety net; it should never be the primary error path.

All error returns use `_fmt_error(msg)` which produces `CallToolResult(isError=True)`. Never raise exceptions to signal errors; never return raw `{"error": ...}` dicts. Tool parameters with fixed value sets use `Literal["a", "b"]` annotations so agents see valid values at discovery time.

## Consequences

- The server survives unexpected vault states without crashing.
- Omitting the outer catch-all on any tool is a bug, even when inner catches seem complete.
- Error messages are consistent and MCP-client-renderable via the `isError` flag.
- All shipped MCP tools must conform to the same three-layer exception strategy; tool-count changes do not create exceptions to the rule.
