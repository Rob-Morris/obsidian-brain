# Obsidian Brain

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Version](https://img.shields.io/badge/version-0.18.6-blue)
![Platform](https://img.shields.io/badge/platform-Obsidian-7C3AED)
![Python](https://img.shields.io/badge/python-≥3.10-3776AB?logo=python&logoColor=white)
![MCP](https://img.shields.io/badge/MCP-server-green?logo=data:image/svg%2Bxml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0id2hpdGUiPjxwYXRoIGQ9Ik0xMiAyQzYuNDggMiAyIDYuNDggMiAxMnM0LjQ4IDEwIDEwIDEwIDEwLTQuNDggMTAtMTBTMTcuNTIgMiAxMiAyem0wIDE4Yy00LjQyIDAtOC0zLjU4LTgtOHMzLjU4LTggOC04IDggMy41OCA4IDgtMy41OCA4LTggOHptLTEtMTNoMnY2aC0yem0wIDhoMnYyaC0yeiIvPjwvc3ZnPg==)

A self-evolving knowledge base for agents and humans working together on what matters.

Agents are getting increasingly capable, but they're forgetful; and so are you. The brain gives you and your agent a long-term memory. Forget something? Ask your agent; it will find it. Working on a task? Your agent remembers what you mean, what you care about, what you've done. As you work, the brain builds a rich, linked graph of everything that matters: ideas connect to projects, decisions connect to context, notes connect to sources. Everything you do together makes your brain, your agent, and you, smarter. Plain Markdown in a local Obsidian vault; human-readable, editable in any tool, no lock-in.

## Why Obsidian Brain?

| Feature | Benefit |
|:--|:--|
| **Never forget** | Every conversation, decision, and idea goes into a linked graph. Your agent finds it when you need it; it remembers so you don't have to. |
| **Context compounds** | The richer the vault, the better the answers. Ideas link to projects, decisions link to reasons, notes link to sources. Every session builds on the last. |
| **Self-evolving** | Like a real brain, it grows around you and how you think. Your agent builds the vault out as you work; no one size fits all, and yours won't look like anyone else's. |
| **Designed for retrieval** | The faster your agent finds the right context, the smarter it can be. A single router file tells your agent where everything lives. Taxonomy files describe each type. No searching blind. |
| **Works with any agent** | Any agent that can read files can understand the brain out of the box; the conventions are clear and the structure is self-documenting. We ship tooling that makes it even better. |
| **You can read it too** | This isn't a hidden vector database. Everything is human-readable Markdown in an Obsidian vault. Browse it, search it, edit it. You see what your agent sees. |
| **Free your data** | Your data is valuable and it's yours; put it to work. It stays on your machine by default. Keep it private, sync it to the cloud, access it from anywhere. |
| **Durable** | No database, no proprietary format, no lock-in. Standard files that any tool can read. Your knowledge survives tool changes, agent changes, platform changes. |

## How It Works

Each brain is an Obsidian vault. You talk to your agent; the agent reads the vault's structure, finds what matters, and extends it. You can browse and edit in Obsidian at any time. The structure holds either way.

All vault content is an **artefact**, either **living** (evolves over time; current version is truth) or **temporal** (bound to a moment; written once). Folders starting with `_` or `.` are infrastructure, not artefacts. Living artefacts sit at the vault root; temporal artefacts sit under `_Temporal/`.

The **router** (`_Config/router.md`) is the single file agents read every session. It lists artefact types, workflow triggers, and links to configuration. **Taxonomy** files (`_Config/Taxonomy/`) describe each type in detail; agents read only the types they need.

The [User Guide](docs/user-guide.md) walks through all of this with examples.

## Quick Start

1. **Install [prerequisites](#prerequisites)**
2. **Create your vault.** Run the [install command](#install) below.
3. **Open as a vault in Obsidian.** Select the folder you created in step 2, then enable the `brain-folder-colours` CSS snippet in Obsidian's Settings > Appearance > CSS Snippets.
4. **Connect your agent.** Open your agent in the vault folder, or register the MCP server for other directories (see [Connecting from other projects](#connecting-from-other-projects)).

That's it. Start talking. The agent reads the vault structure and knows what to do. See [A Day in the Life](docs/user-guide.md#a-day-in-the-life) for what working with the brain looks like in practice.

### Prerequisites

- [Obsidian](https://obsidian.md)
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (or any MCP-capable agent)
- Python 3.10+ (recommended; the installer works without it but skips MCP server setup)
- git

### Install

Run this from any terminal. It downloads the repo, creates a vault, installs dependencies, and registers the MCP server:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/robmorris/obsidian-brain/main/install.sh)
```

The script will ask where to create the vault (defaults to the current directory). You can also pass the path directly:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/robmorris/obsidian-brain/main/install.sh) ~/brain
```

If you've already cloned the repo, run the install script directly. It will use local files instead of downloading:

```bash
bash install.sh ~/brain
```

#### Upgrade

Re-run the install script on an existing vault to upgrade brain-core:

```bash
bash install.sh ~/brain
```

The script detects the existing installation, shows the version change, and asks to confirm.

#### Existing vault

Point the script at a non-empty directory (with or without Obsidian) and it installs brain-core without touching your files:

```bash
bash install.sh ~/my-existing-vault
```

#### Uninstall

```bash
bash install.sh --uninstall ~/brain
```

Removes brain system files (`.brain-core/`, `.brain/`, `.venv/`, `.mcp.json`, `CLAUDE.md`). Your notes are not affected. Optionally offers to delete the entire vault with a multi-stage confirmation.

#### Non-interactive mode

```bash
bash install.sh --force ~/brain
bash install.sh --uninstall --force ~/brain
```

Skips all prompts. Useful for scripted or agent-driven installs. On uninstall, `--force` only skips the system-files prompt; vault deletion always requires interactive confirmation.

> **Full reference:** [docs/tooling.md — install.sh](docs/tooling.md#installsh) covers all flags, safety guards, and edge-case behaviour.

<details>
<summary>Fully manual setup</summary>

If you prefer to do it yourself:

1. Clone this repo: `git clone https://github.com/robmorris/obsidian-brain.git`
2. Copy `template-vault/` to your preferred location: `cp -RL template-vault ~/brain` (the `-L` flag resolves the `.brain-core` symlink)
3. Create a venv and install dependencies: `cd ~/brain && python3 -m venv .venv && .venv/bin/pip install "mcp>=1.0.0"`
4. Register the MCP server: `python3 .brain-core/scripts/init.py` (or `--user` for all projects)
5. Open the folder as an Obsidian vault
6. Enable the CSS snippet in **Settings > Appearance > CSS Snippets** (`brain-folder-colours`)

</details>

### Connecting from Other Projects

The install script registers the MCP server for the vault directory. To connect your agent from other directories:

```bash
# Make the brain available to all projects
python3 .brain-core/scripts/init.py --user

# Or link a specific project
python3 .brain-core/scripts/init.py --project /path/to/project
```

### Hello, Is It Me You're Looking For?

| You Can Just | Do Things |
|:--|:--|
| "I just had an idea about..." | "Build a board presentation from this quarter's work on Cairn" |
| "What did we decide about pricing for Longboard?" | "Optimise the pricing model for Tidepool based on user purchase behaviour to maximise gross revenue" |
| "What's changed on Helios since I went on leave?" | "Research real-time sync approaches that would work with Mosaic's event-driven architecture" |
| "Why did we drop the subscription model for Pace?" | "I have an idea for a preference engine. Interview me to shape it." |
| "We're getting Redis timeouts on Atlas. What could be causing them?" | "Turn my notes from the Sequoia offsite into something the product team can use" |
| "What went wrong with the Kite launch?" | "Write a brief for the new designer on Lantern. Bring them up to speed on everything we've decided." |
| "What's the status of everything I'm working on?" | "Draft a competitive analysis for Ridgeline. How are others solving the same distribution problem?" |
| "Who was that person I met at the conference in March?" | "Start planning the kitchen renovation. Start with the quotes and inspiration I've been collecting." |
| "Recommend three books I might like to read next" | "Put together talking points for my 1:1 with Marcus about the Rivian partnership" |
| "What are 3 high-impact things I could do differently next quarter?" | "Brainstorm ideas for something special for my anniversary with Sam. She loved that place in Byron last time." |

## Documentation

- [Quick-Start Guide](src/brain-core/guide.md) — day-to-day essentials (ships as `.brain-core/guide.md` in vaults)
- [User Guide](docs/user-guide.md) — workflows, idea graduation, building knowledge
- [User Reference](docs/user-reference.md) — every artefact type, configuration point, and convention
- [Tooling](docs/tooling.md) — scripts, MCP server, development setup
- [Specification](docs/specification.md) — design rationale and structural decisions
- [Plugins](docs/plugins.md) — install and write plugins
- [Changelog](docs/changelog.md) — version history
- [Contributing](docs/contributing.md) — contributor guide (general + [agent-specific](docs/contributing-agents.md))
