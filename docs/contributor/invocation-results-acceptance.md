# Invocation result navigation acceptance

The implementation follows [DD-091](../architecture/decisions/dd-091-invocation-result-navigation.md).
These checks establish local behaviour; they do not certify an installed runtime,
public proxy or release, or claim that hosted CI has passed.

## Behaviour and evidence owners

| Contract | Executable evidence |
|---|---|
| Private sessions, latest admission per command, no TTL, atomic publication, explicit capacity and cleanup | `tests/application/test_result_store.py` |
| Durable quota subtotals, interrupted-write recovery and bounded healthy transactions | `tests/application/test_result_accounting.py` |
| Exact byte-aware maps, selections, Unicode fragments and structured detail progression | `tests/application/test_result_navigation.py` |
| No source replay, current disclosure, consumed one-shot consent and revocation | `tests/application/test_result_delivery_integration.py` |
| Current list revision, same-count edits and independent page notices | `tests/application/test_result_delivery_integration.py` |
| Active-list maintained revisions and explicit retained archive/all snapshots | `tests/application/test_result_list_revisions.py` |
| Atomic generation selection, cached page-size layouts and anchored navigation | `tests/application/test_result_atomic_selection.py`, `tests/application/test_result_cached_views.py` |
| Bounded literal search, fragmented matches and explicit unsearched detail scope | `tests/application/test_result_search.py` |
| Reserved metadata stripping, protected CLI files and inherited job context | `tests/application/test_result_session_transports.py` |
| Credential-publication failure preserves execution status and effects | `tests/application/test_result_issuance_truth.py` |
| Private file creation, ownership and access checks | `tests/test_private_files.py`, `tests/test_private_files_windows.py` |
| SDK stdio bootstrap, complete mandatory guidance, two agents and CLI subprocess resumption | `tests/application/test_result_delivery_cli_mcp.py` |
| Final canonical/encoded ceilings, required proxy notices and post-write observation | `tests/test_proxy_result_budget.py`, `tests/test_mcp_proxy_refresh.py` |
| Credential redaction and private buffered-input transfer during proxy handoff | `tests/test_proxy_debug_redaction.py`, `tests/test_proxy_handoff_channel.py` |
| Complete declaration costs and the existing no-growth ratchet | `tests/test_granular_mcp_metadata.py` |

The stdio/subprocess fixture uses an installed disposable Brain and the real
composer, SDK, application adapter and result store. Its declared bootstrap
readiness/dependency surrogate avoids provisioning or detached warmup. It is
transport acceptance, not managed-runtime or native-host certification.

On 2026-10-21, an isolated native-fixture probe also passed with installed Codex
CLI 0.163.0 and Claude Code 2.1.303 against the same immutable candidate image.
Both resumed two bootstrap source windows through 15 generic `result.read` calls
and reconstructed the complete 23,568-character mirror with the same SHA-256.
The probe used localhost scripted model endpoints, disposable homes/vaults and
the fixture's declared readiness surrogate. This verifies host transport and
complete guidance delivery, not an external model's reasoning, public-proxy
upgrade behaviour or release certification.

Native Windows ACL and installed-CLI resumption checks are included in the
Windows smoke workflow. They require Windows and are not established by the
local macOS run. See [private files](../architecture/private-files.md) for the
platform-specific boundary.

## Interpretation

A response's `status` is the source execution outcome. `delivery.status` describes
whether this call delivered the complete selected output, a bounded page, an
out-of-range page or an unavailable representation. Page zero never bypasses the
one-response byte ceiling. Retrieve more pages rather than repeat a mutation.

Cursor-free active artefact lists enumerate and index existing summary projections.
Only delivery is lazy: unchanged paging reads bounded stored pages; changed
projections rebuild the summary layout. Counts describe that observed projection,
not proof that every live file has been reindexed. Ranked search and graph
traversal retain their existing candidate windows, native scopes and cursors.

Archived/all lists retain the opening filtered summaries because archive files
have no cheap maintained revision. Their metadata reports retained backing and
immutable-output consistency, with a persistent freshness-limitation notice.
Paging does not scan or refresh archive files; invoke a new list for current
data. These remain list selections: requested page size, exact counts and the
default ten-page selection across calls still apply, with outside continuation
and `max_pages: "all"` exposing the complete retained set.

Literal search covers retained body units, with explicit scan and detail
coverage. It does not claim to search every repeated envelope field. Stored
layouts are reused when page size and source revision match. A size change
through an existing continuation requires an explicit unit anchor.

Healthy quota transactions read fixed-size ancestor subtotals. Full filesystem
reconciliation is reserved for initial or interrupted accounting; it does not
run on every admission or changed-page observation. Retired bindings remain
charged and are never silently evicted.

Metadata annotations that merely repeat a field name are omitted from MCP
schemas. Canonical command help remains fully described and validation/literal
values remain unchanged. This offsets reserved-session and navigation-tool cost;
the complete eager catalogue still exceeds the 20,000-token product target.
