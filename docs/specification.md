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

System folders (`_Attachments/`, `_Config/`, `_Plugins/`, `.obsidian/`) are infrastructure, not artefacts. Living artefact folders may contain an `_Archive/` subfolder for documents that have reached a terminal status and transferred authority to a successor. Archived files are date-prefixed (`yyyymmdd-slug.md`) and carry `archiveddate: YYYY-MM-DD` in frontmatter. `brain_action("rename")` handles wikilink updates automatically during archiving (Obsidian CLI first, grep-replace fallback). Archived files are excluded from search indexing.

## Architecture

### Core / Config Split

- **`.brain-core/`** — versioned methodology docs, copied into the vault during setup and upgrades (not symlinked, so vaults are self-contained and portable). `taxonomy/readme.md` is a lean pointer to `_Config/Taxonomy/` — it explains the classification system and key derivation convention, not a full artefact reference. Other core docs cover extensions, triggers, colours, plugins. Read when the agent needs to understand or modify the system.
- **`.brain-core/skills/`** — core skills. System-provided skill documents that teach agents how to use brain-core tools. Discovered by the compiler, tagged `"source": "core"`. Not user-editable; overwritten on brain-core upgrade.
- **`_Config/`** — instance configuration. Router, taxonomy, style, colour assignments, templates, user skills, memories, user preferences. Specific to this vault installation.
- **`_Config/User/`** — vault owner's standing instructions. `preferences-always.md` (workflow preferences, quality standards, agent behaviour rules) and `gotchas.md` (learned lessons from previous sessions). Both read every session when present.
- **`_Config/router.md`** — the bridge. Lean format: capability detection, always-rules, and conditional trigger gotos pointing to taxonomy/skill files. Read every session (~45 tokens).
- **`_Config/Taxonomy/`** — one file per artefact type with detailed instructions. Agents read only the types they need.

### Agent Reading Flow

Four-tier boot, each degrading gracefully:

1. **MCP tools** — if `brain_read`/`brain_action`/`brain_search` are available, the agent uses them. Lowest token cost, structured responses, in-memory caching.
2. **Scripts** — if MCP isn't available, `.brain-core/scripts/` provides full functionality: `read.py` (query compiled router), `search_index.py` (BM25 search), `rename.py` (wikilink-safe rename), `compile_router.py`, `check.py`. Same logic as MCP — the server imports from these scripts.
3. **Lean router** — if neither MCP nor scripts are available, the agent reads `Agents.md` → `_Config/router.md` (~45 tokens). The router provides conditional trigger pointers and vault-specific rules. Taxonomy files are loaded on demand when a condition matches.
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

CSS snippet at `.obsidian/snippets/folder-colours.css` driven by a palette of CSS variables. Graph view colours at `.obsidian/graph.json` `colorGroups`. System design documented in `.brain-core/colours.md`; instance assignments in `_Config/Styles/obsidian.md`.

- Palette colours defined as `--palette-*` variables
- Theme variables (`--theme-*-fg`, `--theme-*-bg`) reference palette colours
- Temporal children use a blend formula: `result = base + (rose - base) × 0.35`
- Each tier has consistent CSS selector patterns for folders, subfolders, and files
- Graph view: same colour assignments written as `colorGroups` in `graph.json` (canvas-based — CSS doesn't apply). Merge preserves existing graph settings

## Extension Procedures

Documented in `.brain-core/extensions.md`:

- **New living artefact**: create at root, add taxonomy file, reference standards (provenance/archiving) in taxonomy if applicable, optionally add router trigger, run `brain_action("compile")` — colours are auto-generated
- **New temporal child**: create under `_Temporal/`, add taxonomy file, reference standards (provenance/archiving) in taxonomy if applicable, optionally add router trigger, run `brain_action("compile")` — rose-blended colours are auto-generated
- **New config child**: create under `_Config/`, inherits purple styling
- **New plugin**: create under `_Plugins/`, inherits gold styling, add skill if it has tools

### Hub Pattern

Some living artefact types act as hubs — containers that group related artefacts (temporal or living) via nested tags. The hub file carries a nested tag (e.g. `project/{slug}`, `journal/{slug}`), and all related artefacts share that tag. The hub is the index; the tag is the query mechanism.

This pattern is useful when a single living artefact organises a stream of related work or content across the vault. Current examples: Projects (groups plans, research, designs, logs via `project/{slug}`), Journals (groups journal entries via `journal/{slug}`), Workspaces (groups brain artefacts related to a bounded data container via `workspace/{slug}`).

## Documentation

- `docs/specification.md` — this file; design rationale and structural decisions
- `docs/user-guide.md` — example-driven walkthrough for vault users
- `docs/user-reference.md` — full type specs, conventions, config reference
- `docs/changelog.md` — single-file version history. When it exceeds ~500 lines, consider splitting into per-version files under `docs/changelog/` with the main file as an index
- `docs/canaries/pre-commit.md` — pre-commit canary: versioning, changelog, routing table, cross-checks
- `docs/contributing.md` — contributor guide: doc architecture, drift prevention, testing, pitfalls
- `docs/standards/canary.md` — generic canary pattern (project-independent)
- `docs/standards/README.md` — standards index
- `docs/tooling.md` — technical design reference with DD index
- `docs/plugins.md` — plugin writing and installation guide

## What Ships in the Starter Vault

**Living artefacts:**
- `Wiki/` — interconnected knowledge base
- `Daily Notes/` — end-of-day summaries
- `Notes/` — low-friction knowledge notes

**Temporal artefacts:**
- `_Temporal/Logs/` — daily activity logs
- `_Temporal/Plans/` — pre-work plans
- `_Temporal/Transcripts/` — conversation transcripts
- `_Temporal/Research/` — investigation notes
- `_Temporal/Decision Logs/` — reasoning behind choices
- `_Temporal/Friction Logs/` — signal accumulator for maintenance
- `_Temporal/Shaping Transcripts/` — Q&A refinement sessions
- `_Temporal/Cookies/` — user satisfaction tracking

**System:**
- `_Attachments/` — non-markdown files (images, PDFs, etc.)
- `_Config/` — router, taxonomy, style, colours, templates, user skills, user preferences
- `_Plugins/` — empty, ready for plugins

Additional types are available in the artefact library (`.brain-core/artefact-library/`) for install as needed: Designs, Documentation, Ideas, Journals, Projects, Workspaces, Writing, Zettelkasten (living); Idea Logs, Journal Entries, Thoughts, Reports, Snippets, Mockups (temporal).
