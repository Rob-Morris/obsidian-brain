# Obsidian Brain

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Version](https://img.shields.io/badge/version-0.16.0-blue)
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
7. Start a conversation

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
