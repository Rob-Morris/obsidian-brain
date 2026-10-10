# DD-084: `VERSION` as the upgrade commit witness and the migration ledger

**Status:** Proposed
**Extends:** DD-036, DD-072, DD-083
**Extended by:** DD-085 (a write-ahead rollback journal makes a kill restore-then-rerun on the same machine; the restartability requirement below is defence in depth for a vault whose journal is unavailable)

## Context

An upgrade copies the new Brain Core into `.brain-core/`, validates the
compiler, runs versioned migrations in two stages, reconciles skills, commits
the CLI cutover and then runs post-commit reconciliation. Each `ok` or
`skipped` migration is recorded in the vault-local ledger
`.brain/local/migrations.json`, and the ledger is seeded with every migration
at or below the installed `.brain-core/VERSION` so a vault with pre-ledger
history does not replay the past.

Two things undermined that ledger. First, `--force` did three jobs: it
bypassed the same-version guard, it bypassed the downgrade guard, and it
selected every migration up to the target while ignoring the ledger. That
replay was added as an escape hatch for the ledger and kept one consumer, an
`install.sh` pass-through that has since gone. The repository holds 29
migrations, each written against the vault as it was at its own version;
idempotence covers running one twice, not applying 0.29.0 to content that
0.30 to 0.71 have reshaped. A lab run that forced a same-version re-apply
replayed `migrate_to_0_29_0` and failed.

Second, the copy wrote `VERSION` with the rest of the core, and `VERSION`
sorts first among modified files. Almost any interrupted upgrade therefore
left `VERSION` new with a partial ledger. A plain rerun stopped at "Already at
X"; seeding would have marked the unrun migrations as done; only the forced
replay healed the state, by accident. A second accident sat beside it: the
0.68.0 authorisation conversion reads the installed template before the copy
replaces it, so a resumed run would have converted legacy grants against the
new profiles.

Downgrades were nominally supported through `force`, but the cutover
preflight already refuses a source older than any registered Brain whenever a
global CLI is installed, there are no down-migrations, and an older Core over
content shaped by newer migrations holds ledger records it does not know.

## Decision

`.brain-core/VERSION` is the commit witness. The copy writes every added or
modified core file except `VERSION`, then fsyncs each copied file and, on
POSIX, each directory whose entries changed. `VERSION` is written with the
safe-write pattern (DD-036) only after both migration stages, skill
reconciliation and the CLI cutover have succeeded, from the source bytes
captured at run start, and its directory is then fsynced. Until that write,
every failure rolls back as before. A failed write or replace of `VERSION`
is a `version_commit` partial outcome whose remedy is to rerun the
same upgrade; once the replace has landed, a failed directory fsync is only a
`version_commit_not_durable` warning and the post-commit stages still run.
One unconditional router compile follows (the router stamps and tracks
`VERSION`, so every compile inside the window is stale once it is written),
reported as `router_compile`.

Seeding still backfills the ledger to the installed `VERSION`, and selection
is `installed VERSION < version <= target` minus recorded keys, per target.
Only `ok` and `skipped` results are recorded, each straight after its
migration; anything else raises. The ledger is not committed to Git, but a
file-sync service shares it with the rest of `.brain/local/`, so a lagging or
absent ledger on one machine is backfilled from `VERSION` rather than
replayed.

`force` bypasses only the "Already at X" outcome: it re-applies a source whose
version and core both match the installed Core. It is not an input to
seeding, selection or recording, and no flag re-runs a recorded migration; a
correction ships as a new migration. A same-version source whose core differs
re-applies without `force`, with a `core_mismatch` warning.

One content guard runs first and cannot be bypassed: a run never applies a
Core older than the recorded content, where the content version is the
higher of the installed `VERSION` and the highest strictly parsed version in
the ledger. The versions it compares are validated first: a source
`VERSION`, or a present installed `VERSION`, that is not strict `X.Y.Z` is
refused as `version_unreadable` rather than compared, because a guard that
cannot order the versions would otherwise fail open; a ledger key that does
not parse is ignored. A missing ledger is no records, but a ledger that is
present and cannot be read as the shape every ledger writer has produced
(JSON that does not parse, a root or `migrations` that is not an object, or
an entry that is not an object) is refused as `ledger_unreadable` rather than
read as empty, because seeding would otherwise overwrite it and lose the
records the guard could not see. The remedy is to restore the file from a
backup or file-sync history; moving it aside lets the next run backfill from
`VERSION`, at the cost of the record of any newer content, which the guard
can then no longer protect. The guard covers an ordinary downgrade and newer content
under an older Core, such as an interrupted newer upgrade. The refusal is a
no-effect error at every surface that reaches the upgrader (`install.sh`'s own
version comparison refuses a downgrade locally, before the upgrader, and exits
0 by design), and its remedy names a source route at or above the content
version, never `brain upgrade`, whose upgrader is the installed
distribution's and may be the source being refused.

