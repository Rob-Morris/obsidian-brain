# Plugins

Guide for authors integrating an external tool with Brain. This document covers packaging, authoring, and maintenance of the Brain-facing parts of a plugin. For installing and using an existing plugin in a vault, see the repo user guide at [docs/user/plugins.md](../user/plugins.md). For the shipped in-vault overview that agents may rely on when they only have `.brain-core/`, see [src/brain-core/plugins.md](../../src/brain-core/plugins.md).

## Separation of Concerns

Keep plugin documentation split by audience:

- [src/brain-core/plugins.md](../../src/brain-core/plugins.md) — shipped in-vault overview for users and agents
- [docs/user/plugins.md](../user/plugins.md) — repo-level guide for installing and using plugins in a vault
- [docs/contributor/plugins.md](plugins.md) — repo-level guide for writing and packaging a plugin integration

Do not put contributor workflow policy into the shipped brain-core doc. Do not duplicate end-user install walkthroughs in this authoring guide beyond what an author needs to package correctly.

When plugin install/use guidance changes, check both the repo user guide and the shipped in-vault overview. If the authoring model changes, update this document and decide whether the shipped overview also needs to change.

Contributor skills used to work on brain-core are separate from vault plugin skills. They live in `.claude/commands/` and are covered in [docs/CONTRIBUTING.md](../CONTRIBUTING.md).

## Packaging a Plugin for Brain

### 1. Choose a folder name

The plugin data folder lives at `_Plugins/{Name}/` inside the vault. Use title case for the display name, for example `Undertask`, `Bookmarks`, or `Contacts`. The folder inherits gold styling automatically.

### 2. Write the skill doc

The skill doc teaches agents how to interact with your tool. It lives at `_Config/Skills/{name}/SKILL.md` in the vault.

```yaml
---
name: my-tool
description: |
  One-line summary of what the skill does.
  When to trigger it, for example "when the user asks to manage X".
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

- Status values, tag conventions, or data-shape assumptions

## CLI Fallback

If the tool has a CLI, document it here.
```

Frontmatter fields to keep stable:

- `name`: kebab-case skill name matching the folder in `_Config/Skills/`
- `description`: the trigger-oriented summary used by clients to decide when to load the skill
- `{tool}_version`: the version of the external tool the skill targets
- `source`: the canonical upstream location of the skill doc

### 3. Document MCP config

If the tool exposes MCP tools, document the client config entries users need to add.

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

Codex project scope uses `.codex/config.toml`:

```toml
[mcp_servers.my-tool]
command = "my-tool-mcp"
args = []

[mcp_servers.my-tool.env]
MY_TOOL_DATA = "/path/to/your-vault/_Plugins/MyTool"
```

When your plugin supports both Claude and Codex, document both surfaces. The exact installation-local paths belong in the plugin's own install README, not in Brain's shared docs.

### 4. Package the integration files in your repo

Keep Brain integration assets in your tool's repo, not in the Brain repo. A typical layout:

```text
my-tool/
├── plugins/
│   └── obsidian-brain/
│       ├── SKILL.md
│       └── README.md
├── src/
│   └── ...
```

That plugin README should cover prerequisites, data-folder creation, skill-doc copy instructions, MCP config, and router updates. See the [Undertask plugin](https://github.com/Rob-Morris/undertask/tree/main/plugins/obsidian-brain) for a working example.

### 5. Track interface versions

Include a `{tool}_version` field in the skill doc frontmatter. When the external tool's interface changes, update the skill doc in lockstep so users can compare their installed skill version against the tool version they are running.

## Design Constraints

- Plugins own their `_Plugins/{Name}/` data folder and its internal schema.
- The skill doc is optional, but recommended for any plugin with MCP tools or CLI commands.
- The router entry is optional; only add it when the plugin should be visible to agents by default.
- User-facing install guidance belongs in the plugin's own README and in `docs/user/plugins.md`, not here.
- Shipped in-vault overview material belongs in `src/brain-core/plugins.md`, not here.
