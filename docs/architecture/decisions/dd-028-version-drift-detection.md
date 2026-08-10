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

The proxy (`proxy.py`) distinguishes exit code 10 from crashes: it restarts immediately with no backoff, then sends a `notifications/tools/list_changed` notification to the MCP client so the client fetches the fresh tool list. Crashes use exponential backoff (0s, 4s, 8s, 16s, 32s). Every child-loss path — reader-thread EOF, main-loop pre-send dead-child detection, `BrokenPipeError` while writing to child stdin, and initial startup failure — funnels into the same restart coordinator, but a dedicated recovery thread owns every backoff sleep and restart attempt. Detection paths only detach the dead child, drain or snapshot in-flight requests, and signal that thread, so the main stdin loop keeps reading and can return `server restarting, please retry` immediately while recovery is still in progress. If the immediate restart fails, the recovery thread falls through to the backoff retry loop rather than entering a limbo state.

`os._exit()` is used rather than `sys.exit()` because `SystemExit` raised inside an MCP tool handler gets wrapped in `BaseExceptionGroup` by anyio task groups, losing the exit code. The MCP SDK's async shutdown then treats it as a normal exit (code 0), causing the proxy to shut down instead of restarting. `os._exit()` bypasses the async stack entirely, ensuring the exit code reaches the proxy.

The proxy tracks in-flight requests (full request objects, not just IDs). Proxy
protocol 2 also binds each granular call, before child dispatch, to the exact
validated initialise header facts: projected tool, canonical command/version,
interface epoch, mutation class and a proxy-owned invocation identifier.

Exit 10 remains the only planned replay path. `_check_version_drift()` runs on
the child side before effects; after restart the proxy replays only when the
replacement header positively retains the accepted protocol, epoch, projected
tool, command identity/version and mutation class. Missing, malformed or
contradictory facts return `interface_changed` with `effects: none`, emit
`notifications/tools/list_changed` and dispatch nothing. Replay depth remains
capped at 1.

Unexpected child loss is separate. A compatible read-only orphan receives one
proxy retry; a second loss returns a proven no-effect retryable transport error.
A mutating orphan is never replayed. After restart the proxy queries the durable
receipt through `invocation.read`; a conclusive receipt reports the privacy-
minimal known outcome, while an absent, invalid or explicitly unknown receipt
returns non-retryable `command_outcome_unknown` with the same queryable
invocation reference. If restart cannot complete, the same read/no-effect or
mutation/unknown distinction is preserved rather than collapsed into a generic
retry-shaped error.

The proxy also detects its own code drift via file-hash comparison (SHA-256) after child restarts, and injects upgrade notes into responses when drift is detected. On Unix, the reader thread uses `select()` with a configurable timeout (default 30s) to detect children that hang without exiting — after 3 consecutive timeouts with in-flight requests, the proxy kills the child and routes that loss through the same restart coordinator. On Windows, anonymous pipe handles cannot be waited on with `select()`, so the reader thread blocks in `readline()` and retains EOF/crash recovery but not timeout-based hang detection. If every restart attempt fails, the proxy marks the session as given up and returns explicit MCP-restart guidance rather than a permanent soft-restarting loop. After give-up, later `tools/call` requests trigger an asynchronous VERSION re-check on the recovery thread; the triggering request still gets the unrecoverable error immediately, and recovery resumes only if `.brain-core/VERSION` changed. If the recovery thread itself crashes, dead-child requests surface a hard `MCP unrecoverable — proxy recovery thread crashed. Restart MCP to recover.` error instead of waiting forever.

## Consequences

- Brain-core upgrades take effect within one tool call — no manual MCP restart needed. The triggering request is transparently replayed, so no client retry is required.
- The proxy remains transport-focused but now owns protocol validation,
  accepted-call identity, bounded read retry and receipt-query orchestration.
  It still owns no command semantics or request translation. File-hash drift
  detection alerts if the proxy itself changed on disk.
- Every tool call pays a cheap disk read for the VERSION file. This is negligible compared to index or vault I/O.
- Version drift is logged as a warning with old and new version strings for auditability.
- On Unix, hung children are detected and killed rather than causing permanent silent hangs. Windows retains child crash/EOF recovery, but not pipe timeout hang detection.
- Persistent restart failure is explicit: once backoff is exhausted, the proxy stops claiming it is still restarting and instead returns a hard failure with MCP-restart guidance.
- Planned drift and unexpected loss have mechanically distinct safety rules;
  mutations can never enter the read retry or planned replay path after an
  unplanned exit.
