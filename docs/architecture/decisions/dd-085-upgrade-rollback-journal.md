# DD-085: A write-ahead rollback journal makes a killed upgrade a failed one

**Status:** Proposed
**Extends:** DD-084

## Context

DD-084 made `VERSION` the commit witness: every failure before the commit
rolls back, and an interrupted run resumes as an ordinary upgrade. Rollback
restored the vault from snapshots held in memory, so it covered an exception
or a Ctrl-C, which the upgrader catches, but not a kill: a closed terminal,
`SIGTERM`, `SIGKILL` or power loss lost the snapshots and left the vault as
far as the migration got. DD-084 therefore required every migration to be
restartable, converging from any partial application of itself, and relied
on that for the kill case.

An audit found the requirement false for released migrations. 0.68.0 killed
after its shared-configuration write resumed by merging the converted shared
profile into the local selection, which gains the control command
`access.prepare`; 0.31.0 keyed an ambiguous tag differently on the rerun;
0.67.0 left empty month folders and reported a bare `skipped`; 0.29.0
backfilled `modified` from the kill time. 0.43.0, 0.50.0, the 0.29.0
pre-compile patch and 0.27.6 are at risk by code read. Released migrations
are not rewritten wholesale.

The in-memory snapshots also read every artefact folder the compiled router
names on every upgrade, whether or not a migration was pending.

## Decision

Killed is failed. The rollback snapshots are a write-ahead journal on disk
and the next run restores from it before doing anything else, so one
rollback model serves the in-process rollback and next-run recovery.

The journal is its own stdlib-only module below `_bootstrap`
(`upgrade_journal.py`): `upgrade.py` loads it beside the vault lock while it
replaces the rest of the scripts tree, and the launcher can read a vault's
journal at its own version (Doctor's reporting of a pending journal is a
follow-up). The module owns the format, location, exclusions, capture,
restore and the durable-write primitive; the upgrader keeps the
classification against the `VERSION` witness, the refusal and warning
messages and the scope policy.

The journal uses machine state home rather than a vault-relative directory.
That location does not enforce a sync exclusion: configured state home or
sync policy can still place it in synced storage. Its location is the state
home that `_bootstrap/paths.state_home`
resolves for every Brain reader (an absolute `$XDG_STATE_HOME`, else
`~/.local/state`; a relative value is ignored, as a relative
`$XDG_CONFIG_HOME` is, so the location never depends on the working
directory), then `brain/upgrade-journals/<digest of the vault's resolved
path>/`. Its header records the vault's resolved path and the run's old and
new versions, and a journal that names another vault is refused. Blobs are
content-addressed, so identical content is written once; entries are
appended, and the first entry for a path in a stage wins. The header is
written first and removed first, so a directory without one is a discarded
remnant and never a journal; opening a journal refuses one that is already
present and removes a remnant, whose blobs may be torn. Reading a journal
changes nothing, so a dry run can classify it.

Write-ahead: the original bytes, or absence, of every path are durable (blob
fsynced, its directory fsynced, then the entry line fsynced) before the path
may change, and a new journal directory is durable up to the first directory
that already existed. A directory listing is journalled with each tree root
so rollback can also remove what the run creates under it; the root line is
appended after the file lines of its batch, so a root that is durable vouches
for a complete batch, and a batch torn by a kill restores the files it holds
and removes nothing. Reading the journal is the trust boundary for everything
a restore will do: every path is absolute and normalised, a tree root must lie
under the vault because it authorises removing what is not listed beneath it,
a file entry may lie outside the vault (0.27.6 declares client configuration
in the home directory) and only ever rewrites that exact path, and a blob's
content is checked against its name when it is read, so a dry run reads no
blob. Rollback restores the ledger first, then the post-compile stage, then
the pre-compile stage, each verified before its predecessor restores older
bytes over overlapping paths, reading one stage's blobs at a time; an
interrupted rollback leaves the ledger at or behind the content, and a
restore that does not verify retains the journal and reports
`rollback_verified: false` with the journal among the recovery paths. An
in-process rollback whose journal cannot be read back still restores the
Core, reports the journal, and retains both the journal and the Core backup.

A restore never destroys bytes it did not journal. It first compares every
captured path with its current bytes, so a path already in its captured
state is not rewritten (which would churn file-sync and editor inodes), and
copies every byte it will replace or remove, whether the killed run's own
half-applied writes or edits made between the kill and the rerun, into
`brain/upgrade-recovery/<digest>/<timestamp>/` under the same state home,
with a manifest from vault path to copy, made durable before the first
restore write. The same restore serves the in-process rollback. The result
reports the directory and the number of paths kept, and so does the
`recovered_interrupted_upgrade` warning.