Rollback restores the ledger first, then the post-compile snapshots, the
pre-compile snapshot and the core. A migration records itself after its
effects land, so unwinding must unrecord before it undoes; an interrupted
rollback then leaves the ledger at or behind the content.

Because an interrupted run resumes a migration against a partial application
of itself, a migration must be restartable as well as idempotent. A
repository contract protects the identity of every released migration: at or
below the `HEAD` `VERSION`, its file name and declared target set cannot
change in a staged commit, while its body may still be corrected, and no
migration can be added at or below that version (vaults already there would
never run it); a release commit that bumps `VERSION` may still add the
migration it releases, because the boundary is `HEAD`'s version. On `dev`
and in the lab, an unreleased migration is applied directly, which writes no
ledger record, so propagation from `src/brain-core` passes the content guard.

The authorisation template layer is persisted at
`.brain/local/authorisation-template-before-upgrade.json` before the copy,
keyed by the installed `VERSION`, reused while that version matches and
removed at the commit. Only the template is persisted: the authored layers
are captured fresh so a user edit between runs is detected rather than
inherited.

`.migrated-version` is retired; nothing reads it. The lab's acceptance gate
checks the `VERSION` commit and full ledger coverage instead, and requires
every record between the baseline and the target to have been written by the
runner. A `running` `last-upgrade.json` produces an
`interrupted_previous_upgrade` warning classified on the closed pre-commit
stage set: a post-commit interruption (re-apply or named repair), a killed
same-version re-apply (re-apply), or a pre-commit interruption left by an
older upgrader (migrations may be missing; `force` will not run them). It is
never an input to selection, because its writes swallow errors. The upgrader's
warnings, these included, reach `brain upgrade` as launcher warnings.

## Alternatives Considered

- **An in-flight record beside the ledger.** It needed six rules of its own
  and still could not make an `install.sh` retry resume, because `VERSION`
  was already new. Deferring `VERSION` needs few reader changes.
- **Ledger-only seeding, seeding when empty.** Wherever the ledger lags its
  machine's `VERSION`, this reruns 0.61 to 0.71 and turns per-target gaps
  into lab failures. Seeding to `VERSION` is the conservative rule whether or
  not file sync shares the ledger.
- **A `--rerun-migration` flag.** No consumer exists, and a correction is a
  new migration.
- **Keeping forced downgrade.** It only ever served rare cases, such as
  leaving a forked dev build above stable, which resolve once stable reaches
  that version: the core then mismatches at the same version and re-applies
  without `force`. Robustness wins.
- **Passing the version into compile instead of recompiling.** `VERSION` is a
  tracked source hash, so the router would still be stale.
- **`os.sync()` for durability.** Machine-global and not available on Windows;
  per-file and per-directory fsync is the narrowest portable primitive.
- **Protecting released migrations by release tag or byte-immutable body.**
  Tags are sparse after 0.51, and released bodies are legitimately corrected
  for vaults not yet upgraded.

## Consequences

- An interrupted upgrade at any point before the commit resumes as an
  ordinary upgrade: recorded migrations are skipped, the interrupted one
  restarts, and the persisted template capture keeps the 0.68.0 conversion
  against the old profiles.
- A forced same-version re-apply runs no migration, so it is safe on any
  vault and is the remedy for a post-commit interruption.
- A downgrade, or a source between the installed `VERSION` and the highest
  ledger record, is refused without override. A crash before the cutover
  followed by `brain upgrade` from the old distribution is refused with the
  source routes that resume normally. Only the first upgrade across this
  change can leave a crash state that the old distribution's upgrader does
  not recognise; it must be resumed from the new source.
- Post-commit work no longer keys off the copy diff: the central runtime is
  ensured every run (a no-op when its venv exists), and the adapter "updated"
  follow-up compares managed copies with the installed adapter, so a resume
  or re-apply with an empty diff still provisions and advises.
- A failed post-commit router compile is a partial result rather than a
  success, which a custom taxonomy can produce in ordinary use.
- Concurrent MCP mutation during the window, sync-service ordering of
  `VERSION` against the files it witnesses, a commit witness for fresh
  installs, and automatic recovery of crash states left by older upgraders are
  out of scope.
