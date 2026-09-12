# Operational Diagnostics

Brain keeps an always-on, bounded, content-free operational log so failures in
the long-lived MCP processes and the command layer are diagnosable after the
fact. Internal errors return a `correlation_id` that is also submitted to this
best-effort stream; when the record is accepted and persisted, operators can
use that identifier to correlate the failure. Queue pressure, lock contention,
I/O failure or abrupt process termination can legitimately leave no persistent
record. Design: DD-067.

## Where

All files live under `.brain/local/diagnostics/` in the selected vault
(directory `0700`, files `0600`, gitignored in template-derived vaults).
Destinations are fixed by the writer — callers never supply paths.

| Family | File | Written by |
|---|---|---|
| `proxy` | `proxy.log` | every MCP proxy process: lifecycle, child management, replay/interface events |
| `proxy-rpc` | `proxy-rpc.log` | every MCP proxy process: `frame.forwarded`/`frame.completed` pairs for each forwarded JSON-RPC request |
| `server` | `server.log` | every MCP server child: lifecycle plus `tool.started`/`tool.handled` spans and its own `command.failed` records |
| `command` | `command.log` | one-shot processes (direct scripts, CLI subprocesses, launcher-owned commands): `command.failed` records |
| `debug-bodies` | `debug-bodies.log` | the proxy, only when `BRAIN_LOG_BODIES` is enabled (below) |

Each family is capped at one 2 MiB active file plus three 2 MiB archives
(`.1` newest → `.3` oldest) — a hard 8 MiB ceiling per family. Multiple
concurrent processes append safely: every append happens under the family's
sidecar lock (`<family>.lock`) and records carry `pid` and a per-run `run_id`.

## Record shape

One NDJSON record per line, schema `brain.operational-log/1`:
`schema`, `ts` (epoch ms), `run_id`, `seq`, `process`, `pid`, `version`,
`event`, plus typed per-event fields (counts, durations, sanitised
identifiers, closed-vocabulary categories). `dropped_before` appears only when
events were lost since the last accepted record (queue overload or failed
writes). There is no free-form message field: identifiers are restricted to
96 chars of ASCII alphanumerics plus `.-_` (anything else becomes the literal
`invalid`), and errors are reduced to a class (`lock`, `io`, `transport`,
`capacity`, `internal`) plus the exception type name. Request parameters,
vault content, paths and exception text are never recorded.

The record layer rejects unknown events and fields. Each event has a closed
field schema: command identifiers use the canonical noun/verb grammar;
phases, outcomes, error classes, replay reasons and resolution sources use
closed vocabularies; and unrecognised or malformed JSON-RPC methods are stored
only as `other`.

Readers must skip lines with an unknown `schema` value, tolerate a missing
`process.exited` after `SIGKILL`/`os._exit`, and tolerate unpaired spans
(the routine version-drift restart exits inside a handler). When the final
record is persisted, the MCP server records exit code `0` only after its serve
loop returns normally; an escaping failure records a non-success code while
the original exception continues to the process boundary.

## Logging can never break a command

Enqueueing is non-blocking (bounded queue, drops counted in-band); writer
failures degrade to a no-op stream with a one-line stderr note; a diagnostics
failure at startup never blocks serving. Separately, the proxy still verifies
`.brain/local` is writable at startup and enters degraded mode when it is not —
that gate is about vault-local state (receipts, access, runtime status), not
logging.

Diagnostics directory components, lock files, active logs and archives are
refused when they are symlinks or non-regular files. Opens verify the endpoint
before any append, truncation or export read so a hostile vault-local link
cannot redirect diagnostics outside the vault.

## `BRAIN_LOG_BODIES` — opt-in wire capture

Set `BRAIN_LOG_BODIES=1` (or `true`) in the MCP proxy's environment to
additionally capture the raw JSON body of every forwarded MCP message to
`debug-bodies.log` (schema `brain.debug-bodies/1`, same rotation bounds,
64 KiB per line with in-place truncation). This deliberately records vault
content to a local `0600` file — it is a dev-machine diagnostic, off by
default, and its presence is flagged on the operational stream's
`process.started` record (`bodies_enabled: true`). Toggling requires an MCP
restart. Nothing ever leaves the machine.

The former `BRAIN_LOG_LEVEL` variable is retired: the operational stream is
single-level and always on, and the proxy's prose logger now writes only
WARNING-and-above to stderr.
