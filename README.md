# Obsidian Brain

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE) ![Version](https://img.shields.io/badge/version-0.34.2-blue) ![Platform](https://img.shields.io/badge/platform-Obsidian-7C3AED) ![Python](https://img.shields.io/badge/python-≥3.12-3776AB?logo=python&logoColor=white) ![MCP](https://img.shields.io/badge/MCP-server-green)

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

**You need:** git and an MCP-capable agent such as [Claude Code](https://docs.anthropic.com/en/docs/claude-code) or Codex. Python 3.12+ is the supported user-facing runtime for MCP and Python lifecycle commands; `install.sh` can still scaffold the vault without it and print the follow-up steps. [Obsidian](https://obsidian.md) is strongly recommended — the brain is designed for it — but you can use any markdown editor or just talk to your agent directly.

**Create your vault:**

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/rob-morris/obsidian-brain/main/install.sh)
```

This downloads the repo, creates the vault in the current directory, and then attempts project-scope MCP setup for Claude Code and Codex. Pass a path to install elsewhere. If you want the vault scaffold without `.venv` / MCP setup, pass `--skip-mcp` (or add `--non-interactive` for non-interactive agent installs). From a local clone, use `bash install.sh` instead.

**Open in Obsidian (recommended):** Open the vault folder, then enable the `brain-folder-colours` CSS snippet in Settings > Appearance > CSS Snippets.

**Start talking:** Open your agent in the vault folder (for example `cd /path/to/brain && claude` or `cd /path/to/brain && codex`). It reads the vault structure and knows what to do. See [Workflows](docs/user/workflows.md) for what working with the brain looks like in practice.

#### Upgrade

The canonical upgrade path is `upgrade.py` from a clone of this repo:

```bash
python3.12 src/brain-core/scripts/upgrade.py --source src/brain-core --vault /path/to/brain
```

If you want a convenience wrapper that fetches the repo or prompts for confirmation, `install.sh` can delegate to `upgrade.py` for an already-installed vault:

```bash
bash install.sh /path/to/brain
```

The wrapper detects the existing installation, shows the version change, and then runs `upgrade.py`. When `.brain-core/brain_mcp/requirements.txt` changes and the vault already has a local `.venv`, the upgrader syncs that environment directly; project MCP registration is left in place and is not re-run. Same-version re-apply, downgrade, and migration rerun flows remain explicit `upgrade.py --force` operations.

#### Repair

If the local Brain runtime or generated state drifts, use the explicit repair entry point:

```bash
python3.12 .brain-core/scripts/repair.py mcp
python3.12 .brain-core/scripts/repair.py router
python3.12 .brain-core/scripts/repair.py index
python3.12 .brain-core/scripts/repair.py registry
```

For most users, `repair.py mcp` is the main recovery path. Use it when the
vault-local `.venv`, MCP dependencies, or installed current-vault project MCP
registration have drifted. It repairs the clients that are already installed
for the vault; it does not act as a first-time installer. The other scopes
repair generated router/index state or the local workspace registry.

If you do not know what is broken, start with:

```bash
python3 .brain-core/scripts/check.py
```

When `check.py` detects router, MCP, or local workspace-registry drift, it now prints the exact `repair.py` command to run. `repair.py` may be launched from any compatible Python 3.12+ interpreter, but packageful repair converges into the vault-local `.venv`; it does not install packages into your wider Python environment.

#### Existing vault

Point the script at a non-empty directory (with or without Obsidian) and it installs brain-core without touching your files:

```bash
bash install.sh ~/my-existing-vault
```

#### Uninstall

```bash
bash install.sh --uninstall /path/to/brain
```

Removes brain system files (`.brain-core/`, `.brain/`, `.venv/`), removes the Brain bootstrap line from `CLAUDE.md` (deleting the file only if it becomes empty), removes only recorded Brain-managed project MCP entries from `.mcp.json` / `.codex/config.toml`, and removes recorded Brain-managed Claude local state in `.claude/`. Your notes are not affected. User-scope MCP cleanup stays explicit. Optionally offers to delete the entire vault with a multi-stage confirmation.

#### Non-interactive mode

```bash
bash install.sh --non-interactive /path/to/brain
bash install.sh --non-interactive --skip-mcp /path/to/brain
bash install.sh --uninstall --non-interactive /path/to/brain
```

Skips all prompts. Useful for scripted or agent-driven installs. Add `--skip-mcp` to scaffold the vault without creating `.venv` or registering Claude/Codex MCP — useful in network-restricted agent sandboxes. If MCP dependency install or registration fails, the installer now leaves the vault in place and prints manual retry steps instead of aborting the whole install. On uninstall, `--non-interactive` removes system files without prompting and skips the vault-deletion offer entirely. On upgrade, `install.sh` just delegates to `upgrade.py`; it does not own upgrade override semantics or re-run MCP setup. If you need same-version re-apply, downgrade, or migration rerun behaviour, call `upgrade.py --force` directly.

> **Full reference:** [Scripts — install.sh](docs/functional/scripts.md#installsh) covers all flags, safety guards, and edge-case behaviour.

<details>
<summary>Fully manual setup</summary>

If you prefer to do it yourself:

1. Clone this repo: `git clone https://github.com/rob-morris/obsidian-brain.git`
2. Copy `template-vault/` to your preferred location: `cp -R template-vault /path/to/brain`
3. Copy brain-core into the vault: `cp -R src/brain-core /path/to/brain/.brain-core`
4. Create a vault-local venv and install Brain MCP dependencies: `cd /path/to/brain && python3.12 -m venv .venv && .venv/bin/python -m pip install -r .brain-core/brain_mcp/requirements.txt`
5. Register the MCP server: `.venv/bin/python .brain-core/scripts/init.py --client all` (or `--user --client all` for all projects)
   For project scope, the file write is not the whole story: Claude still needs `/mcp` approval for `brain`, and Codex still needs the project trusted with `brain` enabled.
6. Open the folder as an Obsidian vault
7. Enable the CSS snippet in **Settings > Appearance > CSS Snippets** (`brain-folder-colours`)

</details>

### Connecting from Other Projects

When MCP setup is enabled, the install script registers the server for the vault directory at project scope for Claude Code and Codex. To use the brain from other directories, run one of these from inside the vault with the vault-local managed runtime:

```bash
# Make the brain available to all projects for both clients
.venv/bin/python .brain-core/scripts/init.py --user --client all

# Or link a specific project for both clients
.venv/bin/python .brain-core/scripts/init.py --project /path/to/project --client all

# Claude-only local scope (gitignored; Codex has no local scope)
.venv/bin/python .brain-core/scripts/init.py --client claude --local
```

Use `--user` if you want the brain everywhere. Use `--project` to connect a single project without affecting others. Use `--client claude --local` when you want Claude-only local config in `.claude/settings.local.json` without committing it. For project scope, the project-scoped MCP still outranks the user-scoped one once it is active, but registration alone is not enough: in Claude, approve `brain` via `/mcp`; in Codex, trust the project and ensure `brain` is enabled. Until then, either client may keep using the user-scoped `brain`.

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

Most repo documentation lives under [docs/README.md](docs/README.md), which routes to the user, functional, architecture, and contributor docs.

Good starting points:

- [Getting Started](docs/user/getting-started.md) — install Obsidian Brain and create your first vault
- [User Docs](docs/user/README.md) — user-facing guides, workflows, and reference
- [Contributing](docs/CONTRIBUTING.md) — repo contribution guide, including links to contributor-specific docs
- [Changelog](docs/CHANGELOG.md) — release history
