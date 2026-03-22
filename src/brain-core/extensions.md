# Extensions

When the vault needs a new artefact type, follow the procedure for the relevant tier. Log what was added and why in the day's log.

## When to Add a New Type

Before creating a new artefact type, check these criteria:

- **No existing type fits** — the content doesn't belong in any current folder, even with generous interpretation
- **Recurring pattern** — you expect multiple files of this kind, not just one
- **Distinct lifecycle** — the content has different naming, frontmatter, or archiving rules from existing types
- **Worth the overhead** — each type needs a taxonomy file and a router trigger (colours are auto-generated). If the content is a one-off, a subfolder or tag within an existing type may be simpler

## Adding a Living Artefact Folder

1. Create the folder at vault root.
2. Add a conditional trigger to the router if the type has one.
3. Create a taxonomy file at `_Config/Taxonomy/Living/{name}.md` describing the type's purpose, conventions, and template.
4. Run `brain_action("compile")` to regenerate the router and colours — CSS is auto-generated.
5. Log the addition.

## Adding a Temporal Child Folder

1. Create the folder under `_Temporal/`.
2. Add a conditional trigger to the router if the type has one.
3. Create a taxonomy file at `_Config/Taxonomy/Temporal/{name}.md` describing the type's purpose, conventions, and template.
4. Run `brain_action("compile")` to regenerate the router and colours — CSS is auto-generated, rose blend applied automatically.
5. Log the addition.

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

## Hub Pattern

Some living artefact types act as hubs — containers that group related artefacts via nested tags. The pattern:

1. The hub file is a living artefact with a nested tag: `{type}/{slug}` (e.g. `project/my-app`, `journal/personal`)
2. All related artefacts (temporal or living) carry the same nested tag
3. The hub is the index; the tag is the query mechanism

This is useful when a stream of related work or content needs a single living touchpoint. The hub file describes the stream and links to key artefacts; the tag makes everything findable.

**Current examples:**
- **Projects** — `project/{slug}` groups plans, research, designs, logs, and other artefacts related to a project
- **Journals** — `journal/{slug}` groups journal entries belonging to a named journal stream

**When to use:** When you need a living artefact that organises a collection of other artefacts (especially temporal ones) rather than containing content itself. If the living artefact is primarily content (like a wiki page or design doc), tags alone suffice — the hub pattern adds an explicit index file.

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
