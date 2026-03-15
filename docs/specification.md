# Obsidian Brain — Specification

Design rationale and structural decisions for the Brain vault.

## Overview

Brain is a self-extending system for organising Obsidian vaults, for agents and humans working together. Agents read a single router file on session start and follow workflow triggers throughout. Core methodology lives in `.brain-core/`, versioned and shared across vaults.

## Design Principles

1. **Files are the source of truth** — the vault is folders and Markdown files, no database. Works with Obsidian, agents, and plain text editors simultaneously.
2. **Every file belongs in a folder** — no content in the vault root
3. **Self-extending** — when content doesn't fit existing folders, the vault grows to accommodate it
4. **Lean instructions** — the router stays minimal; detailed reference lives in core docs and config files
5. **Agent-first** — `Agents.md` → `_Config/router.md` is the entry point; the router teaches agents everything they need for a session (`CLAUDE.md` is a symlink to `Agents.md` for Claude Code compatibility)

## Artefact Model

All content in the vault is an artefact, classified into two types:

| Type | Location | Behaviour |
|------|----------|-----------|
| **Living** | Vault root (e.g. `Wiki/`) | Evolves over time. Current version is the source of truth. |
| **Temporal** | `_Temporal/` (e.g. `_Temporal/Logs/`) | Bound to a moment. Written once, rarely edited. |

System folders (`_Config/`, `_Plugins/`, `.obsidian/`) are infrastructure, not artefacts.

## Architecture

### Core / Config Split

- **`.brain-core/`** — versioned methodology docs, copied into the vault during setup and upgrades (not symlinked, so vaults are self-contained and portable). How artefacts work, how to extend the vault, trigger system, colour system, plugin system, naming conventions. Read when the agent needs to understand or modify the system.
- **`_Config/`** — instance configuration. Router, taxonomy, style, colour assignments, templates, skills. Specific to this vault installation.
- **`_Config/router.md`** — the bridge. Lists this vault's artefact types, active triggers, and config file links. Read every session.
- **`_Config/Taxonomy/`** — one file per artefact type with detailed instructions. Agents read only the types they need.

### Agent Reading Flow

1. Agent reads `Agents.md` (or `CLAUDE.md` symlink) → directed to `_Config/router.md`
2. Router provides: artefact type map with taxonomy links, workflow triggers, config file links
3. Agent reads taxonomy files for the artefact types it's working with
4. Agent reads core docs only when extending the vault or understanding the system
5. Agent reads config files only when relevant (style when writing, principles when restructuring)

### Folder Tiers

Four tiers, each with distinct file explorer styling:

| Tier | Prefix | Colour | Purpose |
|------|--------|--------|---------|
| Artefact | none | Rose gold bg, unique foreground per folder | Primary content |
| Temporal | `_Temporal/` | Steel-tinted | Dated working files |
| Config | `_Config/` | Purple | System files |
| Plugin | `_Plugins/` | Gold | External tool data, skills, and MCP integrations |

## Colour System

CSS snippet at `.obsidian/snippets/folder-colours.css` driven by a palette of CSS variables. System design documented in `.brain-core/colours.md`; instance assignments in `_Config/Styles/obsidian.md`.

- Palette colours defined as `--palette-*` variables
- Theme variables (`--theme-*-fg`, `--theme-*-bg`) reference palette colours
- Temporal children use a blend formula: `result = base + (steel - base) × 0.35`
- Each tier has consistent CSS selector patterns for folders, subfolders, and files

## Extension Procedures

Documented in `.brain-core/extensions.md`:

- **New living artefact**: create at root, pick colour, add CSS, add to router, add taxonomy file
- **New temporal child**: create under `_Temporal/`, blend colour towards steel, add CSS, add to router, add taxonomy file
- **New config child**: create under `_Config/`, inherits purple styling
- **New plugin**: create under `_Plugins/`, inherits gold styling, add skill if it has tools

## What Ships in the Starter Vault

**Living artefacts:**
- `Wiki/` — interconnected knowledge base

**Temporal artefacts:**
- `_Temporal/Logs/` — daily activity logs
- `_Temporal/Transcripts/` — conversation transcripts
- `_Temporal/Plans/` — pre-work plans

**System:**
- `_Config/` — router, taxonomy, style, colours, templates, skills
- `_Plugins/` — empty, ready for plugins
