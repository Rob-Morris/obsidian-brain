# Obsidian Brain

A self-extending system for organising Obsidian vaults, for agents and humans working together.

## Quick Start

1. Copy `template-vault/` to your preferred location
2. Copy `src/brain-core/` into the vault as `.brain-core/` (the template vault uses a symlink for development convenience, but real vaults should contain a copy so they're self-contained and portable)
3. Open the folder as an Obsidian vault
4. Enable the CSS snippet in **Settings > Appearance > CSS Snippets** (`folder-colours`)
5. Start working — agents read `CLAUDE.md` → `_Config/router.md` and follow the workflow automatically
6. Set up Claude Code: `python3 .brain-core/scripts/init.py` (or `--user` for all projects)

To upgrade brain-core later, replace the contents of `.brain-core/` with the new version from `src/brain-core/`.

## Repository Structure

```
obsidian-brain/
├── src/
│   └── brain-core/              # core methodology (source of truth)
│       ├── VERSION              # brain-core version
│       ├── index.md             # entry point — links to all core docs
│       ├── guide.md             # quick-start guide (ships into vaults)
│       ├── artefact-library/    # ready-to-install type definitions
│       ├── taxonomy/readme.md   # artefact classification guide
│       ├── extensions.md        # how to add types, colours, triggers
│       ├── triggers.md          # workflow trigger system
│       ├── colours.md           # folder colour system design
│       ├── plugins.md           # plugin system
│       ├── scripts/             # tooling (compile_router, check, build_index, search)
│       └── mcp/                 # MCP server (brain_read, brain_search, brain_action)
├── tests/                       # test suite (make test)
├── docs/
│   ├── user-guide.md            # walkthrough with examples
│   ├── user-reference.md        # full reference for all types and conventions
│   ├── tooling.md               # technical design and development setup
│   ├── changelog.md             # version history
│   ├── plugins.md               # how to install and write plugins
│   └── specification.md         # design rationale and structural decisions
├── template-vault/              # starter vault — copy to create a new Brain
└── README.md
```

## How It Works

All content in the vault is an **artefact** — either **living** (evolves over time, current version is truth) or **temporal** (bound to a moment, written once). Folders starting with `_` or `.` are infrastructure, not artefacts. Living artefacts sit at the vault root; temporal artefacts sit under `_Temporal/`.

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
- **`_Config/router.md`**: add/remove workflow triggers and vault-level constraints
- **`_Config/Taxonomy/`**: add taxonomy files for new artefact types
- **`.brain-core/artefact-library/`**: browse and install ready-made type definitions
- **`_Config/User/preferences-always.md`**: your standing instructions for agents
- **`_Config/User/gotchas.md`**: learned pitfalls from previous sessions
- **`_Config/Styles/writing.md`**: adjust language preferences (defaults to Australian English)
- **`_Config/Styles/obsidian.md`**: folder colours and file explorer styling

## Documentation

- [Quick-Start Guide](src/brain-core/guide.md) — day-to-day essentials (ships as `.brain-core/guide.md` in vaults)
- [User Guide](docs/user-guide.md) — walkthrough with examples: workflows, idea graduation, building knowledge
- [User Reference](docs/user-reference.md) — every artefact type, configuration point, and convention in detail
- [Tooling](docs/tooling.md) — technical design for scripts, MCP server, and development setup
- [Specification](docs/specification.md) — design rationale and structural decisions
- [Plugins](docs/plugins.md) — how to install and write plugins
- [Changelog](docs/changelog.md) — version history
