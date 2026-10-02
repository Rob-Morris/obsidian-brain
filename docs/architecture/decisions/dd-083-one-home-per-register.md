# DD-083: One home per register

**Status:** Accepted (implemented in stages)
**Extends:** DD-052, DD-078, DD-082
**Amends:** DD-051 (§2), DD-053

Items 1, 2, 3, 5, 7, 8, 9 and 10 and the amendments to DD-051 and DD-053 are
in the code, as is item 11's configuration migration. Items 4 and 6, the rest
of item 11 and the consequences that follow from them are staged: they record
the decision and land in later changes, after which this text is reconciled
with the code.

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
   is withdrawn: it hid the missing owner.
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
   the Brain end, `collect_registry_check_findings` emits
   `workspace_registry_malformed` and `workspace_link_disagreement` (a
   reachable, readable manifest naming another hub key or a Brain resolving to
   a different vault on this machine) as `warning` findings repaired by the
   now-automatic `registry` family, and `workspace_link_unverifiable` (no
   manifest, unreadable manifest or unresolvable Brain ID) and
   `workspace_folder_unreachable` as `info` judgement findings, one per row,
   each with `file` `.brain/local/workspaces.json#<key>`. From the workspace
   end, `vault.check` reports `workspace_registry_missing` (`info`,
   report-only). Reconciliation never adds a row, never drops a row it cannot
   positively contradict, confirms a disagreeing manifest with a second read
   under the vault lock immediately before the registry write, and takes no
   lock and creates no file in any workspace folder.
7. **Doctor's payload describes the vault registry.** `machine.registry` is
   `state` (`current`, or `stale` when rows point at non-Brains), `path` and
   `brains_count`; the derived-registry fields are removed; counts gain
   `unregistered_brains`; `brain.doctor` moves to version 3.
8. **Doctor health counts per-Brain findings only when the machine owns or
   repairs them.** A per-Brain finding counts against `machine.healthy` only
   when its repair family is automatic or machine-owned; one without such a
   family is listed and never makes Doctor unhealthy, so a dismissal-free
   Doctor is never stuck unhealthy on a per-Brain finding. Stale vault
   registry rows, MCP registration drift and an unhealthy or legacy runtime
   still count, as before. `brain_unregistered` is a machine-pass kind the
   health formula does not read, so it is health-neutral by construction.
   The two staged link codes, `workspace_link_unverifiable` and
   `workspace_folder_unreachable`, are additionally emitted at `info`
   severity so the current vault's `vault.check` exit code, which Doctor's
   overall result also folds in, stays 0.
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
- `brain.doctor` is version 3, `workspace.unregister` is version 2, and the
  launcher catalogue loses `machine-registry.sync`.
- A linked workspace whose folder is moved is reported as unreachable and
  stays reported until `workspace setup` runs from the new location or
  `workspace unregister` forgets it; a manifest edited to another Brain is
  dropped from the old Brain's registry by the pass; a row whose manifest is
  missing is kept and reported as unverifiable.
- Removing command IDs is a breaking contract under the pre-1.0 rule, so the
  configuration migration ships with a minor version bump.
- A vault at a pre-0.71.0 Core whose profiles hold `workspace.bind` or
  `workspace.register` fails to load authorisation under a newer `dev` Core
  until `migrate_to_0_71_0.migrate` has been applied to it, because the
  migration only runs on an upgrade to 0.71.0. The loader gains no tolerance
  for retired IDs; development and lab use apply the migration first.
- Stored allow-lists are never widened automatically: a grant added to a
  template profile reaches stored profiles only when someone adds it.
- Row verification does one `isdir` per row inside Doctor and the passes, the
  same call `vault_registry.list_entries` already makes per vault-registry
  row; a dead network mount can stall it. Not bounded here.

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
