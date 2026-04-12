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

This assumes `.brain-core/` is a version-bound unit: upgrades replace the engine as one atomic surface. The system does not attempt to support mixed-version execution where some files come from the old release and others from the new one.

The proxy (`proxy.py`) distinguishes exit code 10 from crashes: it restarts immediately with no backoff, then sends a `notifications/tools/list_changed` notification to the MCP client so the client fetches the fresh tool list. Crashes use exponential backoff (0s, 4s, 8s, 16s, 32s). If the immediate restart fails, the proxy falls through to the backoff retry loop rather than entering a limbo state.

`os._exit()` is used rather than `sys.exit()` because `SystemExit` raised inside an MCP tool handler gets wrapped in `BaseExceptionGroup` by anyio task groups, losing the exit code. The MCP SDK's async shutdown then treats it as a normal exit (code 0), causing the proxy to shut down instead of restarting. `os._exit()` bypasses the async stack entirely, ensuring the exit code reaches the proxy.

The proxy tracks in-flight requests (full request objects, not just IDs) and handles them on child exit: for version drift, saved requests are replayed to the new child so the client gets a success response; for crashes, error responses are sent to the client. Replay is safe because `_check_version_drift()` runs before any side effects. Replay depth is capped at 1 to prevent loops.

The proxy also detects its own code drift via file-hash comparison (SHA-256) after child restarts, and injects upgrade notes into responses when drift is detected. The reader thread uses `select()` with a configurable timeout (default 30s) to detect children that hang without exiting — after 3 consecutive timeouts with in-flight requests, the proxy kills and restarts the child.

## Consequences

- Brain-core upgrades take effect within one tool call — no manual MCP restart needed. The triggering request is transparently replayed, so no client retry is required.
- The proxy must remain running across upgrades; it is deliberately kept thin (no business logic) so it rarely needs replacing. File-hash drift detection alerts if the proxy itself changed on disk.
- Every tool call pays a cheap disk read for the VERSION file. This is negligible compared to index or vault I/O.
- Version drift is logged as a warning with old and new version strings for auditability.
- Hung children are detected and killed rather than causing permanent silent hangs.
