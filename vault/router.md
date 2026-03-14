# Brain

Read [[.brain-core/v1.0/index|Brain Core]] for how this vault system works.

## Artefact Types

### Living

Living artefacts evolve over time. The current version is the source of truth.

| Folder | Purpose | Naming |
|--------|---------|--------|
| `Wiki/` | Interconnected knowledge base | `{slug}.md` |

### Temporal

Temporal artefacts are bound to a moment. Written once, rarely edited. Grouped into `yyyy-mm/` month folders.

| Folder | Purpose | Naming |
|--------|---------|--------|
| `_Temporal/Logs/` | Append-only daily activity logs | `log--yyyy-mm-dd.md` |
| `_Temporal/Transcripts/` | Conversation transcripts | `yyyymmdd-{type}-transcript--{slug}.md` |

## Workflow Triggers

**Before:**
- Before taking action, ask clarifying questions.
- Before taking action, show a brief plan.
- Before creating any file, confirm it has a home in the vault. If no folder fits, extend the vault first — never drop files in the vault root.
- Before taking complex action, write the detailed plan to `_Temporal/Plans/yyyy-mm/yyyymmdd-{slug}.md` (add Plans as a temporal type first — see [[.brain-core/v1.0/extensions|Extensions]]).
- Before deleting files, ask for explicit user approval.
- Before implementing large refactors, backup the vault.

**After:**
- After completing meaningful work, append a timestamped entry to the day's log in `_Temporal/Logs/yyyy-mm/log--yyyy-mm-dd.md`.
- After refining an artefact through Q&A, capture the raw Q&A in a transcript under `_Temporal/Transcripts/`.

**Ongoing:**
- During long sessions, re-read these triggers before and after each block of work.

## Configuration

- [[_Config/style|Style]] — writing style preferences
- [[_Config/principles|Principles]] — vault constraints
- [[_Config/colours|Colours]] — folder colour assignments
