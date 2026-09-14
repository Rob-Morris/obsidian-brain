# DD-074: Proxy-owned server refresh

**Status:** Implemented (v0.68.2)
**Extends:** DD-061, DD-073

## Context

A long-lived MCP proxy can outlive an installed Core upgrade. Checking drift
inside a tool handler is too late to establish that every concurrent invocation
is safe to replay. Reads can spend specific consent, so they are not exceptions.
Human-only proxy drift text also hides actionable version information from
clients that forward only the assistant JSON block.

## Decision

The existing proxy recovery worker owns both crash recovery and idle server
refresh. `brain_proxy_status` and `brain_proxy_refresh` are MCP transport controls,
separate from the application catalogue and `runtime.status`. They accept no
arguments and remain reachable without a healthy child. Their distinct
`brain.proxy-result/1` result makes no application receipt or mutation claim.

The proxy checks installed Core drift before accepting each semantic call. At
an idle boundary it negotiates a candidate before retiring the previous child,
then dispatches the unaccepted request once. Explicit refresh takes the same
path. Failed validation retains the previous process but blocks admission to
stale application code. Explicit refresh can retry after repair. Initialisation
timeouts are bounded, and status distinguishes pending handover from success.

If any request is in flight, return busy immediately. Waiting for it on the
stdin thread could block the host response it needs; force-killing it would
destroy the safety property. This is a zero-wait drain, not a new timeout or
semantic retry mechanism. A concurrent installation can still race the final
check; the child guard and conservative owned-receipt recovery remain necessary.

Same-proxy handover retains the consent owner. Current configuration and
contract invalidation still apply. Changed command mappings are refused until that tool is rediscovered; unchanged
mappings continue across handover. No dispatched semantic request is replayed,
whether mutation or observation. New proxy instances need fresh exceptional
consent; proxy self-replacement is deferred to a separately verified handoff.

Loaded/installed versions, interface fingerprint/generation, refresh state and
recovery action are compact transport observations. A proxy drift warning joins
the existing command warning vocabulary and is serialised back into first-block
JSON, leaving ordinary no-drift responses unchanged. Legacy tool-list changes
are notified; negotiated host cache behaviour is not assumed.

## Consequences and verification

Two small declarations supplement MCP discovery without adding schemas to
`command.list` or the bootstrap. These controls cannot select another Brain,
execute caller-supplied code, install a release or raise permissions. Deployment
requires one restart for old proxies to acquire the new control surface.

Subprocess tests exercise both negotiated protocol eras, compatible automatic
and explicit handover, real consent continuity/spending, incompatible candidate
retention and repair. Focused tests cover busy work, unavailable-child discovery,
the single recovery owner and JSON/structured-content parity. Existing tests
continue to guard against replay after dispatched calls lose their response.
