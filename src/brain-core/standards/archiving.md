# Archiving Living Artefacts

When a living artefact reaches a terminal status (e.g. `implemented` for designs, `graduated` for ideas), it can be moved to `{Type}/_Archive/` to keep the active folder clean. The general pattern:

1. Set the terminal status in frontmatter
2. Add `archiveddate: YYYY-MM-DD` to frontmatter
3. Add a supersession callout linking to the successor (the file that now holds authority)
4. Rename the file to `yyyymmdd-{slug}.md` using `brain_action("rename")` — this updates all wikilinks vault-wide automatically (Obsidian CLI first, grep-replace fallback)
5. Move the file to `{Type}/_Archive/`

Notes:
- `brain_action("rename")` handles wikilink updates automatically — no manual link maintenance needed
- Not all types need archiving — only types with terminal statuses opt in. Each type's taxonomy defines its own archiving rules
- `_Archive/` is a system subfolder (starts with `_`), so it's automatically excluded from indexing and search
