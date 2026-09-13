# DD-072: Migrations never touch definition files; definition sync owns convention updates

**Status:** Implemented (v0.67.0)
**Extends:** DD-009

## Context

A vault's artefact definitions (`_Config/Taxonomy/**`, templates) come from two
sources. Library-managed types are installed from `artefact-library/` and
tracked in `.brain/tracking.json`; definition sync compares upstream,
installed and local hashes and applies safe updates after every upgrade under
the `artefact_sync` preference. Custom types created through `type.create`
have no manifest and no tracking entry, so sync never sees them — yet
self-extension is a core product promise, and a custom temporal type is
expected to keep working across releases without hand maintenance.

When a filing convention changes (DD-071 flattens temporal folders), every
temporal taxonomy's Naming folder must change with it. Three homes were
possible for that rewrite: the migration, the check/repair machinery, or
definition sync. Earlier migrations had hand-edited managed definitions and
patched tracking hashes in the same breath (`migrate_to_0_29_0`), which
couples migrations to sync's internal bookkeeping and flips a file into a
both-sides-changed conflict if the hash patch is missed.

## Decision

Migrations never edit definition files, managed or unmanaged. They move and
rewrite artefacts only.

Definition sync owns definition propagation in both populations:

- **Managed definitions** update from the library as today: `sync_ready`
  files are overwritten and their source hash recorded; locally modified files
  become conflicts resolved through sync's workflow or `--force`.
- **Unmanaged definitions** are brought forward by a **convention pass**: a
  rule table beside the taxonomy parser (`compile_router.CONVENTION_RULES`,
  one rule at v0.67.0 — a temporal Naming folder ending in `yyyy-mm` becomes
  the flat folder) is applied to every taxonomy under `_Config/Taxonomy/`
  that no manifest or tracking entry owns. The matcher acts on the *parsed*
  folder token, never on prose, and only the backticked token inside
  `## Naming` is rewritten (`compile_router.rewrite_naming_folder`, so the
  parser and the rewriter agree on the format by construction). Exact matches
  apply (the analogue of `sync_ready`) and the previous value is recorded on
  the result entry; anything a rule matches but cannot rewrite unambiguously
  is preserved and warned (the analogue of a conflict). The pass runs as a
  separate traversal so it never writes a tracking entry, which would
  silently make a custom type managed. The managed set fails closed: every
  library type directory claims its conventional taxonomy target even when
  its manifest cannot be parsed.

Checks surface drift but do not fix it: `check_taxonomy_conventions` is a
router-driven `info` finding pointing at definition sync. It consults the same
rule table as the pass, so its matcher cannot drift from what sync rewrites.
The two enumerate their populations differently by design — the check reads
the compiled router (DD-009), the pass walks `_Config/Taxonomy/` — so the
sets are not identical: a managed library taxonomy is flagged by the check but
repaired through the manifest loop rather than the pass, a type listed in
`artefact_sync_exclude` is flagged but left for the user, and a custom
taxonomy whose content directory does not yet exist is rewritten by the pass
but invisible to the router-driven check.

The pass inherits sync's surfaces and consent model: `upgrade --dry-run`
previews it, `artefact_sync: skip` (or `upgrade.py --no-sync`) skips it with
the rest of sync, the unscoped `sync_definitions.py` CLI runs it, and it
re-offers on every future upgrade. `type.sync` is always scoped to one type
and therefore never runs the pass.

## Alternatives Considered

- **Migration rewrites definitions, patching tracking hashes** (the
  `migrate_to_0_29_0` mould). Rejected: couples migrations to sync internals
  and runs after the last rollback point with no core source to restore from.
- **Migration rewrites only unmanaged definitions.** Rejected: the rewrite
  would live in a one-shot script with no preview, no re-offer and no
  relationship to the consent preference the user already set for definition
  changes.
- **Check + repair scope rewrites definitions.** Rejected as the primary home:
  check/repair is an after-the-fact backstop for vault integrity (broken
  links, drifted folders); the convention change is known at upgrade time and
  belongs in the step that already owns definition changes. The check remains
  as the surface for `artefact_sync: skip` vaults.
- **Warn only; never touch user-authored definitions.** Rejected: leaves
  self-extension a second-class path and leaves stale month prose as a live
  vector for untooled agents.

## Consequences

- Sync now rewrites user-authored files, under its existing consent rules.
  `upgrade --dry-run` is the consent gate; the sync result records `previous`
  for every convention entry.
- Result entries from the pass join sync's `updated`/`warnings` lists with the
  same keys (`type`, `role`, `target`, `action: "convention"`) plus `rule`,
  `previous` and `folder`, so existing renderers and the post-upgrade router
  recompile (which fires on any updated `_Config/Taxonomy/` target) work
  unchanged; the human renderers name the folder change, and read errors from
  the pass are surfaced by the upgrade output rather than dropped.
- The pass is skipped when sync is scoped to explicit `--types`; it always
  runs for an unscoped sync.
- Future convention changes add a rule. The first applicable rule is applied
  per pass; a vault several conventions behind may need more than one sync
  run, and each run re-offers under the same consent model.
- Migrations README records the operating model.
