# Linking

How wikilinks work in the vault and how to keep them healthy.

## Resolution

Obsidian resolves wikilinks by **basename** — case-insensitive, no extension needed.

- `[[My Page]]` matches any `My Page.md` regardless of folder depth
- `[[My Page#heading]]` links to a heading within that file
- `[[My Page|display text]]` shows custom text
- `![[image.png]]` embeds a file (images, PDFs, etc.)

Path-qualified links like `[[Wiki/My Page]]` match only if the file sits at that exact relative path. They **do not** match files in subfolders — `[[Wiki/My Page]]` will not find `Wiki/Projects/My Page.md`.

## Convention

**Use basename-only links by default.** They survive folder moves, archiving, and subfolder reorganisation.

- **Temporal artefacts**: always basename. Dated filenames (`20260329-decision~JWT Refresh Strategy`) are naturally unique across the vault.
- **Living artefacts**: basename by default. `brain_create` auto-disambiguates collisions (see below).
- **Path-qualified links**: avoid. They break when files move into subfolders, get archived, or get reorganised.

## Namespace Collisions

A collision happens when two files share the same basename (e.g. `Wiki/JWT Refresh.md` and `Ideas/JWT Refresh.md`). Every `[[JWT Refresh]]` link becomes ambiguous — Obsidian picks whichever is "closest", which may not be what the author intended.

**Automatic disambiguation:** `brain_create` handles collisions automatically:

- **Cross-folder** (same basename in a different type folder): appends the type key — `JWT Refresh (ideas).md`. Links use the full name: `[[JWT Refresh (ideas)]]`.
- **Same-folder** (duplicate title in the same type): appends a random 3-character suffix — `JWT Refresh k7f.md`.

The original file always keeps its clean name. The compliance checker flags ambiguous links at `info` severity.

## Link Maintenance

When renaming or deleting files programmatically, always use the link-aware tools:

- **Rename**: `rename_and_update_links()` in `scripts/rename.py` — renames the file and rewrites all wikilinks pointing to the old name
- **Delete**: `delete_and_clean_links()` in `scripts/rename.py` — removes the file and replaces wikilinks with strikethrough text
- **Bulk rename**: `scripts/migrate_naming.py` — uses `rename_and_update_links()` for every rename in a naming convention migration

Never rename `.md` files with raw `os.rename()` or `mv` — this silently breaks every wikilink pointing to the old name.

The compliance checker (`scripts/check.py`) detects:

- **Broken wikilinks** (`warning`) — target file does not exist
- **Ambiguous wikilinks** (`info`) — basename matches multiple files

## For Agents Without MCP Tools

If you're working with the vault directly (no `brain_create` / `brain_action` tools):

1. Before creating a file, search for existing files with the same basename
2. When linking, use the basename only — don't include folder paths
3. If you rename a file, grep for `[[old-name` across all `.md` files and update matches
4. If you delete a file, grep for `[[filename` and replace links with strikethrough or remove them
