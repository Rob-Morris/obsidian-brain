# Plugins

Plugins connect external tools to a Brain vault. Each plugin gets a data folder under `_Plugins/`, an optional agent skill, and an optional MCP server config. The vault ships with no plugins installed — you add what you need.

## How Plugins Work

A plugin has up to four pieces:

| Piece | Location | Purpose |
|-------|----------|---------|
| Data folder | `_Plugins/{Name}/` | Files managed by the tool — do not hand-edit unless the plugin says you can |
| Skill doc | `_Config/Skills/{name}/SKILL.md` | Teaches agents how to use the tool's MCP tools or CLI |
| MCP config | Claude: `.mcp.json`; Codex: `.codex/config.toml` | Starts the tool's MCP server in the chosen client |
| Router entry | `_Config/router.md` | Makes the plugin visible to agents each session |

Only the data folder is strictly required. The other pieces depend on whether the tool has an MCP server, a CLI, or needs agent awareness.

## Installing a Plugin

Each tool provides its own install instructions, typically in its own repo. The usual vault-side steps are:

1. Install the tool's binary or application.
2. Create the plugin data folder: `mkdir -p _Plugins/{Name}`.
3. Copy the skill doc to `_Config/Skills/{name}/SKILL.md` if one is provided.
4. Add MCP config to `.mcp.json` or `.codex/config.toml` if the tool exposes MCP tools.
5. Update `_Config/router.md` if the plugin should be visible to agents each session.

If the plugin ships both a README and a Brain skill doc, treat the plugin README as the source of truth for install steps and the skill doc as the source of truth for how agents should use the tool once installed.

## Plugin Data Conventions

Plugins own their data folder completely. Brain does not impose an internal schema within `_Plugins/{Name}/`. However:

- Markdown with YAML frontmatter is strongly preferred when the plugin stores human-readable records.
- Flat frontmatter keeps files compatible with Dataview and similar Obsidian tooling.
- Files under `_Plugins/` are browsable in Obsidian and inherit the gold plugin theme.
- Dataview queries can target plugin data directly, for example: `FROM "_Plugins/MyTool"`.

## Available Plugins

| Plugin | Description | Install guide |
|--------|-------------|---------------|
| [Undertask](https://github.com/Rob-Morris/undertask) | Task management via MCP tools | [plugins/obsidian-brain](https://github.com/Rob-Morris/undertask/tree/main/plugins/obsidian-brain) |

## For Plugin Authors

This guide is for installing and using plugins in a vault. If you are building a plugin integration for Brain, use [docs/contributor/plugins.md](../contributor/plugins.md).
