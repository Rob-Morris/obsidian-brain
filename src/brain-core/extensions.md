# Extensions

When the vault needs a new artefact type, follow the procedure for the relevant tier. Log what was added and why in the day's log.

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
2. Choose a base hue and apply the blend formula (`result = base + (steel - base) × 0.35`) to derive the steel-tinted variant — see [[.brain-core/colours]].
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

## Extending Principles

System-level always-rules live in `index.md`'s `Always:` section. Vault-specific additions go in the router's `Always:` section. Add each as a bullet with a short description explaining the constraint. The compiler merges both — system rules first, vault additions after.

---

See [[.brain-core/library]] for ready-to-use artefact type definitions.
