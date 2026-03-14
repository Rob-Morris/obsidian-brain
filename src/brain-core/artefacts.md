# Artefacts

All content in the vault is an artefact — either living or temporal. System folders (`_Config/`, `_Plugins/`, `.obsidian/`) are infrastructure, not artefacts.

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
_Temporal/Transcripts/2026-03/20260307-vault-restructure.md
```

## Frontmatter

Every artefact file has YAML frontmatter with at least `type` and `tags`:

```yaml
---
type: living/wiki
tags:
  - tag-name
---
```

Config files (`_Config/`) do not use frontmatter. Plugin files follow their own conventions.

## What a Vault Ships

This file describes the artefact model. The **router** lists which specific artefact types exist in a given vault — it's the per-instance configuration. See [[.brain-core/v1.0/extensions]] for adding new types.