The journal holds only what the run can change. Always: the migration
ledger, even when absent, and `.brain/`, which holds the compile outputs,
tracking and skill backups. A migration's `prospective_effects` declaration
is exhaustive: each entry is the exact file it may create or change, or an
existing directory whose entries it adds or removes, journalled as a tree.
The released declarations were audited against their write sets: 0.24.0 and
0.25.0 now declare the `_Config/router.md` line they rewrite, and 0.67.0
declares `_Temporal/`, whose month folders it removes and kept folders it
creates; the other declared migrations were exact. For a pending migration
that declares nothing, the broad scope: `_Config/` and, after compile, every
artefact folder the compiled router names, captured once per stage and only
when such a migration will run; a compiled router that cannot be read there
fails the run, because the scope cannot be bounded. 0.71.0 declares its two
configuration files, and a test requires every migration above the released
`VERSION` to declare, so the broad scope stays a legacy path. For skill
reconciliation, each override's folder. Nothing is read when no migration is
pending. Excluded everywhere: lock endpoints (`*.lock`, whose restore would
replace the inode under a holder); the rebuildable retrieval outputs under
`.brain/local/`: `retrieval-index.json`, the embedding sidecars
`type-embeddings.npy`, `doc-embeddings.npy` and `embeddings-meta.json` (the
maintenance outputs of `retrieval.refresh-lexical` and
`retrieval.rebuild-semantic`) and `semantic-models/`, re-provisioned from
`semantic-model-manifest.json`, which the maintenance source manifest treats
as an input and which therefore stays in; and the stores no upgrade writes
and a rollback must not rewind, because they record what happened, this run
included: the command receipts under `command-outcomes/`, the operational
`diagnostics/`, the staged drafts under `staging/` and the upgrade log
`last-upgrade.json`, which every run rewrites. A contract test pins the
exclusion set to the constants their owners declare. `compiled-router.json`
stays in: it is small, and rollback must leave the old core with the router
its own compiler last produced. Anything not provably derived stays in.

The commit: once every migration is recorded, the skills are reconciled and
the cutover is committed, nothing remains that a rollback would need to undo,
so the journal closes and then `VERSION` is written. A kill between the two
leaves content the ledger fully records under the old `VERSION`, which the
next run finishes exactly like a failed `VERSION` write. A journal that could
not be closed is reported (`upgrade_journal_not_discarded`), by one treatment
wherever it happens: at the commit, `VERSION` witnesses it and the next run
discards it; after a verified restore, the next run restores the same bytes
again. Recovery classifies a leftover journal by the witness: a
version-changing run whose target `VERSION` is now installed committed, so
its journal is discarded (`upgrade_journal_discarded`) rather than restored,
because restoring would undo recorded migrations under the new `VERSION`; a
journal whose old version is the installed one belongs to a run that never
committed and is restored (a same-version run never changes `VERSION` and
selects no migration, so its journal holds only re-applicable pre-commit
effects, and restoring it is safe whether or not that run reached its
commit); any other pairing means the vault moved on by another route since
that run, and the journal is refused as `journal_stale`, naming the journal,
both versions and the choices, rather than restored over content the ledger
records.

Recovery runs at the start of the next upgrade, before the content guard,
because restoring returns the vault to its pre-run state, which is correct
whatever the new source is. A real run takes the vault mutation lock once,
before the recovery, and holds it through the content guard, the
same-version decision, the diff, the cutover preflight (an interactive
cutover acknowledgement therefore waits under the lock, which is acceptable:
nothing else may mutate the vault while the upgrade decides), every
pre-commit mutation and the `VERSION` commit, with the installed `VERSION`
read under it, so the guards judge locked state and a live run's journal is
never recovered by a concurrent one. The lock is not re-entrant; skill
reconciliation, which takes it on its own, is told the upgrader holds it
(`lock_held`), and a migration must not take it. A lock that cannot be
taken, busy or otherwise, is a no-effect `vault_busy` refusal. The
post-commit stages take their own locks and run after it is released.
`--dry-run` takes no lock and writes nothing: it reports that the journal
will restore the vault first, and its content guard and migration preview
judge the ledger the restore will leave.

