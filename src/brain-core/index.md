# Brain Core

Brain is a self-extending system for organising Obsidian vaults, for agents and humans working together.

## Key Idea

All content in the vault is an **artefact** — either living (evolves over time, current version is the source of truth) or temporal (bound to a moment, written once). System folders (`_Config/`, `_Plugins/`, `.obsidian/`) are infrastructure, not artefacts. This distinction drives folder organisation, naming, and styling.

The system is self-extending. When content has no home, you add a new artefact type following documented procedures rather than forcing it into an existing folder.

## How It Works

Each vault has a **router** — a single file an agent reads every session. The router lists which artefact types exist, what triggers to follow, and where to find configuration. Agents read the core docs only when they need to understand or extend the system.

## Core Documentation

- Governing constraints — [[.brain-core/v1.0/principles]]
- Living vs temporal model, organisation rules, frontmatter — [[.brain-core/v1.0/artefacts]]
- How to add artefact types and extend principles — [[.brain-core/v1.0/extensions]]
- Ready-to-use artefact type definitions — [[.brain-core/v1.0/library]]
- Workflow trigger system — [[.brain-core/v1.0/triggers]]
- Folder colour system design, palette, CSS templates — [[.brain-core/v1.0/colours]]
- Plugin system for external tools — [[.brain-core/v1.0/plugins]]
- Naming conventions, frontmatter, wikilinks — [[.brain-core/v1.0/naming]]
