# Extensions

When the vault needs a new artefact type, follow the procedure for the relevant tier. Log what was added and why in the day's log.

## When to Add a New Type

Before creating a new artefact type, check these criteria:

- **No existing type fits** — the content doesn't belong in any current folder, even with generous interpretation
- **Recurring pattern** — you expect multiple files of this kind, not just one
- **Distinct lifecycle** — the content has different naming, frontmatter, or archiving rules from existing types
- **Worth the overhead** — each type needs a taxonomy file, colour assignment, CSS selectors, and a router trigger. If the content is a one-off, a subfolder or tag within an existing type may be simpler

## Adding a Living Artefact Folder

1. Create the folder at vault root.
2. Pick a palette colour (or add a new `--palette-*` variable) and add a `--color-*` variable in the Themes block of `.obsidian/snippets/folder-colours.css`. Never reuse a system folder colour (purple, steel, gold, slate) — those are reserved for `_Config/`, `_Temporal/`, `_Plugins/`, and `_Attachments/`.
3. Add three CSS selector blocks (folder + subfolders, border, files) — see [[.brain-core/colours]] for the template.
4. Add a conditional trigger to the router if the type has one.
5. Create a taxonomy file at `_Config/Taxonomy/Living/{name}.md` describing the type's purpose, conventions, and template.
6. Update `_Config/Styles/obsidian.md` with the new colour assignment.
7. Log the addition.

## Adding a Temporal Child Folder

1. Create the folder under `_Temporal/`.
2. Choose a base hue and apply the blend formula (`result = base + (rose - base) × 0.35`) to derive the rose-tinted variant — see [[.brain-core/colours]].
3. Add a `--color-temporal-*` hex in the Themes block and add CSS selectors with `background-color: var(--theme-temporal-bg)` and `border-radius: 4px`.
4. Add a conditional trigger to the router if the type has one.
5. Create a taxonomy file at `_Config/Taxonomy/Temporal/{name}.md` describing the type's purpose, conventions, and template.
6. Update `_Config/Styles/obsidian.md`.
7. Log the addition.

## Adding a Config Child Folder

1. Create the folder under `_Config/`.
2. No CSS changes needed — inherits config purple styling.
3. Document in the router if relevant.

## Adding a Plugin Folder

1. Create the folder under `_Plugins/`.
2. No CSS changes needed — inherits gold plugin styling.
3. Document in the router.
4. Create a skill in `_Config/Skills/` if the plugin has MCP tools or CLI commands.

## Archiving Living Artefacts

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

## Artefact Provenance

When an artefact spins out of another (e.g. an idea graduating to a design, or a log entry becoming a standalone note), record the lineage in both files:

**On the new artefact:**
- Add `**Origin:** [[source-file|description]] (yyyy-mm-dd)` in the body

**On the source artefact:**
- Add a callout **at the top of the body** (after frontmatter, before other content) so agents and humans hit it immediately:

```markdown
> [!info] Spun out to {type}
> [[new-file]] — yyyy-mm-dd
```

**Terminal status:** If the source artefact has transferred all authority to the new one, set the terminal status in frontmatter and archive if the type supports it. Otherwise the callout alone suffices — the source remains active.

Individual artefact taxonomy files may document specific provenance patterns (e.g. idea graduation, log spinout) with their own terminology, but the underlying mechanism is always this: origin link on the child, callout on the parent.

## Subfolders Within Living Artefact Folders

Artefacts start as flat files in their type folder. Structure is not planned upfront — it emerges when a single logical work grows to span multiple files.

When a project, book, or other complex work outgrows a single file, a subfolder appears naturally:

- One file in the subfolder acts as the **index** (`index.md` or the project slug, e.g. `my-book.md`)
- The subfolder inherits the parent artefact type — no separate taxonomy or CSS needed
- CSS prefix selectors (e.g. `[data-path^="Writing/"]`) handle nested content automatically

This is organic growth, not upfront architecture. Common evolution patterns (e.g. "a book might grow chapters in a subfolder") can be documented in individual artefact taxonomy files as cues, without being prescriptive.

## User Preferences

`_Config/User/` holds per-vault user preferences that agents read every session:

- `preferences-always.md` — standing instructions: workflow preferences, quality standards, agent behaviour rules
- `gotchas.md` — learned lessons and known pitfalls from previous sessions, added incrementally

Both files are freeform markdown. Content is up to the vault owner — Brain provides the convention and the empty files, not the content.

## Extending Principles

System-level always-rules live in `index.md`'s `Always:` section. Vault-specific additions go in the router's `Always:` section. Add each as a bullet with a short description explaining the constraint. The compiler merges both — system rules first, vault additions after.

---

See [[.brain-core/library]] for ready-to-use artefact type definitions.
