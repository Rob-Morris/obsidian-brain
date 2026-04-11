# Obsidian Brain

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE) ![Version](https://img.shields.io/badge/version-0.24.8-blue) ![Platform](https://img.shields.io/badge/platform-Obsidian-7C3AED) ![Python](https://img.shields.io/badge/python-≥3.10-3776AB?logo=python&logoColor=white) ![MCP](https://img.shields.io/badge/MCP-server-green)

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

The [Getting Started guide](docs/user/getting-started.md) walks through all of this with examples.

## Quick Start

**You need:** git, Python 3.10+, and an MCP-capable agent ([Claude Code](https://docs.anthropic.com/en/docs/claude-code), etc.). [Obsidian](https://obsidian.md) is strongly recommended — the brain is designed for it — but you can use any markdown editor or just talk to your agent directly.

**Create your vault:**

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/robmorris/obsidian-brain/main/install.sh) ~/brain
```

This downloads the repo, creates the vault at `~/brain`, installs dependencies, and registers the MCP server. Run without a path to be prompted for a location. From a local clone, use `bash install.sh ~/brain` instead.

**Open in Obsidian (recommended):** Open the vault folder, then enable the `brain-folder-colours` CSS snippet in Settings > Appearance > CSS Snippets.

**Start talking:** Open your agent in the vault folder (e.g. `cd ~/brain && claude` for Claude Code). It reads the vault structure and knows what to do. See [Workflows](docs/user/workflows.md) for what working with the brain looks like in practice.

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

Skips all prompts. Useful for scripted or agent-driven installs. On uninstall, `--force` removes system files without prompting and skips the vault-deletion offer entirely — vault deletion is only available in interactive mode (without `--force`).

> **Full reference:** [Scripts — install.sh](docs/functional/scripts.md#installsh) covers all flags, safety guards, and edge-case behaviour.

<details>
<summary>Fully manual setup</summary>

If you prefer to do it yourself:

1. Clone this repo: `git clone https://github.com/robmorris/obsidian-brain.git`
2. Copy `template-vault/` to your preferred location: `cp -R template-vault ~/brain`
3. Copy brain-core into the vault: `cp -R src/brain-core ~/brain/.brain-core`
4. Create a venv and install dependencies: `cd ~/brain && python3 -m venv .venv && .venv/bin/pip install "mcp>=1.0.0"`
5. Register the MCP server: `python3 .brain-core/scripts/init.py` (or `--user` for all projects)
6. Open the folder as an Obsidian vault
7. Enable the CSS snippet in **Settings > Appearance > CSS Snippets** (`brain-folder-colours`)

</details>

### Connecting from Other Projects

The install script registers the MCP server for the vault directory. To use the brain from other directories, run one of these from inside the vault:

```bash
# Make the brain available to all projects (adds to ~/.claude/settings.json)
python3 .brain-core/scripts/init.py --user

# Or link a specific project (creates .mcp.json in the target directory)
python3 .brain-core/scripts/init.py --project /path/to/project
```

Use `--user` if you want the brain everywhere. Use `--project` to connect a single project without affecting others.

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

Docs are organised into three layers so you can find what you need quickly. The **user** layer covers how to use the brain — start here. The **functional** layer is the exact specification of each tool, script, and config option. The **architectural** layer explains why things are built the way they are. Functional docs also live alongside the code in `src/brain-core/`, which ships inside your vault, where agents naturally find and maintain them.

**Using the Brain:**

- [Getting Started](docs/user/getting-started.md) — installation, first vault, orientation
- [Workflows](docs/user/workflows.md) — day-to-day usage patterns
- [System Guide](docs/user/system-guide.md) — artefact lifecycle, frontmatter, statuses
- [Template Library](docs/user/template-library-guide.md) — what ships, what each type is for
- [Quick-Start Guide](src/brain-core/guide.md) — in-vault cheat sheet (ships as `.brain-core/guide.md`)

**Reference:**

- [MCP Tools](docs/functional/mcp-tools.md) — tool specifications
- [Scripts](docs/functional/scripts.md) — script reference
- [Config](docs/functional/config.md) — configuration profiles and environment
- [User Reference](docs/user-reference.md) — configuration points, colour system, writing style
- [Plugins](docs/plugins.md) — install and write plugins

**Architecture:**

- [Specification](docs/specification.md) — design rationale and structural decisions
- [Architecture Overview](docs/architecture/overview.md) — components, data flow, boundaries
- [Changelog](docs/changelog.md) — version history
- [Contributing](docs/contributing.md) — contributor guide (general + [agent-specific](docs/contributing-agents.md))
