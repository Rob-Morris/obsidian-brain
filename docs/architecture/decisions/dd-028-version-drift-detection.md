# DD-028: Version drift detection

**Status:** Implemented

## Context

The MCP server is a long-running process launched by a proxy. When `.brain-core/` is upgraded (e.g., via `upgrade.py`), the server process continues running stale code while new Python modules sit on disk. Continuing to run stale code after an upgrade risks subtle bugs and inconsistencies between the loaded router, index, and the new logic.

Three approaches were considered:

1. **`importlib.reload()`** — Python module reloading is unreliable for complex packages; it doesn't re-execute module-level state, doesn't reload C extensions, and can leave partially-updated objects in memory.
2. **In-process restart** — Resetting all globals and re-running startup is fragile and would require careful ordering to avoid serving requests during reinitialisation.
3. **Exit and let the proxy restart** — Clean separation: the server exits with a distinguished code; the proxy detects it and spawns a fresh process with new code.

## Decision

On every MCP tool call, `_check_version_drift()` reads `.brain-core/VERSION` from disk and compares it to `_loaded_version` (recorded at startup). If they differ, the server calls `os._exit(_EXIT_VERSION_DRIFT)` (exit code 10) after flushing logs.

The proxy (`proxy.py`) distinguishes exit code 10 from crashes: it restarts immediately with no backoff, then sends a `notifications/tools/list_changed` notification to the MCP client so the client fetches the fresh tool list. Crashes use exponential backoff (0s, 4s, 8s, 16s, 32s). If the immediate restart fails, the proxy falls through to the backoff retry loop rather than entering a limbo state.

`os._exit()` is used rather than `sys.exit()` because `SystemExit` raised inside an MCP tool handler gets wrapped in `BaseExceptionGroup` by anyio task groups, losing the exit code. The MCP SDK's async shutdown then treats it as a normal exit (code 0), causing the proxy to shut down instead of restarting. `os._exit()` bypasses the async stack entirely, ensuring the exit code reaches the proxy. The proxy also tracks in-flight request IDs and sends JSON-RPC error responses when the child dies mid-request, preventing indefinite client hangs.

## Consequences

- Brain-core upgrades take effect within one tool call — no manual MCP restart needed.
- The proxy must remain running across upgrades; it is deliberately kept thin (no business logic) so it rarely needs replacing.
- Every tool call pays a cheap disk read for the VERSION file. This is negligible compared to index or vault I/O.
- Version drift is logged as a warning with old and new version strings for auditability.
