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

## _Archive (root + subfolders)

Archive content uses slate styling — visually signalling "infrastructure, not active content". Two cases:

- **Root `_Archive/`** gets the full slate treatment (slate fg + slate 12% bg + double slate border) plus the `⧈` icon badge, matching `_Assets/`.
- **Artefact `_Archive/` subfolders** (e.g. `Designs/_Archive/`) use slate fg on the parent folder's background and border colour.

Wildcard CSS selectors apply to any artefact type's `_Archive/` subfolder automatically — no per-folder CSS needed.

| Folder | Colour | Variable |
|--------|--------|----------|
| `_Archive/` (root) | Slate | `--theme-assets-fg` / `--theme-assets-bg` |
| `{Type}/_Archive/` | Slate fg on parent bg | `--theme-assets-fg` |

## Living Artefact Colours

All living artefact folders share a rose gold background tint (`--theme-artefact-bg`). Each folder gets a unique foreground colour from the palette. Never reuse a system folder colour (purple, steel, orchid, slate) for a living artefact.

| Folder | Colour | Variable |
|--------|--------|----------|
| `Daily Notes/` | `#E0958F` | `--color-daily-notes` |
| `Designs/` | `#E0AF8F` | `--color-designs` |
| `Documentation/` | `#E0C98F` | `--color-documentation` |
| `Ideas/` | `#DEE08F` | `--color-ideas` |
| `Notes/` | `#C4E08F` | `--color-notes` |
| `People/` | `#AAE08F` | `--color-people` |
| `Projects/` | `#90E08F` | `--color-projects` |
| `Releases/` | `#8FE0A8` | `--color-releases` |
| `Tasks/` | `#8FD6E0` | `--color-tasks` |
| `Workspaces/` | `#8F94E0` | `--color-workspaces` |
| `Writing/` | `#E08FCB` | `--color-writing` |

## Temporal Child Colours

Temporal children share a rose background tint. Each gets a unique foreground derived by blending a base palette colour 35% towards rose.

| Folder | Colour | Variable |
|--------|--------|----------|
| `_Temporal/Bug Logs/` | `#E698A2` | `--color-temporal-bug-logs` |
| `_Temporal/Captures/` | `#E6A3A2` | `--color-temporal-captures` |
| `_Temporal/Cookies/` | `#E6AEA2` | `--color-temporal-cookies` |
| `_Temporal/Decision Logs/` | `#E6BAA2` | `--color-temporal-decision-logs` |
| `_Temporal/Friction Logs/` | `#E6C5A2` | `--color-temporal-friction-logs` |
| `_Temporal/Ingestions/` | `#E4CCA2` | `--color-temporal-ingestions` |
| `_Temporal/Logs/` | `#D9CCA2` | `--color-temporal-logs` |
| `_Temporal/Mockups/` | `#CECCA2` | `--color-temporal-mockups` |
| `_Temporal/Observations/` | `#C3CCA2` | `--color-temporal-observations` |
| `_Temporal/Plans/` | `#B8CCA2` | `--color-temporal-plans` |
| `_Temporal/Presentations/` | `#B2CCA5` | `--color-temporal-presentations` |
| `_Temporal/Reports/` | `#B2CCB0` | `--color-temporal-reports` |
| `_Temporal/Research/` | `#B2CCBC` | `--color-temporal-research` |
| `_Temporal/Shaping Transcripts/` | `#B2C2D6` | `--color-temporal-shaping-transcripts` |
| `_Temporal/Snippets/` | `#B29DD6` | `--color-temporal-snippets` |
| `_Temporal/Thoughts/` | `#B898D6` | `--color-temporal-thoughts` |
| `_Temporal/Transcripts/` | `#E698C6` | `--color-temporal-transcripts` |
