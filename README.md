# Obsidian Brain

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Version](https://img.shields.io/badge/version-0.15.11-blue)
![Platform](https://img.shields.io/badge/platform-Obsidian-7C3AED)

A self-evolving knowledge base for agents and humans working together on what matters. Local-first. Built for Obsidian.

## Why Obsidian Brain?

- **It gets better, not worse.** Most note systems start organised and slowly decay. Brain's structure is self-reinforcing — agents maintain conventions as they go, so the vault stays useful over time.
- **Agents that understand your knowledge.** Your agent reads the vault's router and taxonomy, finds existing work before creating new work, and files things in the right place without being told. It surfaces the right context when you need it and connects related ideas across your vault.
- **Plain Markdown, no lock-in.** Everything is stored as Markdown files in an Obsidian vault. Browse on desktop or mobile, edit in any text editor, back up with git. No database, no proprietary format.
- **Less organising, more thinking.** Capture ideas without worrying about where they go. Come back after a break and find things where you expect them.

## What Does It Look Like?

You talk to your agent. It reads your vault's router, finds relevant artefacts, and updates your knowledge base — creating new files, connecting ideas, and following your vault's conventions automatically. You can also browse and edit directly in Obsidian at any time. The structure holds either way.

See the [User Guide](docs/user-guide.md) for a full walkthrough with examples.

## How It Works

The intended way to interact with your brain is by talking with your agent. The brain is designed to be easy for your agent to understand, find what matters, and expand.

Each brain is an Obsidian vault, with content stored in Markdown files. You can browse and edit your files with Obsidian on desktop or mobile, or with a text editor. No database, no lock-in.

All brain content in the vault is an **artefact** — either **living** (evolves over time, current version is truth) or **temporal** (bound to a moment, written once). Folders starting with `_` or `.` are infrastructure, not artefacts. Living artefacts sit at the vault root; temporal artefacts sit under `_Temporal/`. Other files go in **_Assets**, or if you want to work on something with many files, you can set up a **_Workspace**.

The **router** (`_Config/router.md`) is the single file agents read every session. It lists which artefact types exist, workflow triggers to follow, and links to configuration. **Taxonomy** files (`_Config/Taxonomy/`) describe each artefact type in detail — agents read only the types they need.

## Quick Start

### Prerequisites

- [Obsidian](https://obsidian.md)
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (or any MCP-capable agent)
- Python 3.12+

### Setup

1. Clone this repo (or download the template vault)
2. Copy `template-vault/` to your preferred location
3. Copy `src/brain-core/` into the vault as `.brain-core/`
4. Open the folder as an Obsidian vault
5. Enable the CSS snippet in **Settings > Appearance > CSS Snippets** (`folder-colours`)
6. Register the Brain MCP server: `python3 .brain-core/scripts/init.py` (or `--user` for all projects)
7. Start a conversation — try "what's in my vault?" or "I have an idea about..."

## Customisation

**Identity & preferences**

- **`Agents.md`** — your name and context so agents know who they're working with
- **`_Config/User/preferences-always.md`** — standing instructions for agents
- **`_Config/User/gotchas.md`** — learned pitfalls from previous sessions

**Vault structure**

- **`_Config/router.md`** — workflow triggers and vault-level constraints
- **`_Config/Taxonomy/`** — type definitions for artefact types
- **`.brain-core/artefact-library/`** — ready-made types to browse and install

**Style**

- **`_Config/Styles/writing.md`** — language preferences (defaults to Australian English)
- **`_Config/Styles/obsidian.md`** — folder colours and file explorer styling

## Plugins

The `_Plugins/` folder holds data managed by external tools. Each tool installs its own subfolder, skill doc, and MCP config. See the [plugin guide](docs/plugins.md) to install or write your own.

Available plugins:

- [Undertask](https://github.com/Rob-Morris/undertask/tree/main/plugins/obsidian-brain) — task management via MCP tools

## Upgrading

To upgrade brain-core:

- **MCP**: ask your agent to run `brain_action(action="upgrade", ...)`
- **CLI**: `python3 .brain-core/scripts/upgrade.py --source /path/to/src/brain-core`
- **Manual**: replace `.brain-core/` with the new version from `src/brain-core/`

## Documentation

- [Quick-Start Guide](src/brain-core/guide.md) — day-to-day essentials (ships as `.brain-core/guide.md` in vaults)
- [User Guide](docs/user-guide.md) — walkthrough with examples: workflows, idea graduation, building knowledge
- [User Reference](docs/user-reference.md) — every artefact type, configuration point, and convention in detail
- [Tooling](docs/tooling.md) — technical design for scripts, MCP server, and development setup
- [Specification](docs/specification.md) — design rationale and structural decisions
- [Plugins](docs/plugins.md) — how to install and write plugins
- [Changelog](docs/changelog.md) — version history
- [Contributing](docs/contributing.md) — guide for humans and agents working on brain-core

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
