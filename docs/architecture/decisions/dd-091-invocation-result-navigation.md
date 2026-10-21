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
Use explicit detail references for oversized structured values and Unicode-safe
fragments for long lines. Preserve originating status and committed effects
when delivery fails. Guard the final canonical and encoded tool-result values,
including proxy notices, without imposing an unrelated JSON-RPC framing limit.

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

## Consequences

Storage has explicit capacity refusal and owner-scoped cleanup rather than silent
TTL eviction. Exact byte-aware maps have a construction cost on initial output
and changed query projections. Unchanged navigation reads bounded page files.
Consumers follow delivery continuation before an originating command's cursor.
Final encoding failures report delivery limitations without rewriting execution
truth or consuming notices that were never delivered.

Durable directory subtotals keep healthy quota transactions independent of the
number of retained pages. A dirty marker makes interrupted accounting recoverable;
reconciliation includes unfinished output and retired bindings. Proxy handoff
transfers buffered input through an inherited kernel channel rather than writing
session credentials into its state file.
