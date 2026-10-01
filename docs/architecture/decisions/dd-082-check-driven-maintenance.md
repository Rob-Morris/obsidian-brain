# DD-082: Check-driven maintenance with human decisions as the only state

**Status:** Accepted
**Extends:** DD-043, DD-061

## Context

Brain detects maintenance needs (`vault.check`, `brain doctor`) and has
commands that repair them, but nothing ran the safe repairs unattended,
nothing let a person claim or dismiss the ones that need judgement, and the
detection-to-repair mapping was copied in three tables held together by a
parity test. Routine repairs were still routed through `repair.py`, the
bootstrap recovery entry point (DD-043), which bypasses the catalogue's
authorisation and receipts (DD-061). Doctor also synchronised the derived
machine registry as a side effect of diagnosis, and `migrate-legacy-installations`
delegated its steps to each Brain's `repair.py` subprocess.

A durable maintenance task store was designed first. Two thirds of its
mechanisms kept that store consistent with detection: everything in it except
a claim and a dismissal was a cache of what the checks already produce.

## Decision

Detection is the source of truth. A check yields findings; each finding has a
disposition declared by one repair table: `automatic` (a derived-only repair
the pass may run), `judgement` (a person decides) or `report-only`. One
`RepairFamily` type, one table per owner: the Brain table in
`_repair_common.py`, read by `vault.check`, Doctor, `repair.py` and the pass;
the machine table beside the machine pass.

A pass is one bounded, non-interactive catalogue command per locality owner,
`maintenance.run` for the selected Brain and `machine-maintenance.run` for the
machine, that any scheduler can call. It takes a non-blocking pass lock or
exits busy, detects in process, runs each unheld automatic family as a fresh
sibling invocation through normal admission, lists the rest, writes a
timestamped `last-pass` summary and exits with the platform exit categories.
Every invoked group ends in exactly one of seven outcomes, and on both sides
an `unknown` sibling makes the pass a `command_outcome_unknown` error naming
that sibling. Nothing follows an unknown up: the next pass re-detects. Only a
live claim withholds an automatic group; an expired claim is a review item the
pass still repairs.

Safety comes from detecting again and from repairs that act on current state:
each automatic repair re-checks the condition under its own lock and changes
nothing if it is already fixed. The first automatic set is `router`, `lexical`
and the new `temporaries` family (stranded atomic-write files under
`.brain/local`), plus add-and-refresh synchronisation of the derived machine
registry, which never drops a row. Concurrency is held by the pass lock, the
vault mutation lock inside each repair, and live claims; receipts are
incidental to admission.

The only persistent state is human decisions: a small versioned decisions file
per owner holding claims (a claimant and a one-hour expiry; an expired claim on
a still-detected finding is a review item) and dismissals (who, why, and the
fingerprint at dismissal; changed evidence reopens it; retention is thirty
days). The pass only reads it. Automatic groups are claimable but never
dismissible, and `router` is never held because detection depends on it.
Finding identity is a key over a declared discriminator and a fingerprint over
declared evidence, never prose. History is a `maintenance` family in the
operational log, observability only.

Maintenance commands are CLI and direct-script only; the only MCP-visible
signal is a coarse advisory read from `last-pass` by `session.start` and
`runtime.status`. The pass runs only from a standalone, keyless script context
under the default principal; MCP instances, session-run jobs and keyed contexts
are not given the maintenance invoker. Doctor becomes read-only, consuming the
target Brain's own `vault.check` through an injected runner, and the legacy
migration runs its machine-owned steps through the launcher's own commands,
reporting the exceptional registry step for a person.

## Alternatives Considered

- A durable task store with lifecycle states, attempts, retry limits and
  reconciliation: rejected as a cache of detection with its own consistency
  problem.
- Idempotency as the safety argument: narrowed to current-state repairs, since
  a router or lexical rebuild that ran unconditionally would clear semantic
  embeddings every time it lost the race with a concurrent fix.
- Automatic content repairs (`frontmatter`, `ownership`, `empty_folders`): kept
  as judgement families with their repair named; they move artefacts or drop
  lines without a backup and stay preview-then-apply.
- Settling unknown outcomes through receipts: rejected; re-detection settles
  them and receipts are keyed per invocation, not per repair.
- Guidance that still names `repair.py`: rejected; guidance names catalogue
  commands, with `repair.py` only for machine-owned scopes when no launcher is
  on PATH and only for recovery scopes.

## Consequences

- Scheduled passes are plain CLI calls with an explicit `--brain` or
  `--vault`; a second overlapping pass exits busy.
- A repair that fails is simply found again; there are no attempt records.
- A cache repair on a semantic-configured vault degrades retrieval until the
  semantic repair runs, so the pass reports `semantic` as needing a person.
- `vault.check` stays at version 3 and now reports the lexical finding
  alongside a semantic one instead of suppressing it.
- Doctor reports registry drift it no longer repairs; `brain
  machine-registry sync` adds discovered Brains and a person decides about
  stale rows.
- Promoting a family to automatic needs both admission tests: a current-state
  re-check under its own lock and a derived-only or trivially reversible effect.

## Verification

`tests/repair/test_repair_table.py` is the single-source repair-table test;
`tests/application/test_maintenance_pass.py` holds the current-state admission
suite over every automatic family, the context gate and the pass contract;
`tests/application/test_maintenance_decisions.py` covers claims and
dismissals; `tests/application/test_launcher_machine_maintenance.py` covers the
machine pass and add-only sync; `tests/test_maintenance_findings.py` covers
identity, temporaries and the summary.
