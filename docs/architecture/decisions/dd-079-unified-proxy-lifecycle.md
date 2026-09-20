# DD-079: One proxy transport owns blocked startup and recovery

**Status:** Accepted
**Extends:** DD-073, DD-074, DD-075, DD-078

## Context

The old degraded startup loop could explain a failure but could not use the
normal proxy's restart control after external repair. Moving controls alone
would not fix this: modern MCP subscriptions are standing transport state, and
the SDK's healthy public capabilities must remain valid before a child exists.
Treating a subscription as an ordinary in-flight request prevents idle recovery.

## Decision

`Proxy` owns one input reader, output queue and lifecycle across ready and blocked
states. `_proxy_session.py` is an SDK-independent public negotiation/subscription
leaf. Real SDK conformance tests guard the advertised capability obligations.
Application interface metadata always comes from a validated real child; the
public-session equality check remains exact. Healthy startup retains its full
catalogue. Known-target failures retain identity and diagnostic controls;
unresolved or changed targets do not silently retarget.

| Owner | Responsibility |
|---|---|
| Input thread | Frame admission, cancellation, host replies, final POSIX handoff |
| Existing recovery worker | Bounded candidate negotiation and image preflight |
| Child reader | Current-generation responses, invocation outcomes and subscription bridge |
| Output writer | Sole stdout writes, ordered notifications and drain barrier |
| Consent owner | Existing per-proxy authority lifetime; fresh after image replacement |

The restart lock admits one lifecycle operation and orders cancellation against
candidate publication. The publication gate excludes retired-child output from
replacement. The subscription lock orders acknowledgement and event publication.
No new supervisor or second stdin reader is introduced. Preparation does not
wait on the input thread: status/ping remain available, concurrent controls are
refused, and new semantic calls are not queued. An original version-drift call
is held before admission, then dispatched once through the shared admission
path. Already dispatched work retains receipt-based recovery, never replay.

POSIX preflight completion wakes the raw input owner through a pipe. Only that
owner rechecks target, session, catalogue generation, subscription state, source
and runtime, captures current unread bytes, and enters bounded final retirement.
This path allows no child as well as a live/dead child. Same-runtime activation
retains the consent owner; actual proxy replacement never transfers consent.
The final descriptor is revalidated after capturing current unread bytes and
before retirement; oversized partial frames refuse handoff without discarding
input or ending consent. A live child without a validated interface is not a
recovery no-op, even when the installed version is unchanged.
Private descriptor version 2 carries bounded subscriptions and reads version 1
with no subscriptions. A recipient that cannot validate a descriptor refuses.

Modern subscriptions remain host-owned across child generations. A private
current-child subscription bridges resource URI and catalogue events.
Subscription-first sessions trigger initial negotiation on the recovery worker;
a shared initial-negotiation lock prevents it racing the input path for stdout.
Host streams are capped at 128/64 KiB; event fanout is coalesced and bounded at 1,024
pending events. Unexpected child-stream termination or overload ends streams
explicitly rather than silently leaving them stale. Obsolete child/stream events
are dropped. EOF closes transport ownership; cancellation before publication
prevents late activation. Cancellation after publication cannot undo it.

Child bridge reconfiguration runs on the recovery worker without holding the
publication gate over pipe writes, so child stdout can drain. After the child
confirms registration with its internal acknowledgement, level-triggered refetch
notifications cover changes during asynchronous resync; pipe-send completion
alone is not a coverage barrier. Host acknowledgements remain proxy-owned.
Bridge writes share the child frame lock and use a five-second deadline with
nonblocking pipe writes. A partial/failed write retires that child and invokes
existing receipt-aware recovery; it cannot strand the coordinator. POSIX waits
on pipe writability; Windows uses bounded short backpressure waits because
`select` cannot wait on its anonymous pipes. This uses Python 3.12's
[nonblocking Windows pipe support](https://docs.python.org/3.12/library/os.html#os.set_blocking),
not another writer/retry thread.

## Boundaries and trade-offs

- Registration, approval policy, installation and dependency provisioning stay
  with their existing owners. Retry reuses trusted selection; it cannot choose
  another Brain, arbitrary executable or environment.
- The early transport requires the proxy's launcher-safe imports to work. Stable
  CLI/pre-proxy failures and broken Python imports remain external-repair cases.
- Initial trusted target/prerequisite assessment, owner setup and first process
  launch still precede the input loop. This change does not promise controls
  during blocked filesystem I/O in that composition step. Fresh startup does not
  await a child handshake there; retaining full first healthy discovery avoids
  forcing otherwise healthy hosts into a recovery-only tool cache. Responsive
  preparation applies to recovery once the transport is entered.
- Windows shares diagnosis and same-runtime activation. POSIX image replacement
  is not emulated by killing the connection and hoping the host reconnects.
- The final quiescence/drain/retirement/exec phase temporarily pauses input;
  preparation does not. Each existing handoff substep retains its bounded wait.
- Host tool caches are outside the protocol owner. A notification and successful
  activation do not prove model-visible rediscovery: verify an ordinary call,
  otherwise reconnect. No extra executor bypasses the host's tool surface.
- Duplicating the degraded server or retaining synchronous private recovery
  alternatives would create competing policy and misleading test coverage;
  both are removed in favour of the wired lifecycle path.

## Verification

Production subprocess tests cover legacy/modern blocked startup, external repair,
ordinary reads, exact session continuity, missing-child image replacement,
subscription retention, cancellation, pipelined/split input and known failed
handoff. Deterministic tests cover delayed candidates, EOF, busy work, child
stream termination, generation filtering, descriptor bounds and Windows pipe
ownership. Native CI remains a separate post-push gate.
