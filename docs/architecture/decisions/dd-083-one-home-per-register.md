# DD-083: One home per register

**Status:** Accepted (implemented in stages)
**Extends:** DD-052, DD-078, DD-082
**Amends:** DD-051 (§2), DD-053

Items 1 to 12 and the amendments to DD-051 and DD-053 are in the code. The
consolidated documentation of the machine and workspace state lands in a later
change, after which this text is reconciled with it.

## Context

Brain keeps six machine-local or workspace-local files that record which
Brains exist, which one is the default, which workspaces link to which Brain,
and which MCP routes were installed. Two of them were derived from others, and
the two derivations were maintained differently from one another and from
everything else.

`~/.config/brain/brains.json`, the "derived machine registry", was a cache of
the vault registry plus the current vault. Nothing on a registration path wrote
it: `install.py` and `brain register` write only the vault registry. It was
written only as a side effect of diagnosis, until DD-082 made Doctor read-only
and moved the write to an explicit `machine-registry.sync` pass family. A fresh
install therefore left the file absent, Doctor reported the derived registry as
drifted, and `doctor_machine.py` exited 1 on a healthy machine. The file's only
reason to exist, a shell-side fallback scan, left with the CLI 3 rewrite
(v0.55.0); every remaining reader was either Doctor reporting on the file
itself or discovery merging it with the authoritative rows it was derived from.

The workspace link has the same shape. A link is one fact with two ends: the
workspace manifest (`<workspace>/.brain/local/workspace.yaml`) says "I am
linked to Brain `id` under hub `key`"; the Brain's linked workspace registry
(`<vault>/.brain/local/workspaces.json`) says "`key` is at folder `P`".
`workspace.setup` writes both ends, but `workspace.register`,
`workspace.unregister` and `workspace_registry.py --register/--unregister`
write only the Brain end, `workspace.bind`, `setup.py workspace` and
`configure.py workspace binding` write only the workspace end, and nothing
checked that the two ends agree. Resolution also wrote: the documented lookup
`resolve_local_brain_alias` registered a vault as a side effect
(`vault_registry.backfill`), and a self-heal layer above the pure resolver
registered Brains and seeded the default pointer, with no production caller.

## Decision

A fact lives in one file. A second file holding the same fact is derived, and a
derived file is kept only when a reader needs it, is written by the operation
that changes the fact, and is otherwise reconciled from its source by a check
with an automatic repair. No fact is maintained by a hidden write.

1. **The derived machine registry is retired.** `brains.json`, its lock,
   parser, backup, synchronisation, inspection and add-and-refresh, the
   `machine-registry.sync` launcher command, the `machine_registry_drift` and
   `stale_machine_registry` machine-pass kinds and Doctor's `drifted`,
   `malformed` and `blocked` registry states are removed. The vault registry
   (`~/.config/brain/vaults`) plus the default pointer (`~/.config/brain/default`)
   is the one home for which Brains exist on this machine. A leftover file on
   disk is inert: nothing reads it, and it is not deleted, because a released
   v0.70.10 Doctor on the same machine would recreate it.
