# Obsidian Style

Custom styling for this vault's Obsidian file explorer. For palette design and CSS templates, see [[.brain-core/colours]].

**Active CSS:** `.obsidian/snippets/brain-folder-colours.css`

## System Folder Colours

System folders (`_` prefixed) have fixed colours. These are reserved — never assign them to living artefacts.

| Folder | Colour | Variable |
|--------|--------|----------|
| `_Assets/` | Slate | `--theme-assets-fg` / `--theme-assets-bg` |
| `_Config/` | Purple | `--theme-config-fg` / `--theme-config-bg` |
| `_Temporal/` | Rose | `--theme-temporal-fg` / `--theme-temporal-bg` |
| `_Plugins/` | Orchid | `--theme-plugins-fg` / `--theme-plugins-bg` |
| `_Workspaces/` | Teal | `--theme-workspaces-fg` / `--theme-workspaces-bg` |

## _Archive Subfolders

`_Archive/` subfolders within artefact folders use the same slate styling as `_Assets/`. This visually signals "infrastructure, not active content". Wildcard CSS selectors apply to any artefact type's `_Archive/` subfolder automatically — no per-folder CSS needed.

| Folder | Colour | Variable |
|--------|--------|----------|
| `{Type}/_Archive/` | Slate | `--theme-assets-fg` / `--theme-assets-bg` |

## Living Artefact Colours

All living artefact folders share a rose gold background tint (`--theme-artefact-bg`). Each folder gets a unique foreground colour from the palette. Never reuse a system folder colour (purple, steel, orchid, slate) for a living artefact.

| Folder | Colour | Variable |
|--------|--------|----------|
| `Daily Notes/` | `#E0988F` | `--color-daily-notes` |
| `Designs/` | `#E0B88F` | `--color-designs` |
| `Documentation/` | `#E0D78F` | `--color-documentation` |
| `Ideas/` | `#CAE08F` | `--color-ideas` |
| `Notes/` | `#AAE08F` | `--color-notes` |
| `People/` | `#8FE093` | `--color-people` |
| `Projects/` | `#8FE0B3` | `--color-projects` |
| `Workspaces/` | `#8F9CE0` | `--color-workspaces` |
| `Writing/` | `#A18FE0` | `--color-writing` |

## Temporal Child Colours

Temporal children share a rose background tint. Each gets a unique foreground derived by blending a base palette colour 35% towards rose.

| Folder | Colour | Variable |
|--------|--------|----------|
| `_Temporal/Captures/` | `#E69AA2` | `--color-temporal-captures` |
| `_Temporal/Cookies/` | `#E6A9A2` | `--color-temporal-cookies` |
| `_Temporal/Decision Logs/` | `#E6B7A2` | `--color-temporal-decision-logs` |
| `_Temporal/Friction Logs/` | `#E6C5A2` | `--color-temporal-friction-logs` |
| `_Temporal/Logs/` | `#E0CCA2` | `--color-temporal-logs` |
| `_Temporal/Observations/` | `#D2CCA2` | `--color-temporal-observations` |
| `_Temporal/Plans/` | `#C3CCA2` | `--color-temporal-plans` |
| `_Temporal/Reports/` | `#B5CCA2` | `--color-temporal-reports` |
| `_Temporal/Research/` | `#B2CCAD` | `--color-temporal-research` |
| `_Temporal/Shaping Transcripts/` | `#B2CCBB` | `--color-temporal-shaping-transcripts` |
| `_Temporal/Snippets/` | `#B2BFD6` | `--color-temporal-snippets` |
| `_Temporal/Thoughts/` | `#B298D6` | `--color-temporal-thoughts` |
| `_Temporal/Transcripts/` | `#E698C7` | `--color-temporal-transcripts` |
