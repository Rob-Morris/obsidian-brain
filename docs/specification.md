# Obsidian Brain — Specification

Design rationale and structural decisions for the Brain vault.

## Overview

Brain is an Obsidian vault methodology designed for use with AI agents (Claude Code, MCP tools). It provides a self-documenting structure where agents read a single router file on session start and follow workflow triggers throughout. Core methodology lives in `.brain-core/`, versioned and shared across vaults.

## Design Principles

1. **Every file belongs in a folder** — no content in the vault root
2. **Self-extending** — when content doesn't fit existing folders, the vault grows to accommodate it
3. **Lean instructions** — the router stays minimal; detailed reference lives in core docs and config files
4. **Agent-first** — `CLAUDE.md` → `router.md` is the entry point; the router teaches agents everything they need for a session

## Artefact Model

Everything in the vault is an artefact, classified into two types:

| Type | Location | Behaviour |
|------|----------|-----------|
| **Living** | Vault root (e.g. `Wiki/`) | Evolves over time. Current version is the source of truth. |
| **Temporal** | `_Temporal/` (e.g. `_Temporal/Logs/`) | Bound to a moment. Written once, rarely edited. |

System folders (`_Config/`, `_Plugins/`, `.obsidian/`) are infrastructure, not artefacts.

## Architecture

### Core / Config Split

- **`.brain-core/v1.0/`** — versioned methodology docs. How artefacts work, how to extend the vault, trigger system, colour system, plugin system, naming conventions. Read when the agent needs to understand or modify the system.
- **`_Config/`** — instance configuration. Style preferences, vault principles, colour assignments, templates, skills. Specific to this vault installation.
- **`router.md`** — the bridge. Lists this vault's artefact types, active triggers, and config file links. Read every session.

### Agent Reading Flow

1. Agent reads `CLAUDE.md` → directed to `router.md`
2. Router provides: artefact type map, workflow triggers, config file links
3. Agent reads core docs only when extending the vault or understanding the system
4. Agent reads config files only when relevant (style when writing, principles when restructuring)

### Folder Tiers

Four tiers, each with distinct file explorer styling:

| Tier | Prefix | Colour | Purpose |
|------|--------|--------|---------|
| Artefact | none | Per-folder unique | Primary content |
| Temporal | `_Temporal/` | Rose-tinted | Dated working files |
| Config | `_Config/` | Purple | System files |
| Plugin | `_Plugins/` | Gold | Data managed by external tools |

## Colour System

CSS snippet at `.obsidian/snippets/folder-colours.css` driven by a palette of CSS variables. System design documented in `.brain-core/v1.0/colours.md`; instance assignments in `_Config/colours.md`.

- Palette colours defined as `--palette-*` variables
- Theme variables (`--theme-*-fg`, `--theme-*-bg`) reference palette colours
- Temporal children use a blend formula: `result = base + (rose - base) × 0.35`
- Each tier has consistent CSS selector patterns for folders, subfolders, and files

## Extension Procedures

Documented in `.brain-core/v1.0/extensions.md`:

- **New living artefact**: create at root, pick colour, add CSS, add to router
- **New temporal child**: create under `_Temporal/`, blend colour towards rose, add CSS, add to router
- **New config child**: create under `_Config/`, inherits purple styling
- **New plugin**: create under `_Plugins/`, inherits gold styling, add skill if it has tools

## What Ships in the Starter Vault

**Living artefacts:**
- `Wiki/` — interconnected knowledge base

**Temporal artefacts:**
- `_Temporal/Logs/` — daily activity logs
- `_Temporal/Transcripts/` — conversation transcripts

**System:**
- `_Config/` — style, principles, colours, templates, skills
- `_Plugins/` — empty, ready for plugins