2. **Resolution never writes; Brain registration has one owner.** The vault
   registry and the default pointer are written only by `vault_registry.py`'s
   actions, reached from `install.py` (the act of installing), the launcher's
   `brain register`, `unregister`, `set-default`, `clear-default` and
   `registry remove-stale`, and the direct script as the recovery form. The
   self-heal layer is deleted, `resolve_local_brain_alias` becomes the pure
   lookup its name states (returning `None` for an unregistered vault),
   `backfill` goes as an alias of `register`, and `install.sh`'s existing-vault
   branch registers without an ID. DD-053's "self-registration on operation"
   is withdrawn: it hid the missing owner. Rows are canonical by invariant:
   registration stores `realpath`, and every reader that maps a path to a
   Brain (registration, the ID lookup, unregister, discovery, direct-command
   identity and the CLI cutover) uses one predicate,
   `vault_registry.row_matches`, an exact comparison of the stored value with
   `realpath(query)`; readers that enumerate rows (the MCP inventory, `brain
   list`, `--brain`, `brain resolve`, remove-stale and the cutover) apply the
   one stale rule, `vault_registry.stale_reason`. A row that is no longer its
   own `realpath` (a symlink left or planted at an old path, or a stored path
   that is not in canonical form) never matches, resolves or selects a Brain,
   so a symlink cannot silently move a Brain's identity. It is stale, and
   registration refuses to give the Brain it resolves to a second ID,
   whatever ID is asked for; a canonical row that already matches the path
   still registers as a no-op. Only an installed Brain registers, so a row is
   never stale on arrival. `brain unregister` accepts an ordinary spelling
   through a symlink (`/tmp`, `/var`, a symlinked parent) and refuses only
   when a drifted row makes the path ambiguous: the path is a drifted row's
   stored value, or a drifted row also resolves to the Brain it names. The
   direct script forwards the literal path to the launcher for the same
   reason. Every strict reader that must see each Brain (MCP migration, and
   every approval-wrapped change while approvals hold records) refuses a
   drifted row with its explanation; an inspection records it as an
   unreachable location with its own recovery. Uninstall reads only the
   current vault's targets, so it meets a drifted row only through that
   approval transition.
   Each stale row names `brain registry remove-stale` only when the registry
   would accept it. One rule, `vault_registry.prune_refusals`, decides both
   the guidance and the removal: a row is never removed while MCP
   integrations it owns survive (a canonical row's at its path, a drifted
   row's at its `realpath`), a drifted row whose `realpath` is another ID's
   row owns none, and a drifted row cannot be removed while managed
   approvals hold records, because their inventory refuses it. No row can be
   removed while the approval state itself needs recovery (a ledger the
   managed writer cannot use, or a pending approval transaction); the
   explanation then names `brain approvals inspect --json`. Remove-stale
   removes every stale row or none, so one refused row blocks it for all;
   a canonical row that is removable on its own is then named for `brain
   unregister`, unless approvals hold records, whose strict inventory would
   refuse the blocking row first. For a removable drifted row whose
   `realpath` is an installed Brain, the recovery is remove-stale and then
   `brain register` with that path and the old `brain_id` (and `brain
   set-default` when the row was the default, which remove-stale clears). A
   row with no command has no guidance; its explanation says why (its own
   refusal and remedy, or the row that blocks remove-stale), `brain list`
   and Doctor carry that explanation, and the machine pass reports the row as
   `stale_vault_registry_blocked`, with no repair command and the same
   finding identity as `stale_vault_registry`. Recovering a moved
   Brain that holds MCP integrations or approvals needs one atomic change
   across the vault registry, the approval ledger and its transitions, and is
   not part of this decision.
3. **An unregistered Brain is a finding, detected machine-side.** Discovery
   reports Brains whose only source is the current vault under
   `unregistered_brains`; Doctor renders them with the `brain register`
   command; the machine pass emits the judgement kind `brain_unregistered`
   with a `brain.register` row whose request is derived from the finding's
   subject. Registering is choosing an identity, which is not derived from
   anything and so fails DD-082's admission test for automatic families.
4. **The workspace manifest is the source of truth of the link; the linked
   workspace registry is derived and keyed by the hub key.** The manifest is
   the only end that can state the fact completely, it moves with the folder,
   it is human-authored, and the Brain cannot verify a folder it cannot reach.
   The registry stays because `workspace.list`, `workspace.read`, artefact
   listing and path resolution need it and a Brain cannot enumerate manifests.
   The manifest `slug` is local identity and never part of the link.
5. **The link is the unit of mutation.** Writers of the linked workspace
   registry are `workspace.setup`, `workspace.unregister` (version 2: request
   `{key}`, drops the row and, when the recorded folder is reachable and its
   manifest names this Brain and key, unbinds that manifest too) and
   `workspace.repair-registry`, and MCP configuration and migration derive
   the row a manifest implies through `workspace_registry` rather than writing
   the file themselves. `workspace.register`, `workspace.bind`
   (`workspace.setup --force` is the rebind surface), `setup.py workspace`,
   `configure.py workspace binding` and `workspace_registry.py`'s
   `--register`/`--unregister` are retired. A vault configuration migration
   removes the retired command IDs from profile allow-lists, initial-command
   lists and overrides without widening any grant, and the historical profile
   projections drop the same IDs, so an upgrade from any earlier version
   reaches it. DD-051 §2's statement that
   the targeted `configure ...` commands remain valid is amended accordingly.
6. **Disagreement is a finding, detected where each end can be seen.** From
   the Brain end, `collect_registry_check_findings` reports, one finding per
   row with `file` `.brain/local/workspaces.json#<key>`:
   - `workspace_link_disagreement` (`warning`, repaired by the now-automatic
     `registry` family): a reachable, readable manifest naming another valid
     hub key, or a Brain that resolves to a different vault on this machine.
   - `workspace_link_unverifiable` (`info`, judgement): the folder is a vault
     root, has no manifest, its manifest cannot be read, its Brain ID does not
     resolve on this machine, or its hub key is missing or not a valid key.
   - `workspace_folder_unreachable` (`info`, judgement): the folder is absent.

   The file itself is one finding. `workspace_registry_malformed` (`warning`,
   `registry` family) is a file whose rows can be read but which needs
   normalising or holds rows that name no usable folder (an invalid key, or a
   path that is empty, relative or contains a NUL byte); the repair rebuilds it
   without them and keeps the file as a backup. A file whose rows cannot be
   read is never rebuilt unattended, because the registry cannot be re-derived:
   `workspace_registry_unparseable` (not UTF-8, not JSON, or the wrong shape)
   and `workspace_registry_unreadable` (the file cannot be read) are `warning`
   judgement findings with no automatic repair. The person rebuilds an
   unparseable file explicitly with `workspace.repair-registry`
   `{"allow_row_loss": true}`, which keeps the backup. When this machine's vault
   registry cannot be read, `workspace_links_unverified` (`info`, judgement)
   says that no row was verified; a Brain that is not registered verifies
   nothing and says nothing, because item 3 reports it machine-side.

   From the workspace end, `vault.check` reports `workspace_registry_missing`
   (`info`, report-only), comparing the canonical path of the row the Brain end
   salvages. Reconciliation never adds a row and never drops a row it cannot
   positively contradict. It classifies each row from one read of its manifest
   outside the vault lock; under the lock it reads the registry again and drops
   a row only if the row still records the same folder and a second read of
   that folder's manifest is byte-identical to the one classified and still
   disagrees. It takes no lock in, and creates no file in, any workspace
   folder. Lock order is vault, then folder, never the reverse:
   `workspace.setup` holds the vault lock from the row write through the
   manifest write, with the folder lock nested inside, so a repair, which drops
   a row only after a fresh read under the vault lock, cannot drop the new row
   while the old manifest is still in place; `workspace.unregister` takes the
   folder lock only after releasing the vault lock. Every writer of the file
   (setup, unregister, the repair and MCP reverse registration) writes with a
   compare-and-swap over the bytes it read; a writer that loses the race writes
   nothing and returns a retryable `conflict`. No other lock order is needed:
   MCP taking the vault lock would invert `repair_mcp`'s existing nesting.
   Every reader applies one row rule, `workspace_registry.salvage_row`, which
   returns the canonical form; a row stored in another form (such as `~`) is
   normalised, and a row that names no usable folder is invalid everywhere and
   never resolved against the current directory.
7. **Doctor's payload describes the vault registry.** `machine.registry` is
   `state` (`current`, or `stale` when rows point at non-Brains), `path` and
   `brains_count`; the derived-registry fields are removed; counts gain
   `unregistered_brains`; `brain.doctor` moves to version 3.
8. **Doctor health counts per-Brain findings only when the machine owns or
   repairs them.** A per-Brain finding counts against `machine.healthy` only
   when its repair family is automatic or machine-owned; one without such a
   family is listed and never makes `machine.healthy` false, so a
   dismissal-free Doctor is never stuck unhealthy on a per-Brain finding. MCP
   registration drift that is not an unreachable location and an unhealthy or
   legacy runtime still count, as before. `brain_unregistered` is a
   machine-pass kind the health formula does not read, so it is health-neutral
   by construction. The link codes `workspace_link_unverifiable`,
   `workspace_folder_unreachable` and `workspace_links_unverified` are `info`,
   so the current vault's `vault.check` exit code, which Doctor's overall
   result also folds in, stays 0. `workspace_registry_unreadable` and
   `workspace_registry_unparseable` are warnings: they leave `machine.healthy`
   alone but make Doctor's overall result unhealthy for the current vault
   until the file is restored or explicitly rebuilt.
9. **The machine pass has no automatic family.** With `LocalAuthority.allows`
   always true, the automatic flag is the only gate on the machine side, and
   nothing left on it passes DD-082's admission test. The pass detects, lists
   and writes its summary with no groups; promoting `mcp` or `runtime` is
   DD-082's existing path.
10. **No renames; precise prose.** Files, modules, scopes, checks, effect
    subjects and command IDs keep their names. "Brain registration" means a
    row in the vault registry; "workspace registration" means creating or
    attaching a `living/workspace` hub; "MCP registration" means a record in
    the MCP registration ledger (DD-078). Where a kept command ID changes
    behaviour, its version is bumped.
11. **Migration and mixed versions.** No automatic migration of `brains.json`
    rows into the vault registry (they were derived from it) and no deletion.
    Linked workspace registry rows survive and are verified lazily. An older
    launcher's bundled Doctor still writes and inspects the derived file,
    which stays self-consistent and is corrected by `brain upgrade`. The
    configuration migration is versioned at the release that ships it.
12. **An absent location is reported, never unhealthy, and pruning never runs
    blind.** The machine cannot tell an unplugged drive from a deleted folder,
    so one rule covers every location it cannot see. The MCP inventory's
    coverage verdict splits its causes: `unreachable` names each registered
    Brain root with no `.brain-core/VERSION` and each linked folder that is
    not a directory; `invalid` is everything else (an unsafe or malformed
    ledger, journal or registry, or a folder that is present but cannot be
    inspected). Of the coverage causes, only `invalid` counts against
    `machine.healthy` (MCP drift, runtime health and automatic or
    machine-owned findings still count), and a stale vault registry row is
    likewise reported (`stale_vault_registry`) and no longer unhealthy. Orphan-runtime pruning still needs both lists empty; when it is
    blocked, its reason names each location to reconnect or unregister, and
    `tidy` is false. MCP registration inspection reports an absent folder as
    `unreachable`, with that remedy and never `mcp migrate`. Doctor's payload
    lists the unreachable locations, and its approval inspection reports an
    approval target as `unreachable` when its folder is absent or when it
    cannot be judged while a registered Brain is unreachable; neither counts.
    `mcp.repair` (version 4) repairs the reachable targets at Brain and machine
    breadth and names the rest in a `follow_up_required` warning, except while
    approval records exist: the approval inventory, which needs every
    registered location, runs first and refuses. Uninstall, Brain
    unregistration, MCP migration and approval changes still need every
    registered location and refuse with the same remedy.

## Alternatives Considered

- Keeping `brains.json` as an add-only cache (DD-082 D19): rejected, because
  no reader needs a cache of a two-column text file and no shell fallback
  exists.
- An automatic registration family for the unregistered Brain: rejected,
  because choosing an identity is not a derived repair, and a vault that
  happens to be the current directory of a scheduled pass is not thereby a
  Brain the operator wants registered.
- A Brain-side `vault.check` finding for the unregistered Brain: rejected,
  because it would need request templating in two renderers and would warn in
  every isolated test vault.
- The Brain end as the source of truth of the link: rejected, because the
  Brain cannot verify a folder it cannot reach and a row goes stale on a move.
  Both ends co-equal: rejected, because that is today's half-operations. No
  registry at all: rejected, because it needs scanning.
- Keeping `workspace.bind`: rejected, because it writes one end and never
  maintains either Brain's registry, and `workspace.setup --force` already
  covers rebinding.
- A folder mutation lock during reconciliation: rejected, because
  `exclusive_file_lock` creates the lock file, a hidden write outside the
  Brain.
- Doctor reading the maintenance decisions file to decide health: rejected,
  because Doctor would then depend on maintenance state.
- Deleting a leftover `brains.json` on upgrade: rejected, because a released
  v0.70.10 Doctor on the same machine recreates it, and a delete-recreate loop
  between two versions is worse than an inert file.
- `session.start` writing the registry on contact: rejected, because
  bootstrap stays read-only; the missing-row finding plus `workspace setup`
  covers it.

## Consequences

- A fresh install and an upgraded install are healthy under Doctor without a
  derived file; `machine.healthy` loses its five derived-registry terms.
- `brain.doctor` is version 3, `workspace.unregister` is version 2,
  `workspace.repair-registry` is version 2, `mcp.repair` is version 4, and the
  launcher catalogue loses `machine-registry.sync`. A shipped command whose
  observable outcome changed moved to a new version: `brain.install` 5,
  `brain.upgrade` 3, `brain.register` 2, `brain.unregister` 2,
  `registry.remove-stale` 2, `brain.list` 2, `brain.resolve` 2 and
  `mcp.configure` 4. The rule covers observable changes for states that
  ordinary operations can produce. Moving a Brain is one, so `brain.upgrade`
  moves to 3 for drifted rows. A state that can arise only by hand-editing
  stored data against the registry's canonical invariant (a row with a `..`
  component or a trailing slash, which registration never writes) is
  outside the command contract, for application and launcher commands
  alike. So `mcp.migrate`, `brain.uninstall`, `brain.set-default` and the
  managed-approval commands keep their versions: where they meet a drifted
  row, they meet it in the strict Brain inventory (MCP migration, and every
  approval-wrapped change while approvals hold records), whose file reads
  already refused any registry row with a symlinked component, so a drifted
  row refused before and refuses now, with its recovery in the message
  instead. Application commands that reach Brain resolution keep their
  versions too: never following a row that is not its own canonical path is
  this decision's correctness rule, and it applies to every command alike.
