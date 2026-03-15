# Obsidian Style

Custom styling for this vault's Obsidian file explorer. For palette design and CSS templates, see [[.brain-core/colours]].

**Active CSS:** `.obsidian/snippets/folder-colours.css`

## System Folder Colours

System folders (`_` prefixed) have fixed colours. These are reserved — never assign them to living artefacts.

| Folder | Colour | Variable |
|--------|--------|----------|
| `_Config/` | Purple | `--theme-config-fg` / `--theme-config-bg` |
| `_Temporal/` | Steel | `--theme-temporal-fg` / `--theme-temporal-bg` |
| `_Plugins/` | Gold | `--theme-plugins-fg` / `--theme-plugins-bg` |

## Living Artefact Colours

All living artefact folders share a rose gold background tint (`--theme-artefact-bg`). Each folder gets a unique foreground colour from the palette. Never reuse a system folder colour (purple, steel, gold) for a living artefact.

| Folder | Colour | Variable |
|--------|--------|----------|
| `Wiki/` | Rose | `--color-wiki` |

## Temporal Child Colours

Temporal children share a steel background tint. Each gets a unique foreground derived by blending a base palette colour 35% towards steel.

| Folder | Colour | Variable |
|--------|--------|----------|
| `_Temporal/Logs/` | Amber → steel | `--color-temporal-logs` (`#D0BD95`) |
| `_Temporal/Transcripts/` | Lavender → steel | `--color-temporal-transcripts` (`#A8A9DD`) |
| `_Temporal/Plans/` | Coral → steel | `--color-temporal-plans` (`#C69DA8`) |
