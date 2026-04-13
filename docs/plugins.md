# Plugins

Plugins connect external tools to the vault. Each plugin gets a data folder under `_Plugins/`, an optional agent skill, and an optional MCP server config. The vault ships with no plugins installed — you add what you need.

## How Plugins Work

A plugin has up to four pieces:

| Piece | Location | Purpose |
|-------|----------|---------|
| Data folder | `_Plugins/{Name}/` | Files managed by the tool — do not hand-edit |
| Skill doc | `_Config/Skills/{name}/SKILL.md` | Teaches agents how to use the tool's MCP tools or CLI |
| MCP config | Claude: `.mcp.json`; Codex: `.codex/config.toml` | Starts the tool's MCP server in the chosen client |
| Router entry | `_Config/router.md` | Makes the plugin visible to agents each session |

Only the data folder is strictly required. The other pieces depend on whether the tool has an MCP server, a CLI, or needs agent awareness.

## Installing a Plugin

Each tool provides its own install instructions (typically a README in the tool's repo). The general steps are:

1. **Install the tool's binary** — build from source or download a release
2. **Create the data folder**: `mkdir -p _Plugins/{Name}` (in your vault)
3. **Copy the skill doc** (if provided): copy to `_Config/Skills/{name}/SKILL.md`
4. **Add MCP config** (if the tool has an MCP server): create or update the relevant client config (`.mcp.json` for Claude project scope, `.codex/config.toml` for Codex project scope)
5. **Update the router**: if the plugin should be visible to agents, add it to `_Config/router.md`

## Writing a Plugin

A plugin is any tool that manages files in the vault. To make your tool work with Brain:

### 1. Choose a folder name

The folder lives at `_Plugins/{Name}/` inside the vault. Use title case for the display name (e.g. `Undertask`, `Bookmarks`, `Contacts`). The folder inherits gold styling automatically.

### 2. Write a skill doc

The skill doc teaches agents how to interact with your tool. It lives at `_Config/Skills/{name}/SKILL.md` in the vault.

```yaml
---
name: my-tool
description: |
  One-line summary of what the skill does.
  When to trigger it (e.g. "when the user asks to manage X").
created: 2026-01-01T00:00:00+00:00
modified: 2026-01-01T00:00:00+00:00
my_tool_version: 1.0.0
source: https://github.com/owner/repo/blob/main/plugins/obsidian-brain/SKILL.md
---

# My Tool

Brief description of what the tool does and how agents should use it.

## When to Use

- Bullet list of triggers

## MCP Tools

Document each tool with its parameters.

## Conventions

- Status values, tag conventions, etc.

## CLI Fallback

If the tool has a CLI, document it here.
```

Key fields in the frontmatter:

- **`name`**: kebab-case skill name, matches the folder name in `_Config/Skills/`
- **`description`**: used by Claude Code to decide when to load the skill — be specific about triggers
- **`{tool}_version`**: tracks which version of your tool the skill was written for
- **`source`**: URL to the canonical version of this file in your repo, so users can check for updates

### 3. Add MCP server config

If your tool has an MCP server, document the client config entries users need to add.

Claude project scope uses `.mcp.json`:

```json
{
  "mcpServers": {
    "my-tool": {
      "command": "my-tool-mcp",
      "args": [],
      "env": {
        "MY_TOOL_DATA": "/path/to/your-vault/_Plugins/MyTool"
      }
    }
  }
}
```

Multiple plugins can coexist in the same Claude `.mcp.json` by adding entries to `mcpServers`.

Codex project scope uses `.codex/config.toml`:

```toml
[mcp_servers.my-tool]
command = "my-tool-mcp"
args = []

[mcp_servers.my-tool.env]
MY_TOOL_DATA = "/path/to/your-vault/_Plugins/MyTool"
```

The vault's project MCP config is installation-local. Document both client surfaces when your plugin supports both Claude and Codex.

### 4. Package the install files

Keep your plugin's Brain integration files in your tool's repo, not in the Brain repo. A typical layout:

```
my-tool/
├── plugins/
│   └── obsidian-brain/
│       ├── SKILL.md          # skill doc — copied into the vault
│       └── README.md         # install instructions
├── src/
│   └── ...
```

The README should cover: prerequisites, creating the data folder, copying the skill doc, adding MCP config, and updating the router. See the [Undertask plugin](https://github.com/Rob-Morris/undertask/tree/main/plugins/obsidian-brain) for a working example.

### 5. Version tracking

Include a `{tool}_version` field in the skill doc's frontmatter. When your tool's interface changes (new MCP tools, changed parameters, new CLI commands), bump the version in the skill doc. Users can compare the installed version against their tool version to know when an update is needed.

## File Conventions

Plugins own their data folder completely. The vault imposes no structure within `_Plugins/{Name}/` — use whatever format your tool needs. However:

- **Markdown with YAML frontmatter** is strongly preferred, since Obsidian can render and query it
- **Flat frontmatter** (no nested objects) keeps files compatible with Dataview and other Obsidian plugins
- Files in `_Plugins/` are browsable in Obsidian's file explorer and show with gold styling
- Dataview queries can target plugin data: `FROM "_Plugins/MyTool"`

## Available Plugins

| Plugin | Description | Install guide |
|--------|-------------|---------------|
| [Undertask](https://github.com/Rob-Morris/undertask) | Task management via MCP tools | [plugins/obsidian-brain](https://github.com/Rob-Morris/undertask/tree/main/plugins/obsidian-brain) |
