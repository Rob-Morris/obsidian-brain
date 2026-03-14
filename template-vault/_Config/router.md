---
modified: 2026-03-14T23:13:22+11:00
---
# Brain

Read [[.brain-core/v1.0/index|Brain Core]] for how this vault system works.

## Artefacts

### Living

Evolve over time. The current version is the source of truth.

| Folder | Purpose | Naming | Reference |
|--------|---------|--------|-----------|
| `Wiki/` | Interconnected knowledge base | `{slug}.md` | [[_Config/Taxonomy/Living/wiki|Wiki]] |

### Temporal

Bound to a day. Written once, rarely edited. Grouped into `yyyy-mm/` month folders.

| Folder | Purpose | Naming | Reference |
|--------|---------|--------|-----------|
| `_Temporal/Logs/` | Append-only daily activity logs | `log--yyyy-mm-dd.md` | [[_Config/Taxonomy/Temporal/logs|Logs]] |
| `_Temporal/Transcripts/` | Conversation transcripts | `yyyymmdd-{slug}.md` | [[_Config/Taxonomy/Temporal/transcripts|Transcripts]] |

## Workflow Triggers

**Before:**
- Before taking action, ask clarifying questions.
- Before taking action, show a brief plan.
- Before creating any file, confirm it has a home in the vault. If no folder fits, extend the vault first — never drop files in the vault root.
- Before deleting files, ask for explicit user approval.
- Before implementing large refactors, backup the vault.

**After:**
- After completing meaningful work, append a timestamped entry to the day's log in `_Temporal/Logs/yyyy-mm/log--yyyy-mm-dd.md`. See [[_Config/Taxonomy/Temporal/logs|Logs]] for format.
- After a conversation that's worth preserving, capture it as a transcript under `_Temporal/Transcripts/`. See [[_Config/Taxonomy/Temporal/transcripts|Transcripts]] for format.

**Ongoing:**
- During long sessions, re-read this file before and after each block of work.

## Configuration

[[_Config/Styles/writing|Writing]] · [[_Config/principles|Principles]] · [[_Config/Styles/obsidian|Obsidian]]
