# Artefact Library Guide

The canonical index of artefact types lives at [[.brain-core/artefact-library/README]]. This file explains how to use it.

## Browsing the Library

Each type has its own directory with four files:

| File | Purpose |
|---|---|
| `README.md` | Overview, suggested colour, install paths |
| `taxonomy.md` | Conventions, naming, frontmatter — copy to `_Config/Taxonomy/` |
| `template.md` | Obsidian template — copy to `_Config/Templates/` |
| `style.css` | Suggested CSS — merge into `.obsidian/snippets/folder-colours.css` |

## Installing a Type

1. Copy `taxonomy.md` → `_Config/Taxonomy/{Living|Temporal}/{key}.md`
2. Copy `template.md` → `_Config/Templates/{Living|Temporal}/{Type Name}.md`
3. Create the storage folder (e.g. `_Temporal/{Type Name}/` or `{Type Name}/`)
4. Merge `style.css` into `.obsidian/snippets/folder-colours.css` — add the colour variable to the Themes `:root` block and the selector blocks to the appropriate section
5. Update `_Config/Styles/obsidian.md` with the new colour assignment
6. Optionally add a conditional trigger to `_Config/router.md`

Each type's README includes specific paths and an optional router trigger.

## Colour System

Living types use a palette colour directly. Temporal types blend a base colour 35% towards rose. See [[.brain-core/colours]] for the full palette, blend formula, and CSS selector templates.
