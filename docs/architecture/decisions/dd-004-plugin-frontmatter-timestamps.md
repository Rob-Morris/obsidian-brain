# DD-004: Obsidian Plugin Absorbs Frontmatter Timestamps

**Status:** Proposed

## Context

Brain-core artefacts carry `created` and `modified` ISO 8601 timestamps in frontmatter. Scripts manage these timestamps when creating or editing files through the MCP/script path. However, when a user edits a file directly in Obsidian (outside MCP), the `modified` timestamp goes stale immediately.

## Decision

A companion Obsidian plugin will automatically update `created` and `modified` frontmatter timestamps when files are opened, created, or saved within Obsidian. This removes the gap where direct edits produce stale metadata.

## Consequences

- Frontmatter timestamps stay accurate regardless of how a file was edited (MCP, CLI, or directly in Obsidian).
- The plugin and scripts must agree on the timestamp format (ISO 8601) to avoid conflicts.
- This is a Proposed decision — the plugin does not yet exist; the gap is currently accepted.
- When implemented, the plugin must not overwrite `created` on existing files that already have the field.
