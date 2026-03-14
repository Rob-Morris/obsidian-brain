# Plugins

Plugins integrate external tools into a Brain vault. Each plugin gets its own folder under `_Plugins/` and manages its own files independently of the vault's standard conventions.

## Plugin Structure

A plugin consists of:

- **Data folder** — `_Plugins/{Name}/` stores the plugin's files. The plugin owns this folder and its file format. Do not hand-edit plugin files unless the plugin's documentation says otherwise.
- **Skill document** — `_Config/Skills/{name}/SKILL.md` teaches agents how to use the plugin's tools. Optional but recommended for any plugin with MCP tools or CLI commands.
- **MCP configuration** — `.mcp.json` at the vault root registers the plugin's MCP server. Only needed for plugins that expose MCP tools.

## File Conventions

Plugin files follow the plugin's own conventions, not the vault's standard frontmatter rules. They are:

- Browsable in Obsidian's file explorer
- Queryable via Dataview if the plugin uses compatible frontmatter
- Styled with the gold plugin theme in the file explorer

## Writing a Plugin

1. Create `_Plugins/{Name}/`.
2. Add an entry to the router if the plugin should be visible to agents.
3. Write a skill document at `_Config/Skills/{name}/SKILL.md` describing available tools.
4. If the plugin has an MCP server, add its configuration to `.mcp.json`.

See the [plugin guide](../../docs/plugins.md) for detailed instructions on writing and packaging plugins.

## Versioning

Skill documents should include a version field in their frontmatter tracking which version of the external tool they describe. When the tool updates, pull the latest skill document.
