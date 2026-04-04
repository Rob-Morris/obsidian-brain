# Archiving Living Artefacts

When a living artefact reaches a terminal status (e.g. `implemented` for designs, `adopted` for ideas), it can be moved to `{Type}/_Archive/` to keep the active folder clean. The general pattern:

1. Set the terminal status in frontmatter
2. Add `archiveddate: YYYY-MM-DD` to frontmatter
3. Add a supersession callout linking to the successor (the file that now holds authority)
4. Rename the file to `yyyymmdd-{Title}.md` using `brain_action("rename")` — this updates all wikilinks vault-wide automatically (Obsidian CLI first, grep-replace fallback)
5. Move the file to `{Type}/_Archive/`

## Wikilink hygiene

The rename in step 4 disambiguates the archived file from any successor that reuses the original name (e.g. idea `Brain Workspaces` graduating to design `Brain Workspaces`). After archiving:

- **Supersession callout** (on the archived file): link to the successor using a path-qualified wikilink — `[[Designs/Brain Workspaces]]` — so it can't accidentally resolve to the archived file itself
- **Origin link** (on the successor): link back using the renamed identifier — `[[20260324-Brain Workspaces|Workspaces Idea]]` — so it resolves to the archived file, not itself
- **All other existing links** to the original name (`[[Brain Workspaces]]`) naturally resolve to the successor since the archived file no longer shares that name

## Project subfolder archiving

When artefacts are organised in a project subfolder (see [[.brain-core/standards/subfolders]]), archiving follows two patterns depending on scope.

### Sub-artefact reaches terminal status (project still active)

The sub-artefact archives to `{Type}/{Project}/_Archive/` — the archive lives with the sub-artefacts, not in the type root's `_Archive/`. Follow the same steps (terminal status, archiveddate, rename, move).

```
Designs/
  Brain Master Design.md
  Brain/
    Brain Inbox.md                    <- still active
    _Archive/
      20260317-Brain Tooling Architecture.md   <- archived sub-artefact
```

### Whole project reaches terminal status

When the master artefact itself reaches terminal status, the entire subfolder (master included) moves to `{Type}/_Archive/{Project}/`. The inner `_Archive/` is flattened at this point — everything is terminal, so the distinction is redundant.

```
Designs/
  _Archive/
    Brain/
      Brain Master Design.md
      Brain Inbox.md
      20260317-Brain Tooling Architecture.md
```

## Notes

- `brain_action("rename")` handles wikilink updates automatically — no manual link maintenance needed
- Not all types need archiving — only types with terminal statuses opt in. Each type's taxonomy defines its own archiving rules
- `_Archive/` is a system subfolder (starts with `_`), so it's automatically excluded from indexing and search
