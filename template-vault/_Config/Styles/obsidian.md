# Obsidian Style

Custom styling for this vault's Obsidian file explorer. For palette design and CSS templates, see [[.brain-core/colours]].

**Active CSS:** `.obsidian/snippets/folder-colours.css`

## System Folder Colours

System folders (`_` prefixed) have fixed colours. These are reserved — never assign them to living artefacts.

| Folder | Colour | Variable |
|--------|--------|----------|
| `_Attachments/` | Slate | `--theme-attachments-fg` / `--theme-attachments-bg` |
| `_Config/` | Purple | `--theme-config-fg` / `--theme-config-bg` |
| `_Temporal/` | Rose | `--theme-temporal-fg` / `--theme-temporal-bg` |
| `_Plugins/` | Gold | `--theme-plugins-fg` / `--theme-plugins-bg` |

## _Archive Subfolders

`_Archive/` subfolders within artefact folders use the same slate styling as `_Attachments/`. This visually signals "infrastructure, not active content". Wildcard CSS selectors apply to any artefact type's `_Archive/` subfolder automatically — no per-folder CSS needed.

| Folder | Colour | Variable |
|--------|--------|----------|
| `{Type}/_Archive/` | Slate | `--theme-attachments-fg` / `--theme-attachments-bg` |

## Living Artefact Colours

All living artefact folders share a rose gold background tint (`--theme-artefact-bg`). Each folder gets a unique foreground colour from the palette. Never reuse a system folder colour (purple, steel, gold, slate) for a living artefact.

| Folder | Colour | Variable |
|--------|--------|----------|
| `Wiki/` | Rose | `--color-wiki` |
| `Daily Notes/` | Sky | `--color-daily-notes` |
| `Notes/` | Teal | `--color-notes` |

## Temporal Child Colours

Temporal children share a rose background tint. Each gets a unique foreground derived by blending a base palette colour 35% towards rose.

| Folder | Colour | Variable |
|--------|--------|----------|
| `_Temporal/Logs/` | Amber → rose | `--color-temporal-logs` (`#F3BD93`) |
| `_Temporal/Transcripts/` | Lavender → rose | `--color-temporal-transcripts` (`#CCA9DB`) |
| `_Temporal/Plans/` | Coral → rose | `--color-temporal-plans` (`#F198A2`) |
| `_Temporal/Research/` | Teal → rose | `--color-temporal-research` (`#A5C5CD`) |
| `_Temporal/Decision Logs/` | Sage → rose | `--color-temporal-decision-logs` (`#B2BEA1`) |
| `_Temporal/Friction Logs/` | Sky → rose | `--color-temporal-friction-logs` (`#AFB2DB`) |
| `_Temporal/Cookies/` | Cookie dough → rose | `--color-temporal-cookies` (`#DDA793`) |
| `_Temporal/Shaping Transcripts/` | Lavender → rose | `--color-temporal-shaping-transcripts` (`#CCA9DB`) |
