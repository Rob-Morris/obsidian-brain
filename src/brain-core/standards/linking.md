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
- **Living artefacts**: basename by default. Check for collisions before creating (see below).
- **Path-qualified links**: avoid. They break when files move into subfolders, get archived, or get reorganised. Use only to resolve a known collision where renaming isn't practical.

## Namespace Collisions

A collision happens when two files share the same basename (e.g. `Wiki/jwt-refresh.md` and `Ideas/jwt-refresh.md`). Every `[[jwt-refresh]]` link becomes ambiguous — Obsidian picks whichever is "closest", which may not be what the author intended.

**Before creating a living artefact**, check whether the basename already exists in another type folder. If it does, differentiate the name:

- `jwt-refresh` (wiki page) and `jwt-refresh-design` (design doc)
- `auth-redesign` (design) and `auth-redesign-research` (research notes)

Do not create duplicate basenames and rely on path-qualification to disambiguate — the path breaks on subfolder or archive moves.

The compliance checker flags ambiguous links at `info` severity. `brain_create` warns when a new file would create a collision.

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
