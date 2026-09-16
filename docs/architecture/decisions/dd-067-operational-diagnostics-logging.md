# DD-067: Always-on content-free operational diagnostics logging

**Status:** Implemented (v0.61.0)

## Context

Before this decision the system had exactly one persistent operational log:
the MCP proxy's prose `RotatingFileHandler` file at `.brain/local/mcp-proxy.log`
(~0644, undocumented outside changelogs). The MCP server child — the process
that executes every tool call — persisted nothing; the `_application` layer's
`DiagnosticReporter` port defaulted to an unconfigured stdlib logger whose
output died on stderr; the CLI launcher had no diagnostics port at all. The
sharpest consequence was a broken contract: `internal_error_result` hands the
client a `correlation_id` to quote, but for read-only commands the receipt
store skips `ReceiptState.NONE` and nothing else persisted — the ID resolved
to no record. Meanwhile `BRAIN_LOG_BODIES=1` interleaved raw vault content
into the same prose log the always-on stream used.

The design was modelled on subcortex's daemon-diagnostics approach (bounded
NDJSON, content-free by construction, non-blocking writer) and adapted to
Brain's process model, where several proxies and server children can serve
one vault concurrently and one-shot script/CLI processes execute commands too.

## Decision

One stdlib-only module, `scripts/_common/_operational_log.py`, owns an
always-on, bounded, content-free NDJSON operational log under
`.brain/local/diagnostics/` (0700 directory, 0600 files, fixed destinations —
callers never supply paths):

- **Record contract** (`brain.operational-log/1`): envelope of `schema`, `ts`,
  `run_id`, `seq`, `process`, `pid`, `version`, `event`, with typed per-event
  fields only — no message field exists in the schema. Unknown events and
  fields are rejected; command identifiers follow the canonical command
  grammar; JSON-RPC methods project into a closed protocol vocabulary; and
  phases, outcomes, replay reasons and error classes are closed vocabularies.
  Exceptions reduce to a class (`lock`/`io`/`transport`/`capacity`/`internal`)
  plus the exception type name. `dropped_before` reports in-band any events
  lost since the last accepted record.
- **File layer invariant: a log file is never open outside its family lock.**
  Appends open/write/rotate/close per batch under a per-family sidecar lock
  (`exclusive_file_lock`, bounded 2 s timeout), which makes N-process appends
  safe on POSIX (no stale-handle inode drift across another process's
  rotation) and makes rotation possible at all on Windows (renames fail while
  any process holds the file open). Rotation keeps 2 MiB active + 3 archives:
  a hard 8 MiB per family. `clear` truncates the active file and deletes
  archives; `export` concatenates chronologically, skipping (and noting) a
  family whose lock is busy.
- **Filesystem endpoints fail closed.** Directory components reject symlinks;
  locks, active logs and archives are opened no-follow and verified as the
  same regular file before append, truncation or export. Proxy writability
  probes use unique exclusive files and never remove a shared name.
- **Delivery**: one `OperationalLogger` per daemon process — a single bounded
  1,024-item queue and writer thread serving all of that process's families,
  `put_nowait` with drop counting so a request thread never blocks, a bounded
  `close()` whose abandon flag prevents shutdown races — and a synchronous
  `append_record` for one-shot processes. Reporter routing is by discovery:
  `current_logger()` when installed, else `command.log`.
  Synchronous appends return whether persistence succeeded; failed
  `command.failed` appends also emit the already validated, bounded record on
  stderr. Keeping this fallback at the shared append boundary gives launchers
  and direct scripts the same content-free correlation metadata without a
  second write attempt or a second validation policy. Invalid records receive
  only a generic note; unavailable stderr is ignored. Daemon delivery remains
  asynchronous and best-effort.
  The outer CLI carries at most one schema-validated script failure matching
  the child result's command and `internal_error` correlation ID through its
  local result projection to stderr, including JSON mode. It discards unrelated
  child stderr. The trusted command reporter does not also emit raw tracebacks
  through stdlib logging.
- **Wiring**: proxy (`proxy.log` lifecycle/child/replay events;
  `proxy-rpc.log` `frame.*` pairs keyed by a per-run `frame_seq` surrogate —
  client-chosen JSON-RPC ids are never logged), server (`server.log`
  lifecycle plus `tool.*` spans from the MCP adapter), the command layer
  (`OperationalDiagnosticReporter` as the `compose_local_context` default,
  closing the correlation-id dead end), and the CLI launcher (a diagnostics
  port on `LauncherContext`, reporting into the selected Brain's `command`
  family).
- **Bodies stay opt-in and separate**: `BRAIN_LOG_BODIES=1` (or `true`) routes
  raw wire bodies through the same bounded file layer into
  `debug-bodies.log` (`brain.debug-bodies/1`, 64 KiB per line, in-place
  truncation), flagged on `process.started`. The operational stream is
  content-free unconditionally. `BRAIN_LOG_LEVEL` is retired; the proxy's
  prose logger keeps only its WARNING stderr surface, and the `.brain/local`
  writability check the old file handler provided survives as an explicit
  startup probe.
- **Boundaries stay enforced**: `tests/application/test_import_boundaries.py`
  pins the module to stdlib-plus-`_common` imports, keeping it usable from the
  launcher's system Python before any managed runtime exists. Logging is
  best-effort by contract — lock or I/O failures drop the batch (counted
  in-band) and only persistent failure disables the stream; nothing ever
  unwinds a command.

The user-facing export/clear surface (a `brain.diagnostics` catalogue command,
which also bundles a doctor snapshot into exports and removes the legacy
`mcp-proxy.log`) and an optional `error_code` parameter on the reporter port
are deferred as the design's v2. The legacy `mcp-proxy.log` is left in place
and simply never written again.

## Consequences

Every long-lived Brain process can leave a bounded, machine-readable,
privacy-preserving history; accepted command-failure records carry the exact
correlation ID the client saw; hang diagnosis has frame pairs at the only
layer that sees every frame; and total diagnostics storage is capped per
vault. Because delivery is deliberately best-effort, a returned correlation
ID is not proof that persistence succeeded. The full contract lives in
`docs/functional/diagnostics.md`.