Recovery is an effect of its own, whatever the run then does. Every outcome
that follows it, a refusal, a busy vault, a skip or an upgrade, carries the
`recovered_interrupted_upgrade` warning (worded to be true whether or not the
run proceeds) and a `recovery` summary, and the launcher reports such a run
with an `upgrade-recovery` committed effect, as `partial` when it was refused,
never as a no-effect error. The `running` upgrade log of the killed run
supplies only the stage it names; the `interrupted_previous_upgrade` warning
of DD-084 is reported only when no journal exists (a remnant is no journal),
and its resume wording says so. A journal that exists but cannot be read, or
names another vault, is a no-effect `journal_unreadable` refusal that names
the journal and the choices: restore the originals by hand from its blobs, or
move it aside and accept that the vault may hold a half-applied migration
that will be re-run. The journal opens before the first vault write, the
progress log and the persisted template capture included, so a journal that
cannot be opened, including one an earlier run could not remove, is a
no-effect `journal_unavailable` refusal.

Restartability stays a requirement, as defence in depth: with the journal a
kill is restore-then-rerun on the same machine, and restartability matters
only when the journal is unavailable, because the vault moved to another
machine or the journal was deleted. 0.68.0 is corrected now, because it is
the one migration that can grant a control command: its planner emits the
`.brain/local/config.yaml` write before the shared file's and the migration
applies the plan as given, an order verified to converge on a kill at either
point. The other at-risk migrations are known best-effort and are not
changed.

## Alternatives Considered

- **Rewriting every released migration to be restartable.** Each was written
  against the vault at its own version; the audit shows the property is hard
  to get right and impossible to prove for the kill case, and the journal
  removes the need on the same machine.
- **Journal inside the vault (`.brain/local/`).** A file-sync service would
  carry a half-written journal to other machines, where it names a vault
  that is not there; machine-local state is the only honest home.
- **Keeping an in-memory copy beside the journal.** Two stores drift; the
  journal is the single store both restore paths read.
- **A `committed` marker before the `VERSION` write.** Closing the journal
  before the witness makes the normal path unambiguous without one; the
  witness rule covers the only case that still needs classification.
- **Discarding the journal after the `VERSION` write.** Every leftover
  journal would then need the witness to classify it, and a same-version run
  has no witness.
- **A separate upgrade lock.** The vault mutation lock already excludes the
  mutations that could race the restore; a second lock would exclude only
  other upgrades.
- **A re-entrant vault lock.** It changed the semantics of every lock caller
  to serve one, let a nested acquisition skip the symlink and parent checks,
  and depended on which copy of the lock module a migration's import context
  had loaded; the explicit `lock_held` the skill library already used for
  edits is the codebase's form.
- **Always journalling `_Config/`, with declarations as extra paths.** It is
  cheap, but it would make a declaration mean two things; an exhaustive
  declaration, audited for the released migrations, keeps the scope rule
  single and gives new migrations one contract.
- **Checking every blob's content at load.** It would read the whole journal
  for a dry run that restores nothing; the check belongs where the bytes are
  read, and the preview says what the restore will attempt.
- **Restoring over edits made after a kill, or refusing to.** Overwriting
  them is silent loss; refusing leaves a half-applied migration in place.
  Keeping the bytes beside the journal and reporting where lets the restore
  complete without destroying anything.

## Consequences

- A kill at any point before the commit is undone by the next upgrade, on
  the same machine, from the journal, and the migration reruns from the
  pre-run state; the migration's own restartability is no longer relied on
  there. Real-kill tests cover a pre-compile patch writing `_Config/`, skill
  reconciliation, the window between the journal close and the `VERSION`
  write, and a kill during the next run's recovery.
- No artefact folder is read when no migration is pending; a migration that
  declares `prospective_effects` keeps the journal to those paths, which is
  the incentive to declare them, and a new migration must.
- A vault moved to another machine, or a deleted journal, falls back to
  DD-084's resume, with the unchanged restartability risk for 0.31.0,
  0.67.0, 0.29.0, 0.43.0, 0.50.0 and 0.27.6.
- A refused, busy or skipped run leaves the vault byte-identical apart from
  the lock endpoint under `.brain/local/` when no journal is present; when
  one is, the restore is reported as the run's effect.
- A recovery leaves a directory under `brain/upgrade-recovery/` whenever it
  replaced bytes that differed from the journal; nothing removes it.
- Doctor does not yet report a pending journal; that is a follow-up. Signal
  handlers and the generated colour snippet under `.obsidian/`, which rollback
  has never restored, remain out of scope.
