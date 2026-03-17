# Brain Core

Brain is a self-extending system for organising Obsidian vaults, for agents and humans working together.

BEFORE modifying the vault always read: [[.brain-core/taxonomy/readme]]

## Key Idea

All content in the vault is an **artefact**:
1. **Living** (in vault root) - evolve over time, source of truth
2. **Temporal** (in _Temporal/) - bound to a moment, historic record

System folders start with `_` or `.`, these are infrastructure, not artefacts. Living artefact folders may contain an `_Archive/` subfolder for documents that have transferred authority elsewhere — see [[.brain-core/extensions]]. These distinction drives folder organisation, naming, and styling.

The system is self-extending. When content has no appropriate home, add a new artefact type following documented procedures rather than forcing it into an existing folder.

## Principles

ALWAYS follow these principles:

### Every file belongs in a folder
No content files in the vault root. Every output — human or agent — goes into a folder appropriate for its content type. If no folder fits, extend the vault first.

### Self-extending vault
When content has no home, add a new artefact type before creating the file.

### Keep instruction files lean
Files read every session (router, index) stay minimal — routing tables, not encyclopedias. Detailed reference lives in the core docs and config files, linked from the router.

Always:
- Every artefact belongs in a typed folder. Prefer existing types over new ones.
- When content has no home, add a new artefact type before creating the file.

## Tooling

Prefer `brain_read`/`brain_action`/`brain_search` MCP tools if available.
Without MCP: use Obsidian CLI (localhost:27124) for search/rename + `.brain-core/scripts/` for compile/index.
Without either: navigate via wikilinks from the router and this document.

## How It Works

Each vault has a **router** — a single file an agent reads every session. The router contains conditional trigger gotos and vault-specific rules, pointing to taxonomy and skill files. Agents read the core docs only when they need to understand or extend the system.

## Core Documentation

- How to add artefact types and extend principles — [[.brain-core/extensions]]
- Ready-to-use artefact type definitions — [[.brain-core/library]]
- How to use workflow trigger system — [[.brain-core/triggers]]
- How to use folder colour system: design, palette, CSS templates — [[.brain-core/colours]]
- How to use and extend plugin system for external tools — [[.brain-core/plugins]]
