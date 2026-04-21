# Plugins

Plugins integrate external tools into a Brain vault. Each plugin gets its own folder under `_Plugins/` and manages its own files independently of the vault's standard conventions.

## Plugin Structure

A plugin consists of:

- **Data folder** — `_Plugins/{Name}/` stores the plugin's files. The plugin owns this folder and its file format. Do not hand-edit plugin files unless the plugin's documentation says otherwise.
- **Skill document** — `_Config/Skills/{name}/SKILL.md` teaches agents how to use the plugin's tools. Optional but recommended for any plugin with MCP tools or CLI commands.
- **MCP configuration** — Claude uses `.mcp.json`; Codex uses `.codex/config.toml`. Only needed for plugins that expose MCP tools.
- **Router entry** — `_Config/router.md` makes the plugin visible to agents each session when needed

Only the data folder is strictly required. The other pieces depend on whether the tool has an MCP server, a CLI, or needs agent awareness.

## Using Plugins as an Agent

When working in a vault with plugins installed:

1. Look in `_Plugins/{Name}/` for the plugin's data.
2. If `_Config/Skills/{name}/SKILL.md` exists, read it before using the plugin's MCP tools or CLI.
3. Treat plugin-owned files as managed by the plugin unless its docs explicitly say they are safe to edit by hand.
4. If the plugin should be visible to agents by default, confirm it has a router entry in `_Config/router.md`.

Skills are the operational instructions. This file explains the plugin model; the skill doc explains how to use a specific plugin.

## Installing or Updating a Plugin

Each plugin provides its own install instructions, typically in the tool's repo or packaged Brain integration files. The usual vault-side steps are:

1. Install the tool's binary or application.
2. Create `_Plugins/{Name}/` in the vault.
3. Copy the plugin skill doc into `_Config/Skills/{name}/SKILL.md` if one is provided.
4. Add MCP config to `.mcp.json` or `.codex/config.toml` if the plugin exposes MCP tools.
5. Update `_Config/router.md` if the plugin should be visible to agents each session.

When a plugin ships both a README and a Brain skill doc, treat the plugin README as the source of truth for installation and the skill doc as the source of truth for agent usage after installation.

## File Conventions

Plugin files follow the plugin's own conventions, not the vault's standard frontmatter rules. They are:

- Browsable in Obsidian's file explorer
- Queryable via Dataview if the plugin uses compatible frontmatter
- Styled with the gold plugin theme in the file explorer
