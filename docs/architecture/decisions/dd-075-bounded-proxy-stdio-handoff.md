# DD-075: Bounded proxy stdio handoff

**Status:** Implemented (v0.68.3)
**Extends:** DD-073, DD-074

## Context

Exiting a stdio MCP proxy relies on host reconnection policy. Installed-client
experiments recovered in Claude and Grok but left Codex disconnected. In-place
process replacement preserved existing tools in all three, but a bare exec lost
buffered input and could lose the result of an already-entered invocation.

## Decision

An explicit, no-argument `brain_proxy_restart` control loads only the selected
Brain's installed proxy through its managed Python. Unchanged code is a no-op.
It requires an idle transport: no admitted calls, outstanding host replies,
pending result publication or lifecycle recovery. Busy work is left running.

The stdin owner uses explicit raw-fd framing with an owned bounded read-ahead
remainder. A versioned, bounded, unlinked private inherited descriptor carries
that remainder, pinned Brain/workspace and launch target, negotiated protocol,
legacy initialisation, exposed tool contracts and the restart request ID. No
credential or consent store/channel is included. Existing trusted launch
authentication remains private process environment.

Preflight launches the candidate's actual child and proves handoff support and
public-session continuity before teardown. Public capabilities, modern
`supportedVersions` and the legacy negotiated version are retained separately
from the replaceable command-interface metadata.

The stdin owner pauses admission, takes the reader publication gate and rechecks
idle state. It suspends the owned child, confirms the stop and refuses any pending
stdout bytes or host replies, resuming the same child on refusal. A bounded writer
barrier then preserves completed output with no producer able to publish behind
it. The child is killed and reaped, output owners stop and exceptional consent
closes; exec preserves fd 0/1. The replacement pins the same target, creates
fresh exceptional consent, negotiates the child and only then answers the
restart request. Buffered requests resume once, in order. Changed tool contracts
still require rediscovery. Host catalogue caches are not assumed to refresh.

Preflight or drain refusal leaves the old instance intact. Exec failure after
teardown resumes transport in the existing image with a fresh owner and an error;
old consent is never restored. A bounded consent-cleanup lock timeout ends state
and channels, records pending private-directory cleanup and recovers with fresh
or unavailable consent in the retained image. An unproven child/output-owner
shutdown fails closed without exec, rather than misreporting preflight refusal.
Failed new-child startup remains an explicit
recovery state, not success. No admitted semantic call is ever replayed.

## Boundaries

Initially support POSIX inherited stdio; unsupported platforms and incompatible
handoff versions require host restart. This is not an installer, privilege
escalation mechanism, permanent supervisor or cross-instance consent transfer.
Framing, state, preflight, output drain and shutdown have explicit bounds.

Read chunks/remainder are bounded to 64 KiB and state to 1 MiB. Ordinary input
frames retain their existing size support. Preflight, publication acquisition,
stop confirmation, writer drain, reap, joins and consent coordination waits each
have five-second limits. Existing bounded child handshake/recovery applies after
replacement. No permanent supervisor or process-wide consent store is introduced.

Server-to-host requests use proxy-owned correlation IDs scoped to their child;
late responses and cancellations cannot reach a replacement child. Private
negotiation refuses host-input requests. Cached tool contracts still need actual
rediscovery when their meaning changes.

Preflight and final source identity checks do not guarantee recovery if another
installer changes imports before the new entry point runs. Immutable release
images are a separate deployment improvement. A frozen filesystem/host cannot
be made responsive by a stdio handoff.
