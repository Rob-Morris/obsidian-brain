# Brain Core

Brain is a self-extending system for organising Obsidian vaults, for agents and humans working together.

## Key Idea

Everything in the vault is an **artefact** — either living (evolves over time, current version is the source of truth) or temporal (bound to a moment, written once). This distinction drives folder organisation, naming, and styling.

The system is self-extending. When content has no home, you add a new artefact type following documented procedures rather than forcing it into an existing folder.

## How It Works

Each vault has a **router** — a single file an agent reads every session. The router lists which artefact types exist, what triggers to follow, and where to find configuration. Agents read the core docs only when they need to understand or extend the system.

## Core Documentation

- [[.brain-core/v1.0/principles|Principles]] — governing constraints for all Brain vaults
- [[.brain-core/v1.0/artefacts|Artefacts]] — living vs temporal model, organisation rules, frontmatter
- [[.brain-core/v1.0/extensions|Extensions]] — how to add artefact types, with an example library
- [[.brain-core/v1.0/triggers|Triggers]] — workflow trigger system
- [[.brain-core/v1.0/colours|Colours]] — folder colour system design, palette, CSS templates
- [[.brain-core/v1.0/plugins|Plugins]] — plugin system for external tools
- [[.brain-core/v1.0/naming|Naming]] — naming conventions, frontmatter, wikilinks
