# Brain Core

Brain is a self-extending system for organising Obsidian vaults, for agents and humans working together.

BEFORE modifying the vault always read: [[.brain-core/v1.0/taxonomy]]

## Key Idea

All content in the vault is an **artefact**:
1. **Living** (in vault root) - evolve over time, source of truth
2. **Temporal** (in _Temporal/) - bound to a moment, historic record

System folders start with `_` or `.`, these are infrastructure, not artefacts. These distinction drives folder organisation, naming, and styling.

The system is self-extending. When content has no appropriate home, add a new artefact type following documented procedures rather than forcing it into an existing folder.

## Principles

ALWAYS follow these principals:

### Every file belongs in a folder
No content files in the vault root. Every output — human or agent — goes into a folder appropriate for its content type. If no folder fits, extend the vault first.

### Self-extending vault
When content has no home, add a new artefact type before creating the file.

### Keep instruction files lean
Files read every session (router, index) stay minimal — routing tables, not encyclopedias. Detailed reference lives in the core docs and config files, linked from the router.

## How It Works

Each vault has a **router** — a single file an agent reads every session. The router lists which artefact types exist, what triggers to follow, and where to find configuration. Agents read the core docs only when they need to understand or extend the system.

## Core Documentation

- How to add artefact types and extend principles — [[.brain-core/v1.0/extensions]]
- Ready-to-use artefact type definitions — [[.brain-core/v1.0/library]]
- How to use workflow trigger system — [[.brain-core/v1.0/triggers]]
- How to use folder colour system: design, palette, CSS templates — [[.brain-core/v1.0/colours]]
- How to use and extend plugin system for external tools — [[.brain-core/v1.0/plugins]]
