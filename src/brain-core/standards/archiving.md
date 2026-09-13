# Archiving Artefacts

Any artefact can be archived to remove it from the active vault namespace,
regardless of lifecycle status. This includes statusless temporal Thoughts.
Use the `artefact_archive` MCP tool (canonical command `artefact.archive`).
Archiving preserves status; deletion is a separate administrator command.

## What `artefact.archive` does

1. Validates the artefact, ownership constraints and destination
2. Adds `archiveddate: YYYY-MM-DD` to frontmatter
3. Adds an archival `yyyymmdd-` prefix unless the naming rule already requires a date
4. Moves the file to `_Archive/{Type}/{Project}/` at the vault root
5. Updates all wikilinks vault-wide

If the artefact has living descendants, the default operation stops with a
`HAS_DESCENDANTS` result. Pass `recursive: true` to archive the complete known
ownership subtree in one preflighted move set.

```
Ideas/Brain/my-idea.md  →  _Archive/Ideas/Brain/20260405-my-idea.md
```

## What `artefact.unarchive` does

1. Removes the archival date prefix while preserving dates required by the naming rule
2. Restores the type/ownership folder and current terminal-status placement
3. Removes `archiveddate` from frontmatter
4. Updates all wikilinks vault-wide

Pass `recursive: true` to restore the complete known ownership subtree through
the current parent, naming, and status projections. The result reports
`uninspected` archived candidates when an unreadable or unknown-type file means
Brain cannot prove the subtree scan was complete; inspect those paths before
treating the restoration as complete.

## Top-level `_Archive/`

All archived files live under a single `_Archive/` directory at the vault root, preserving the original type and project structure inside:

```
_Archive/
  Ideas/
    Brain/
      20260101-my-idea.md
  Designs/
    20260315-old-design.md
```

The layout inside `_Archive/` is a historical snapshot of where each file sat when it was archived; `artefact.unarchive` re-files by the current conventions (flat under the type root, or under the owner chain), not by the archived path.

This single location is excluded from the vault file index, search, listing, and all normal artefact operations.

## Accessing archived files

Normal active-namespace operations do not interact with `_Archive/`. Select the archive namespace explicitly when reading or listing:

| Operation | Tool |
|-----------|------|
| **List** | `artefact.list(location="archived")` |
| **Read** | `artefact.read(reference=..., location="archived")` |
| **Restore** | `artefact.unarchive`; use its explicit recursive field for a subtree |

No edit, search, or create operations work on archived files.

## Wikilink hygiene

The rename in the archive action disambiguates the archived file from any successor that reuses the original name. After archiving:

- **Supersession callout** (on the archived file): link to the successor using a path-qualified wikilink — `[[Designs/Brain Workspaces]]`
- **Origin link** (on the successor): link back using the renamed identifier — `[[20260324-Brain Workspaces|Workspaces Idea]]`
- **All other existing links** to the original name naturally resolve to the successor since the archived file no longer shares that name

## Ownership-subtree archiving

Recursive archive follows canonical living `parent:` metadata rather than
assuming every file physically below a folder belongs to that owner. Each
known descendant is planned into its archive destination, the complete move
set is collision-checked, then metadata and wikilinks are updated. Recursive
unarchive uses the same authoritative relationships in reverse; folder shape
is never used to invent missing parent metadata.

## Notes

- Every artefact type supports archiving; terminal status is not a prerequisite
- `_Archive/` is excluded from the vault file index, search, and all normal operations
- Archived files are frozen snapshots — their internal wikilinks are not updated on rename operations
- Legacy per-type `_Archive/` directories (e.g. `Ideas/_Archive/`) are supported for backward compatibility but new archives go to the top-level `_Archive/`

Archive, restore and delete reconcile the active router and lexical index before
returning success. A derived refresh failure reports committed effects and the
commands needed to repair the index. Search AUTO can use lexical results while
semantic sidecars await rebuilding.
