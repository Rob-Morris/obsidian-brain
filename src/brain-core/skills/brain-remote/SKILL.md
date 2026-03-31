---
name: brain-remote
description: |
  How to use the Brain vault from an external project via MCP tools.
  Triggers when brain MCP tools (brain_read, brain_search, brain_action)
  are available but the working directory is not the vault itself.
created: 2026-03-24T00:00:00+11:00
modified: 2026-03-24T00:00:00+11:00
---

# Brain Remote

Use the Brain vault's MCP tools from an external project folder. This skill covers the workflow when you're not working directly inside the vault.

## Session Start

1. `brain_read(resource="router")` — always-rules and metadata
2. `brain_read(resource="trigger")` — workflow triggers to follow throughout the session
3. `brain_read(resource="style", name="writing")` — language and tone preferences
4. Read taxonomy for artefact types you'll create: `brain_read(resource="type", name="{type-key}")`

Follow all triggers the same as in-vault work. The router is the source of truth.

## Key Differences

**Reading vault content:** Use MCP tools, not direct file access:
- `brain_read(resource=...)` for config (types, triggers, styles, templates, skills, plugins, memories)
- `brain_search(query=...)` for finding artefacts by content
- `brain_read(resource="compliance")` for structural health checks

**Writing vault content:** The vault root path is available from `brain_read(resource="environment")` → `vault_root`. Write vault artefacts using absolute paths to the vault. Files created in the project folder are project files, not vault artefacts.

**Mutations:** Use `brain_action` for vault-side operations:
- `brain_action(action="compile")` after config changes
- `brain_action(action="build_index")` after adding content
- `brain_action(action="rename", params={"source": "...", "dest": "..."})` for wikilink-safe moves

## Logging

After meaningful work, log it:
1. `brain_search(query="log", type="temporal/logs", top_k=1)` to find today's log
2. If none exists, create one following the logs taxonomy naming pattern
3. Append a timestamped entry

## Search

Use `brain_search` to find context:
- `brain_search(query="topic")` — full-text search
- `brain_search(query="topic", type="living/wiki")` — filter by type
- `brain_search(query="topic", tag="project/slug")` — filter by tag

## Setup

If brain MCP tools are not available in this project:

```bash
# Link this project to a vault
python3 /path/to/vault/.brain-core/scripts/init.py --project .

# Or register a default brain for all projects
python3 /path/to/vault/.brain-core/scripts/init.py --user
```

## Priority

If anything here conflicts with the router or brain-core index, the router wins.