- A linked workspace whose folder is moved is reported as unreachable and
  stays reported until `workspace setup` runs from the new location or
  `workspace unregister` forgets it, or the finding is dismissed; Doctor stays
  healthy meanwhile. A manifest edited to another Brain is dropped from the
  old Brain's registry by the pass; a row whose manifest is missing is kept and
  reported as unverifiable.
- Every install provisions the managed runtime whatever `--mcp-scope` is:
  managed CLI and direct commands (`brain session start` among them) need it
  as much as the MCP server does, so `skip` skips MCP registration only. A
  host without a usable Python 3.12 gets a failed runtime step and a note
  naming `brain runtime repair`, and Doctor then reports the runtime missing.
  `install.sh --skip-mcp` on an existing vault no longer defers the upgrade's
  runtime sync; `upgrade.py --no-sync-deps` remains the explicit way to.
- On `access.prepare`, a workspace planner's known refusal is a no-effect
  consent error, never an unknown outcome. One rule classifies it: a defect
  in the request or in the binding it names (already bound to another Brain
  without `force`, the vault root, a malformed manifest, an unknown key) is
  `invalid_request`; a failure to read or use local state (an unreadable
  binding or registry, a stale or missing router, a broken git checkout, a
  permission failure, a path that is not a file) is `conflict`, the class
  invoke reports for the same case. Only the prepare path converts: invoke
  keeps its own mapping, so a stale router keeps its cache details and the
  `runtime.refresh-router` next action.
