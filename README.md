# Obsidian Brain

A self-extending system for organising Obsidian vaults, for agents and humans working together.

## Quick Start

1. Copy `template-vault/` to your preferred location
2. Copy `src/brain-core/` into the vault as `.brain-core/v1.0/` (the template vault uses a symlink for development convenience, but real vaults should contain a copy so they're self-contained and portable)
3. Open the folder as an Obsidian vault
4. Enable the CSS snippet in **Settings > Appearance > CSS Snippets** (`folder-colours`)
5. Start working — agents read `CLAUDE.md` → `_Config/router.md` and follow the workflow automatically

To upgrade brain-core later, replace `.brain-core/v1.0/` with the new version from `src/brain-core/`.

## Repository Structure

```
obsidian-brain/
├── src/
│   └── brain-core/              # core methodology (source of truth)
│       ├── index.md             # entry point — links to all core docs
│       ├── artefacts.md         # living vs temporal artefact model
│       ├── extensions.md        # how to add types + example library
│       ├── triggers.md          # workflow trigger system
│       ├── colours.md           # folder colour system design
│       ├── plugins.md           # plugin system
│       └── naming.md            # naming conventions, frontmatter, wikilinks
├── docs/
│   ├── changelog.md             # version history
│   ├── plugins.md               # how to install and write plugins
│   └── specification.md         # design rationale and structural decisions
├── template-vault/              # the Obsidian vault — copy or open directly
│   ├── Agents.md                # agent entry point → router
│   ├── CLAUDE.md                # symlink → Agents.md (Claude Code compatibility)
│   ├── .brain-core/v1.0/        # copy of src/brain-core (version-pinned)
│   ├── .obsidian/               # Obsidian settings, plugins, CSS snippets
│   ├── _Config/                 # router, taxonomy, style, colours, templates, skills
│   ├── _Plugins/                # tool-managed data (installed separately)
│   ├── _Temporal/               # dated working files (logs, transcripts)
│   └── Wiki/                    # interconnected knowledge base
└── README.md
```

## How It Works

All content in the vault is an **artefact** — either **living** (evolves over time, current version is truth) or **temporal** (bound to a moment, written once). System folders (`_Config/`, `_Plugins/`, `.obsidian/`) are infrastructure, not artefacts. Living artefacts sit at the vault root; temporal artefacts sit under `_Temporal/`.

The **router** (`_Config/router.md`) is the single file agents read every session. It lists which artefact types exist, workflow triggers to follow, and links to configuration. **Taxonomy** files (`_Config/Taxonomy/`) describe each artefact type in detail — agents read only the types they need.

## Core / Config Split

- **`.brain-core/`** — versioned methodology. How the Brain system works. Copied into each vault during setup and upgrades (not symlinked), so vaults are self-contained.
- **`_Config/`** — instance configuration. Router, taxonomy, style, colours. Specific to this vault.

## Plugins

The `_Plugins/` folder holds data managed by external tools. Each tool installs its own subfolder, skill doc, and MCP config. See the [plugin guide](docs/plugins.md) for how to install or write plugins.

Available plugins:

- [Undertask](https://github.com/Rob-Morris/undertask/tree/main/plugins/obsidian-brain) — task management via MCP tools

## Customisation

- **`Agents.md`**: add your name and personal context for agents
- **`_Config/router.md`**: add/remove artefact types and workflow triggers
- **`_Config/Taxonomy/`**: add taxonomy files for new artefact types
- **`_Config/Styles/writing.md`**: adjust language preferences (defaults to Australian English)
- **`_Config/router.md` § Principles**: vault-level constraints
- **`_Config/Styles/obsidian.md`**: folder colours and file explorer styling

## Documentation

- [Specification](docs/specification.md) — design rationale and structural decisions
- [Plugins](docs/plugins.md) — how to install and write plugins
- [Changelog](docs/changelog.md) — version history
- Core docs at `.brain-core/v1.0/` — the authoritative reference for the Brain system
