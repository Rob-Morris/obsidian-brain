# Brain Core

Brain is a self-extending system for organising Obsidian vaults, for agents and humans working together.

`.brain-core/` is read-only. Never edit files here directly — changes will be overwritten on version upgrade.

BEFORE modifying the vault always read: [[.brain-core/taxonomy/readme]]

## Key Idea

All content in the vault is an **artefact**:
1. **Living** (in vault root) - evolve over time, source of truth
2. **Temporal** (in _Temporal/) - bound to a moment, historic record

System folders start with `_` or `.` — these are infrastructure, not artefacts. Living artefact folders may contain an `_Archive/` subfolder for documents that have transferred authority elsewhere. See [[.brain-core/standards/archiving]] for archiving procedures. This distinction drives folder organisation, naming, and styling.

The system is self-extending. When content has no appropriate home, add a new artefact type following documented procedures rather than forcing it into an existing folder.

## User Preferences

If `_Config/User/preferences-always.md` exists, read and follow it every session. It contains the vault owner's standing instructions — workflow preferences, quality standards, and agent behaviour rules.

If `_Config/User/gotchas.md` exists, read it before starting work. It contains learned lessons and known pitfalls from previous sessions.

## Principles

ALWAYS follow these principles:

### Every file belongs in a folder
No content files in the vault root. Every output — human or agent — goes into a folder appropriate for its content type. If no folder fits, extend the vault first.

### Self-extending vault
When content has no home, add a new artefact type before creating the file.

### Always link related things
When artefacts relate — by origin, topic, or reference — connect them with wikilinks in the body.

### Save each step before building on it
Multi-stage work (research → analysis, capture → synthesis) produces an artefact at each stage. Don't skip ahead.

### Keep instruction files lean
Files read every session (router, index) stay minimal — routing tables, not encyclopedias. Detailed reference lives in the core docs and config files, linked from the router.

### Start simple, grow organically
Artefacts start as flat files in their type folder. Structure (subfolders, index files, linked compositions) is added as needed when complexity grows, not planned upfront. Lean into organic growth; add structure to deal elegantly with increasing complexity.

## Tooling

Prefer `brain_read`/`brain_action`/`brain_search` MCP tools if available.
Without MCP: use `.brain-core/scripts/` (compile, search, read, rename).
Without either: navigate via wikilinks from the router and this document.

## How It Works

Each vault has a **router** — a single file an agent reads every session. The router contains conditional trigger gotos and vault-specific rules, pointing to taxonomy and skill files. Agents read the core docs only when they need to understand or extend the system.

## Core Documentation

- How to add artefact types and extend principles — [[.brain-core/standards/extending/README]]
- Ready-to-use artefact type definitions — [[.brain-core/artefact-library/README]]
- How to use workflow trigger system — [[.brain-core/triggers]]
- How to use folder colour system: design, palette, CSS templates — [[.brain-core/colours]]
- How to use and extend plugin system for external tools — [[.brain-core/plugins]]

## Standards

- Artefact file naming rules — [[.brain-core/standards/naming-conventions]]
- Artefact provenance and lineage — [[.brain-core/standards/provenance]]
- Archiving living artefacts — [[.brain-core/standards/archiving]]
- Hub pattern for grouping artefacts — [[.brain-core/standards/hub-pattern]]
- Subfolders within living artefact folders — [[.brain-core/standards/subfolders]]
- User preferences — [[.brain-core/standards/user-preferences]]
