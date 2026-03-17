# Obsidian Brain — Specification

Design rationale and structural decisions for the Brain vault.

## Overview

Brain is a self-extending system for organising Obsidian vaults, for agents and humans working together. Agents read a single router file on session start and follow workflow triggers throughout. Core methodology lives in `.brain-core/`, versioned and shared across vaults.

## Design Principles

1. **Files are the source of truth** — the vault is folders and Markdown files, no database. Works with Obsidian, agents, and plain text editors simultaneously.
2. **The filesystem is the canonical index** — manually maintained file lists are redundant. The vault's folder structure declares its artefact types; tooling discovers them by scanning, not by reading a registry.
3. **Every file belongs in a folder** — no content in the vault root
4. **Self-extending** — when content doesn't fit existing folders, the vault grows to accommodate it
5. **Lean instructions** — the router stays minimal; detailed reference lives in core docs and config files
6. **Agent-first** — `Agents.md` → `_Config/router.md` is the entry point; the router teaches agents everything they need for a session (`CLAUDE.md` is a symlink to `Agents.md` for Claude Code compatibility)

## Artefact Model

All content in the vault is an artefact, classified into two types:

| Type | Location | Behaviour |
|------|----------|-----------|
| **Living** | Vault root (e.g. `Wiki/`) | Evolves over time. Current version is the source of truth. |
| **Temporal** | `_Temporal/` (e.g. `_Temporal/Logs/`) | Bound to a moment. Written once, rarely edited. |

System folders (`_Attachments/`, `_Config/`, `_Plugins/`, `.obsidian/`) are infrastructure, not artefacts.

## Architecture

### Core / Config Split

- **`.brain-core/`** — versioned methodology docs, copied into the vault during setup and upgrades (not symlinked, so vaults are self-contained and portable). `taxonomy/readme.md` is a lean pointer to `_Config/Taxonomy/` — it explains the classification system and key derivation convention, not a full artefact reference. Other core docs cover extensions, triggers, colours, plugins. Read when the agent needs to understand or modify the system.
- **`_Config/`** — instance configuration. Router, taxonomy, style, colour assignments, templates, skills. Specific to this vault installation.
- **`_Config/router.md`** — the bridge. Lean format: capability detection, always-rules, and conditional trigger gotos pointing to taxonomy/skill files. Read every session (~45 tokens).
- **`_Config/Taxonomy/`** — one file per artefact type with detailed instructions. Agents read only the types they need.

### Agent Reading Flow

Four-tier boot, each degrading gracefully:

1. **MCP tools** — if `brain_read`/`brain_action`/`brain_search` are available, the agent uses them. Lowest token cost, structured responses.
2. **Compiled router** — if the MCP server isn't available but the compiled router exists (`_Config/.compiled-router.json`), the agent reads it for a structured, environment-aware view of the vault.
3. **Lean router** — if neither MCP nor compiled router is available, the agent reads `Agents.md` → `_Config/router.md` (~45 tokens). The router provides conditional trigger pointers and vault-specific rules. Taxonomy files are loaded on demand when a condition matches.
4. **Naive fallback** — if the agent has no knowledge of the system, it reads `Agents.md` → `router.md` → follows wikilinks. The filesystem itself is discoverable: root-level non-system folders are living types, `_Temporal/` subfolders are temporal types.

All tiers begin by reading `index.md` (via the router's "Always read [[.brain-core/index]]" directive) for system principles, always-rules, and tooling instructions. This ensures MCP-only agents receive the taxonomy-first gate and system constraints.

### Folder Tiers

Four tiers, each with distinct file explorer styling:

| Tier | Prefix | Colour | Purpose |
|------|--------|--------|---------|
| Artefact | none | Rose gold bg, unique foreground per folder | Primary content |
| Temporal | `_Temporal/` | Steel-tinted | Dated working files |
| Attachments | `_Attachments/` | Slate | Non-markdown files (images, PDFs, etc.) |
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
- `_Attachments/` — non-markdown files (images, PDFs, etc.)
- `_Config/` — router, taxonomy, style, colours, templates, skills
- `_Plugins/` — empty, ready for plugins
