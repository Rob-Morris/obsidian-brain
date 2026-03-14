# Artefacts

Everything in the vault is an artefact. Artefacts come in two types.

## Living

Living artefacts evolve over time. The current version is the source of truth. You update them as understanding changes.

Living artefact folders sit at the vault root: `Wiki/`, `Designs/`, `Notes/`, etc.

## Temporal

Temporal artefacts are bound to a moment. Written once, rarely edited. They capture state at a point in time.

Temporal artefact folders sit under `_Temporal/`: `_Temporal/Logs/`, `_Temporal/Transcripts/`, etc.

### Month Folders

All temporal content is grouped into `yyyy-mm/` month folders for chronological organisation. Files sit flat in their month folder — no subfolders within months.

```
_Temporal/Logs/2026-03/log--2026-03-10.md
_Temporal/Transcripts/2026-03/20260307-design-transcript--discord-bot.md
```

## Not Artefacts

System folders are infrastructure, not artefacts:

- `_Config/` — configuration, templates, skills, assets
- `_Plugins/` — data managed by external tools (follows the tool's own conventions)
- `.obsidian/` — Obsidian app settings

## Frontmatter

Every artefact file has YAML frontmatter with at least `tags`. Most also include `created` and `modified` as ISO 8601 timestamps with timezone:

```yaml
---
tags:
  - tag-name
created: '2026-03-10T08:38:47+11:00'
modified: '2026-03-10T08:38:47+11:00'
---
```

Config files (`_Config/`) do not use frontmatter. Plugin files follow their own conventions.

The `created` and `modified` fields can be managed automatically by Obsidian plugins like Front Matter Timestamps. Exclude `_Config/` from such plugins.

## What a Vault Ships

This file describes the artefact model. The **router** lists which specific artefact types exist in a given vault — it's the per-instance configuration. See [[.brain-core/v1.0/extensions|Extensions]] for adding new types.
