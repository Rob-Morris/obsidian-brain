# DD-091: Invocation result navigation

**Status:** Accepted

## Context

MCP hosts impose different response limits. Legacy command cursors describe
source windows, not delivery of an already-produced output. Reexecuting a
mutation to recover an omitted result is unsafe. Agents sharing an account
also need independent retained-result slots.

## Decision

Keep the latest invocation per command, private result session and selected
Brain in a disk-backed store without time expiry. A server-issued credential
identifies a namespace; it grants no command permission or execution consent.
CLI jobs inherit a private context; standalone CLI calls can resume through a
protected credential file. Superseded invocation IDs never alias new results.

Apply one delivery descriptor and bounded page per response, including page-zero
get-all intent. Report exact physical page counts and selected/outside extents.
Return ordinary text once in `result.content`, using UTF-8 byte paging by
default and an explicitly requested line mode. Both modes preserve Unicode
character boundaries and report character ranges; oversized lines may span
pages under the response caps. Queries page records and structured roots page
fields, with explicit detail references for oversized structured values. Honour
explicit page sizes within each mode's bounds and apply final response caps
independently. Preserve originating status and committed effects
when delivery fails. Guard the final canonical and encoded tool-result values,
including proxy notices, without imposing an unrelated JSON-RPC framing limit.

Project delivery continuations as opaque versioned stateless string tokens.
Callers pass them unchanged and use explicit request fields to change navigation.
This encapsulates the coordinate representation without per-cursor persistence.
A MAC derived from the existing result-session credential binds each token to
its result ID and session. Tokens carry navigation, not disclosure authority or
proof of exclusive server issuance: a credential holder can construct them.
Independent ownership and current disclosure checks remain authoritative.

Retained output is immutable. Active-list navigation observes the existing derived
projection revision on each read, rebuilds its summary layout when needed and
warns independently per page/view. This is eager summary computation with lazy
page delivery; it does not introduce another query database. Native graph cursors
and ranked-search candidate windows retain their own semantics. Archived/all
lists have no cheap maintained archive revision and therefore retain the opening
snapshot, explicitly reporting that freshness is not revalidated. They preserve
requested page size and the default ten-page list selection across calls.

Recheck current disclosure authority before each read/search. Eligible exceptional
output has a result-specific entitlement bound to its original consent owner and
revocation generations; spending execution consent does not prohibit page two.
It never authorises source replay or fresh exceptional query execution. Consent-control
read results remain bound to their original owner; grant/revocation replies and
one-time result credentials are not retained as command output.

## Alternatives

- Host-specific truncation loses content and makes command behaviour inconsistent.
- Replaying commands for page delivery can repeat effects and spend consent again.
- Account-wide slots mix concurrent agents; connection-only slots cannot resume.
- Memory-only caching loses results on restart and duplicates disk persistence.
- Count-only freshness checks miss same-count edits and reordering.
- Editable continuation objects expose coordinate internals that callers should
  not need to inspect or modify; opaque tokens keep that representation private.
- Stored opaque handles require per-cursor state and lifecycle management;
  stateless tokens provide encapsulation without those writes or records.

## Consequences

Storage has explicit capacity refusal and owner-scoped cleanup rather than silent
TTL eviction. Exact byte-aware maps have a construction cost on initial output
and changed query projections. Unchanged navigation reads bounded page files.
Text reads have one delivery pager and no additional source cursor. Consumers
follow its continuation while retaining the originating envelope and payload
shape. Genuine domain cursors, such as graph traversal, remain independent of
delivery paging. Selection recovery messages distinguish another selected
page, later pages via `outside_continuation`, and earlier pages available by
absolute page number; selection completion does not imply every page was read.
Final encoding failures report delivery limitations without rewriting execution
truth or consuming notices that were never delivered.

Durable directory subtotals keep healthy quota transactions independent of the
number of retained pages. A dirty marker makes interrupted accounting recoverable;
reconciliation includes unfinished output and retired bindings. Proxy handoff
transfers buffered input through an inherited kernel channel rather than writing
session credentials into its state file.
