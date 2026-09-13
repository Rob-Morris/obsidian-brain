# Migration to v0.67.0

Flattens temporal date folders. Every file under a `yyyy-mm/` directory beneath
`_Temporal/` moves up past that segment, so temporal artefacts file flat under
their type root (`_Temporal/Logs/20260314-log.md`) or owner chain
(`_Temporal/Reports/project~brain/20260314-report~Title.md`). Wikilinks are
rewritten vault-wide through the shared move engine in one preflighted move
set; an existing destination stops the upgrade before any write so the
collision can be resolved explicitly. Vacated month folders are pruned; one
that still holds non-artefact content (for example `.DS_Store`) is left in
place and named in the migration's `warnings` — review and remove it with
`repair.py empty_folders`.

`_Archive` is left untouched, including the legacy `_Temporal/<Type>/_Archive/`
shape. Archived layout is a historical snapshot; `artefact.unarchive` re-files a
restored artefact by the current conventions.

The migration edits no definition files. Library-managed taxonomies are
updated by the post-upgrade definition sync; custom taxonomies created through
`type.create` are brought forward by sync's convention pass, which rewrites a
Naming folder ending in `yyyy-mm` to the flat folder and records the previous
value in the sync result. Preview both with `upgrade.py --dry-run`. Vaults with
`artefact_sync: skip` see remaining drift as `taxonomy_conventions` findings in
`vault.check` until they run `sync_definitions.py`.

The migration removes the lexical retrieval index and the semantic embeddings
sidecars so the post-upgrade retrieval repair rebuilds them for the new paths
(a rename preserves mtimes, so the caches would otherwise report themselves
fresh). It declares every file in the shared wikilink rewrite surface plus all
move destinations for upgrade rollback snapshots; the derived caches are
deliberately not declared because they rebuild from the restored markdown.

## Verification

- `vault.check` reports no `taxonomy_conventions` findings once sync has run.
- No `yyyy-mm` directory remains under `_Temporal/` outside `_Archive`.
- `artefact.search` returns a moved artefact at its new path.
- Wikilinks to moved artefacts resolve (`brain links check`).

## Manual steps for agents without MCP tools

1. For each file under `_Temporal/<Type>/[<owner chain>/]yyyy-mm/`, move it up
   one level so the `yyyy-mm` folder disappears, and update every wikilink
   that used the month path.
2. In every temporal taxonomy under `_Config/Taxonomy/Temporal/`, change the
   Naming folder from `` `_Temporal/<Type>/yyyy-mm/` `` to `` `_Temporal/<Type>/` ``.
3. Remove the empty month folders.
4. Rebuild the search index (`build_index.py`) or run `repair.py lexical`.