- Removing command IDs is a breaking contract under the pre-1.0 rule, so the
  configuration migration ships with a minor version bump.
- A vault at a pre-0.71.0 Core whose profiles hold `workspace.bind` or
  `workspace.register` fails to load authorisation under a newer `dev` Core
  until `migrate_to_0_71_0.migrate` has been applied to it, because the
  migration only runs on an upgrade to 0.71.0. The loader gains no tolerance
  for retired IDs; development and lab use apply the migration first.
- Stored allow-lists are never widened automatically: a grant added to a
  template profile reaches stored profiles only when someone adds it.
- Row verification costs about four filesystem calls per reachable row and one
  per unreachable row inside Doctor and the passes; a dead network mount can
  stall it. Not bounded here. The registry repair classifies outside the vault
  lock and holds the lock only to recheck the disagreeing rows and write.

## Verification

`tests/test_install_then_doctor.py` proves a fresh `install.py` subprocess
(with a stand-in managed runtime, which a skip install does not provision) is
healthy under `doctor_machine.py` and `brain doctor`, with and without a
leftover `brains.json`, and that the machine pass with the leftover writes an
empty summary and leaves the file untouched; `tests/repo/test_no_derived_registry.py` is the
repository contract that no file under `src/` or `cli/` names the derived
file; `tests/test_machine.py` and
`tests/application/test_launcher_machine_maintenance.py` cover discovery, the
health rule and the pass without an automatic family.
`tests/test_resolution_purity.py` proves every resolution outcome and the
local Brain ID lookup leave the redirected machine homes, vaults and workspace
manifests byte-identical and take no file lock;
`tests/test_vault_registry.py` registers a Brain, moves it and leaves a symlink
at the old path, and shows the drifted row is stale to every reader, gives its
Brain no second ID under any requested ID, and round-trips through
remove-stale and `register` with the old ID; it also shows unregister refuses
only the ambiguous paths and the direct script forwards the literal path when
approvals are present; `tests/test_mcp_registration_parity.py` executes the
guidance for every stale shape (canonical and absent, canonical with
integrations, drifted to a Brain with and without integrations, an alias
pair, a dangling symlink, drifted to a non-Brain with integrations) and shows
remove-stale is named exactly where it succeeds and one refused row blocks it
for all; `tests/application/test_launcher_approvals.py` shows a drifted row
refuses remove-stale before any approval change while approvals hold records;
`tests/test_install_core.py` shows a skip install provisions the runtime and
registers no MCP; `tests/application/test_caller_workspace_owners.py` shows
each planner refusal reaches `access.prepare` with its consent reason and
that invoke keeps its own no-effect refusal.
`tests/test_registry_write_owners.py` proves the vault registry's writers,
derived from the module itself, are reached only from `install.py` and the
launcher's registry owners, and the linked workspace registry's only from
`workspace.setup`, `workspace.unregister`, the registry repair and the
historical migration that first wrote it.
`tests/application/test_caller_workspace_owners.py` covers `workspace.unregister`
removing both ends, dropping only the row for a folder that is not this link's,
and taking its two locks one after the other; `tests/test_migrate_to_0_71_0.py`
applies the configuration migration directly and loads the result through the
authorisation resolver.
`tests/application/test_workspace_checks.py` covers the link checks from both
ends, including every unverifiable cause, invalid hub keys, a CRLF manifest,
the file conditions and a workspace end that must not crash on a malformed
file; `tests/check/test_check_orchestration.py` pins that the Brain-end `info`
codes add only information to `vault.check`.
`tests/application/test_maintenance_pass.py` adds the `registry` family to the
current-state suite and covers a manifest that agrees, names yet another hub,
loses its other Brain, or a row that moves, between the repair's own
classification and its locked recheck (each keeps its row, and removing the
recheck fails them), a held vault lock, an other-Brain drop and a per-folder
digest showing the pass writes nothing in any linked folder.
`tests/application/test_caller_workspace_owners.py` proves a repair cannot run
between `workspace.setup`'s row and manifest writes, and
`tests/application/test_workspace_registry_repair_owner.py` and
`tests/repair/test_repair_scopes.py` cover the dropped rows, the dry run, the
explicit lossy rebuild and its unattended refusal.
`tests/test_mcp_registration_parity.py` proves an absent linked folder with an
MCP record leaves registrations healthy, names the folder in the coverage
verdict and lets `plan_repair` repair the reachable target; that a linked path
replaced by a file is absent, not invalid; and that the inventory reads `~`,
relative and NUL rows by the one row rule. The owner tests show a row MCP
commits inside the repair's locked window survives the repair's
compare-and-swap, that a file which tears between the repair's read and its
lock is never rebuilt, even with `allow_row_loss`, and that a relative row is
never resolved against the current directory by `workspace.unregister`;
`tests/application/test_launcher_machine_maintenance.py` runs the machine pass
over an uninspectable folder and a malformed registry.
`tests/test_install_then_doctor.py` runs deleted, re-keyed, other-Brain, moved
and unplugged links on an installed Brain through the pass, the maintenance
decisions and the launcher Doctor, which stays healthy with exit code 0 while
the folders are away and blocks pruning with a reason that names them.
