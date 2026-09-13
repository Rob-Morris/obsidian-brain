# DD-071: Temporal artefacts file flat under their type root or owner chain

**Status:** Implemented (v0.67.0)
**Extends:** DD-009, DD-030

## Context

Temporal artefacts filed as `_Temporal/<Type>/[<owner chain>/]yyyy-mm/<file>`.
The month segment was introduced when temporal folders were expected to be
browsed by hand; every temporal filename already carries a `yyyymmdd-` prefix
(DD-030), so the segment duplicated information the filename provides, and
the ordering it offered is the ordering the filename gives.

In practice the segment cost more than it returned. Each owner chain fanned out
into one directory per month, most holding a handful of files; a deployed vault
measured 288 month directories over 1,673 files. Every lifecycle move that
crossed a month boundary vacated a directory, and until v0.66.1 nothing pruned
them. Path-qualified wikilinks had to carry the month, so a log backfilled for a
different month than its `created` date needed a `date_source` rule just to
land in the right folder, and any change to the rule invalidated links. Owner
projection, status folders and month folders composed into three independent
path segments that agents had to keep straight, and untooled agents following
the shipped prose regularly created month folders by hand in the wrong place.
DD-009's check catalogue carried a `month_folders` check whose only job was to
police the segment.

## Decision

Temporal filing is `{type root}/` for unparented artefacts and
`{type root}/{owner chain}/` for parented ones. There is no date segment.
Chronology comes from the dated filename, which the naming rule's
`date_source` still governs; `date_source` no longer has any folder role.

`resolve_folder` computes the flat path for every caller (create, edit,
convert, reparent, archive planning, ownership repair, shaping transcripts).
`_finish_artefact` relocates a temporal file only when its `parent` changes.
`compile_router.naming_storage_root` no longer special-cases a literal
`yyyy-mm` segment. The `month_folders` check is removed from DD-009's
catalogue; the `empty_folders` check and repair scope (v0.66.1) and the
`taxonomy_conventions` check (DD-072) join it.

`migrate_to_0_67_0` moves every file under a `yyyy-mm` directory beneath
`_Temporal/` up past that segment in one preflighted move set through the
shared move engine, prunes the vacated directories, declares the complete
wikilink rewrite surface for rollback, and removes the path-keyed retrieval
caches so the post-upgrade repair rebuilds them. It leaves `_Archive`
untouched: archived layout is a historical snapshot, and `artefact.unarchive`
re-files any artefact whose parent the router cannot confirm through current
conventions rather than restoring its recorded archive path. The migration
edits no definition files (DD-072).

## Alternatives Considered

- **Keep `yyyy-mm/`.** Rejected: the segment duplicates the filename prefix,
  multiplies directories per owner chain, and is the cause of the vacated
  folders and hand-created strays described above.
- **Replace with `yyyy/` year folders.** Rejected for now; the year is also in
  the filename, and a vault would need decades of history before a flat type
  root became unwieldy. The same migration machinery can reintroduce a year
  segment later; doing so must restore a date-segment rule in
  `naming_storage_root`, a convention rule in `compile_router.CONVENTION_RULES`,
  and a date-driven relocation trigger in `_finish_artefact` (which today
  relocates only on `parent` change, the sole determinant of a flat path) —
  and nothing in unarchive, which already re-files by current conventions.
- **Flatten `_Archive` in the same migration.** Rejected: archive is designed
  as out of operations (excluded from search, the active namespace and the
  wikilink-rewrite walker), a one-time migration is not licence to breach that,
  and the only path that resurrects an archived layout is unarchive, which is
  the seam that was fixed instead.
- **Keep `month_folders` inverted as a dedicated "no stray subfolder" check.**
  Not needed as a separate check: `parent_contract` already reports an
  unparented living file in a subfolder as an implied owner or an orphan, and
  lifting its living-only guard gives temporal files the same coverage for
  free.

## Consequences

- Temporal paths shorten by one segment; path-qualified wikilinks carry only
  the owner chain. Existing links are rewritten by the migration.
- `parent_contract` now covers unparented temporal files as it covers
  unparented living ones: a temporal file in a subfolder with no `parent`
  field is reported as an implied-owner or orphan finding, so a legacy month
  folder recreated by hand is still detected after `month_folders` is removed.
- `resolve_folder` no longer needs render fields; its `fields` parameter is
  removed.
- Archived artefacts keep whatever layout they had when archived. Parentless
  restores, and restores whose recorded parent no longer resolves, land at the
  type root rather than in the recorded owner chain — a behaviour change
  documented in `standards/archiving.md`.
- The breaking change ships as a minor release under the pre-1.0 semver policy
  (vault-structure change preserving the core model).

## Implementation Notes

- Shared matcher: `compile_router.legacy_month_folder(folder)` returns the
  flat replacement for a parsed Naming folder ending in `yyyy-mm`, or `None`;
  `compile_router.CONVENTION_RULES` scopes it to temporal taxonomies for both
  the check and the sync convention pass.
- Companion note: `scripts/migrations/migrate_to_0_67_0.md`.
