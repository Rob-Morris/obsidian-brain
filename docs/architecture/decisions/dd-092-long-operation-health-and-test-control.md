# DD-092: Long-operation health and development test control

**Status:** Accepted

## Context

Quiet work is not evidence of a hung child. The proxy's output-silence watchdog
can kill a healthy serial application worker. A separate contributor need is
to stop slow tests deliberately while preserving useful development evidence.
Arbitrary in-process interruption cannot establish that fixtures or children
have been cleaned up. Neither concern warrants weakening release certification.

## Decision

Keep live child health and contributor verification as separate owners.

The proxy owns a bounded probe outside the application worker and pipe reader.
Use the supported protocol-era operation: legacy ping or modern discovery.
Validate matching current-child replies; discovery reuses the existing command
interface validator and never updates the public catalogue as a side effect.
The proxy's own client ping response cannot satisfy the child check. A failed
probe enters the existing identity-guarded recovery path. Preserve serial
execution, cancellation finalisation and owned mutation-outcome rules. Read
complete child frames with bounded waits, retaining partial bytes, so retired
pipes cannot strand replacement delivery. Keep complete reads and publication
under the handoff gate. Explicitly reap a child retired by health failure before
replacement; failed reaping stops the proxy. Retire the health owner at handoff.
An admitted idle lifecycle pauses reader reacquisition until completion, so
publication priority does not depend on lock fairness.

An opt-in POSIX contributor controller owns one serial pytest process group,
outside its own lifetime. Normal pytest, parallel feedback and release commands
do not load its plugin. Safe checkpoints may unwind a requested interruption
only during an active call and never while an exception is already unwinding.
Test authors register audited cleanup predicates; missing/failed proof stops
the whole run. Explicit requests have a fixed wrap-up grace, then bounded
whole-group termination/reaping. No execution deadline is imposed on healthy
tests. Unsupported distributed/loop modes are refused before their runners start.

Persist exact selection/input identity before tests, append bounded terminal
attempt evidence before the next test, and let the surviving controller write
the terminal receipt. Recovery does not depend on session finish.
Terminal records address exact test IDs by their position in the run manifest;
large parametrized names are stored once. Older raw-ID records remain readable.
Only complete, consistent phase evidence can credit a pass or a legitimate
applicability skip.
An interrupted final append stays outstanding. Preserve genuine failures and
collection/session errors rather than erasing them with a later pass.
A matching normal-exit marker is emitted only after `pytest.main()` returns
through plugin finalisation; missing completion forces fresh scope verification.

Whole-test follow-ups reuse only applicable development passes. Hash authored
inputs, explicit external inputs, dependencies and environment; keep environment
values out of the record. Changes require reassessment. Unknown shared cleanup
requires a fresh selected-scope run; unconfirmed process termination refuses
automatic follow-up. Preserve earlier attempts as history even when evidence
cannot be combined. Combined development coverage explicitly cannot certify a
release. Candidate preparation and hosted/native certification keep their full gates.

## Consequences

Cooperative waits can wrap up without a new process per test. Uncooperative
work has a whole-run stop fallback. A manifest, outcome ledger and controller
receipt answer the observed failure without generic checkpoint resumption,
dependency inference or event-sourcing machinery. Validate native paths that
are offered and measure cost before expanding isolation or optimisation.

See [contributor controls](../../CONTRIBUTING.md#controlling-long-development-tests)
and [proxy recovery](dd-028-version-drift-detection.md).
