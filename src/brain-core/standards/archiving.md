# Archiving Living Artefacts

When a living artefact reaches a terminal status (e.g. `implemented` for designs, `adopted` for ideas), it can be archived to remove it from the active vault namespace. Use `brain_move(op="archive", path="...")` — it handles everything automatically.

## What `brain_move(op="archive", path="...")` does

1. Validates the artefact has a terminal status
2. Adds `archiveddate: YYYY-MM-DD` to frontmatter
3. Renames the file to `yyyymmdd-{Title}.md` (disambiguates from any successor)
4. Moves the file to `_Archive/{Type}/{Project}/` at the vault root
5. Updates all wikilinks vault-wide

```
Ideas/Brain/my-idea.md  →  _Archive/Ideas/Brain/20260405-my-idea.md
```

## What `brain_move(op="unarchive", path="...")` does

1. Strips the `yyyymmdd-` date prefix from the filename
2. Moves the file back to its original type folder
3. Removes `archiveddate` from frontmatter
4. Updates all wikilinks vault-wide

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

This single location is excluded from the vault file index, search, listing, and all normal artefact operations.

## Accessing archived files

Normal tools (`brain_read`, `brain_edit`, `brain_search`, `brain_list`) do not interact with `_Archive/`. Use dedicated operations:

| Operation | Tool |
|-----------|------|
| **List** | `brain_list(resource="archive")` |
| **Read** | `brain_read(resource="archive", name="_Archive/...")` |
| **Restore** | `brain_move(op="unarchive", path="_Archive/...")` |

No edit, search, or create operations work on archived files.

## Wikilink hygiene

The rename in the archive action disambiguates the archived file from any successor that reuses the original name. After archiving:

- **Supersession callout** (on the archived file): link to the successor using a path-qualified wikilink — `[[Designs/Brain Workspaces]]`
- **Origin link** (on the successor): link back using the renamed identifier — `[[20260324-Brain Workspaces|Workspaces Idea]]`
- **All other existing links** to the original name naturally resolve to the successor since the archived file no longer shares that name

## Project archiving

When a whole project reaches terminal status, the entire subfolder moves as-is:

```
Designs/Brain/  →  _Archive/Designs/Brain/
```

Any inner `_Archive/` that existed within the project comes along — no flattening needed.

## Notes

- Not all types need archiving — only types with terminal statuses opt in
- `_Archive/` is excluded from the vault file index, search, and all normal operations
- Archived files are frozen snapshots — their internal wikilinks are not updated on rename operations
- Legacy per-type `_Archive/` directories (e.g. `Ideas/_Archive/`) are supported for backward compatibility but new archives go to the top-level `_Archive/`
