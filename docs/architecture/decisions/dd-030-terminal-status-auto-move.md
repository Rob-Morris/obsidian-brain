# DD-030: Terminal status auto-move

**Status:** Implemented

## Context

Artefact types define a set of `terminal_statuses` in the router (e.g., `implemented`, `superseded` for decisions; `cancelled`, `done` for tasks). When an artefact reaches a terminal status, it is conceptually finished. However, finished artefacts often remain in the same folder as active work, creating visual noise in Obsidian's file explorer.

The alternative — requiring humans or agents to manually move files after status changes — is error-prone and easy to forget.

## Decision

When `brain_edit` applies a frontmatter change that sets a terminal status, `_maybe_status_move()` automatically moves the artefact into a `+{Status}/` subfolder within its current directory (e.g., `Decisions/+Implemented/dd-042-foo.md`). If a non-terminal status is set and the file is currently in a `+Status/` folder, it is moved back to the parent directory. Empty `+Status/` folders are removed after such a "revive" move.

The `+` prefix is chosen for `+Status/` folders because:
1. `+` sorts before letters and most special characters in most file explorers, placing terminal-status subfolders near the top where they are easily collapsed.
2. It is visually distinct from underscore-prefixed system directories (`_Archive`, `_Config`) and dot-prefixed hidden directories.
3. It avoids colliding with legitimate folder names.

Auto-move does not apply to artefacts already inside `_Archive/` (manual location) or to non-artefact resources (skills, memories, styles, templates).

If a file is already in a `+Status/` folder when a new terminal status is set, the move resolves relative to the grandparent to avoid nesting (`+Implemented/+Superseded/`).

## Consequences

- Agents never need explicit move instructions after a status change — the status edit is the complete operation.
- Obsidian users see terminal artefacts grouped in collapsible subfolders, keeping active work uncluttered.
- Wikilinks are updated during the move via `rename_and_update_links()`, so references remain valid.
- The router's `terminal_statuses` list is the single source of truth; adding a new terminal status automatically enables auto-move for it.
