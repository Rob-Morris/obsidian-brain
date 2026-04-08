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

System folders (`_Assets/`, `_Config/`, `_Plugins/`, `.obsidian/`) are infrastructure, not artefacts. Living artefact folders may contain an `_Archive/` subfolder for documents that have reached a terminal status and transferred authority to a successor. Archived files are date-prefixed (`yyyymmdd-Title.md`) and carry `archiveddate: YYYY-MM-DD` in frontmatter. `brain_action("rename")` handles wikilink updates automatically during archiving (Obsidian CLI first, grep-replace fallback). Archived files are excluded from search indexing.

## Architecture

For the system architecture (components, data flow, agent reading flow, folder tiers), see [Architecture Overview](architecture/overview.md).

## Colour System

CSS snippet at `.obsidian/snippets/brain-folder-colours.css` driven by a palette of CSS variables. Graph view colours at `.obsidian/graph.json` `colorGroups`. System design documented in `.brain-core/colours.md`; instance assignments in `_Config/Styles/obsidian.md`.

- Palette colours defined as `--palette-*` variables
- Theme variables (`--theme-*-fg`, `--theme-*-bg`) reference palette colours
- Temporal children use a blend formula: `result = base + (rose - base) × 0.35`
- Each tier has consistent CSS selector patterns for folders, subfolders, and files
- Graph view: same colour assignments written as `colorGroups` in `graph.json` (canvas-based — CSS doesn't apply). Merge preserves existing graph settings

## Extension Procedures

Documented in `.brain-core/standards/extending/README.md`:

- **New living artefact**: create at root, add taxonomy file, reference standards (provenance/archiving) in taxonomy if applicable, optionally add router trigger, run `brain_action("compile")` — colours are auto-generated
- **New temporal child**: create under `_Temporal/`, add taxonomy file, reference standards (provenance/archiving) in taxonomy if applicable, optionally add router trigger, run `brain_action("compile")` — rose-blended colours are auto-generated
- **New config child**: create under `_Config/`, inherits purple styling
- **New plugin**: create under `_Plugins/`, inherits orchid styling, add skill if it has tools

### Hub Pattern

Some living artefact types act as hubs — containers that group related artefacts (temporal or living) via nested tags. The hub file carries a nested tag (e.g. `person/{slug}`, `project/{slug}`, `journal/{slug}`), and all related artefacts share that tag. The hub is the index; the tag is the query mechanism.

This pattern is useful when a single living artefact organises a stream of related work or content across the vault. Current examples: People (groups observations and other artefacts related to a person via `person/{slug}`), Projects (groups plans, research, designs, logs via `project/{slug}`), Journals (groups journal entries via `journal/{slug}`), Workspaces (groups brain artefacts related to a bounded data container via `workspace/{slug}`).

### Master/Sub-Artefact Convention

When a master artefact accumulates enough related sub-artefacts to crowd the type folder, sub-artefacts move into a named subfolder. The master stays in the type root as the entry point; the subfolder groups its children. Sub-artefacts inherit the parent type — no separate taxonomy or CSS needed.

```
Designs/
  Brain Master Design.md          ← master stays in root
  Brain/                          ← sub-artefacts cluster here
    Brain Inbox.md
    Brain Mcp Server.md
```

Archiving uses `brain_action("archive")` which moves artefacts to a top-level `_Archive/` directory at the vault root, preserving type/project structure inside (e.g. `_Archive/Designs/Brain/20260405-old.md`). Archived files are excluded from the vault file index, search, and all normal operations. Projects archive as-is — no flattening.

## Documentation

- `docs/specification.md` — this file; design rationale and structural decisions
- `docs/user/getting-started.md` — installation, first vault, orientation
- `docs/user/workflows.md` — day-to-day usage patterns and examples
- `docs/user/system-guide.md` — artefact system mechanics, lifecycle, extension
- `docs/user/template-library-guide.md` — template library, available types, install procedures
- `docs/user-reference.md` — full type specs, conventions, config reference
- `docs/changelog.md` — single-file version history. When it exceeds ~500 lines, consider splitting into per-version files under `docs/changelog/` with the main file as an index
- `.canaries/pre-commit.md` — pre-commit canary: versioning, changelog, routing table, cross-checks
- `docs/contributing.md` — contributor guide: doc architecture, drift prevention, testing, pitfalls
- `docs/standards/canary.md` — canary brief pattern (reusable technique for testing subjective agent work)
- `docs/tooling.md` — redirect to `docs/functional/` and `docs/architecture/decisions/`
- `docs/plugins.md` — plugin writing and installation guide

## What Ships in the Starter Vault

**Living artefacts:**
- `Daily Notes/` — end-of-day summaries
- `Designs/` — design documents, wireframes, and specs
- `Documentation/` — guides, standards, and reference material
- `Ideas/` — concepts shaped to clarity through iterative refinement
- `Notes/` — low-friction knowledge notes
- `People/` — person hubs
- `Projects/` — project index files linking related artefacts
- `Tasks/` — persistent units of work, tracked and linked to artefacts
- `Workspaces/` — scoped data containers
- `Writing/` — long-form written works with lifecycle

**Temporal artefacts:**
- `_Temporal/Bug Logs/` — point-in-time records of broken behaviour
- `_Temporal/Captures/` — external material ingested verbatim
- `_Temporal/Cookies/` — user satisfaction tracking
- `_Temporal/Decision Logs/` — reasoning behind choices
- `_Temporal/Friction Logs/` — signal accumulator for friction: missing context, inconsistencies, suboptimal outcomes
- `_Temporal/Ingestions/` — processing records for content decomposition
- `_Temporal/Logs/` — daily activity logs
- `_Temporal/Mockups/` — visual or interactive prototypes
- `_Temporal/Observations/` — timestamped facts and things noticed
- `_Temporal/Plans/` — pre-work plans
- `_Temporal/Presentations/` — slide decks (Marp)
- `_Temporal/Reports/` — distilled findings from detailed processes
- `_Temporal/Research/` — investigation notes
- `_Temporal/Shaping Transcripts/` — Q&A refinement sessions
- `_Temporal/Snippets/` — short crafted content derived from existing work
- `_Temporal/Thoughts/` — raw unformed thinking captured in the moment
- `_Temporal/Transcripts/` — conversation transcripts

**System:**
- `_Assets/` — non-markdown files and generated output (`Attachments/` + `Generated/`)
- `_Config/` — router, taxonomy, style, colours, templates, user skills, user preferences
- `_Plugins/` — empty, ready for plugins
- `_Workspaces/` — workspace data bucket (infrastructure)

Additional types are available in the artefact library (`.brain-core/artefact-library/`) for install as needed: Wiki, Journals, Zettelkasten (living); Idea Logs, Journal Entries (temporal).

The starter vault ships 27 defaults (10 living + 17 temporal) out of 32 in the library.
