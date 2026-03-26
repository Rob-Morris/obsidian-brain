# Archiving Living Artefacts

When a living artefact reaches a terminal status (e.g. `implemented` for designs, `graduated` for ideas), it can be moved to `{Type}/_Archive/` to keep the active folder clean. The general pattern:

1. Set the terminal status in frontmatter
2. Add `archiveddate: YYYY-MM-DD` to frontmatter
3. Add a supersession callout linking to the successor (the file that now holds authority)
4. Rename the file to `yyyymmdd-{Title}.md` using `brain_action("rename")` — this updates all wikilinks vault-wide automatically (Obsidian CLI first, grep-replace fallback)
5. Move the file to `{Type}/_Archive/`

## Wikilink hygiene

The rename in step 4 disambiguates the archived file from any successor that reuses the original name (e.g. idea `brain-workspaces` graduating to design `brain-workspaces`). After archiving:

- **Supersession callout** (on the archived file): link to the successor using a path-qualified wikilink — `[[Designs/brain-workspaces]]` — so it can't accidentally resolve to the archived file itself
- **Origin link** (on the successor): link back using the renamed identifier — `[[20260324-brain-workspaces|Workspaces Idea]]` — so it resolves to the archived file, not itself
- **All other existing links** to the original name (`[[brain-workspaces]]`) naturally resolve to the successor since the archived file no longer shares that name

## Notes

- `brain_action("rename")` handles wikilink updates automatically — no manual link maintenance needed
- Not all types need archiving — only types with terminal statuses opt in. Each type's taxonomy defines its own archiving rules
- `_Archive/` is a system subfolder (starts with `_`), so it's automatically excluded from indexing and search
