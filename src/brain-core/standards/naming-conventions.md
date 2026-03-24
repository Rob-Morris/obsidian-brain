# Naming Conventions

Standard for artefact file naming across the vault. Individual artefact types define their specific pattern in their taxonomy file — this document explains the principles behind those patterns.

## Temporal Artefacts

**Pattern:** `yyyymmdd-{type-prefix}--{slug}.md`

- The date prefix is the creation date in `yyyymmdd` format (no separators).
- The type prefix is a short, lowercase, hyphenated identifier matching the artefact type.
- A double-dash `--` separates the type prefix from the slug.
- The slug is a lowercase, hyphenated description of the content.

**Why the prefix matters:** Wikilinks become self-documenting. `[[20260324-report--session-failure-analysis]]` tells you the artefact type without opening the file. Without the prefix, `[[20260324-session-failure-analysis]]` could be research, a plan, a transcript, or anything else. Temporal artefacts share a flat date-ordered namespace within their month folder, so the prefix is the only type signal in the filename.

**Special cases:**
- **Logs** use `yyyymmdd-log.md` (no slug). One log per day — the date is the only identifier.
- **Shaping Transcripts** embed the source document type: `yyyymmdd-{sourcedoctype}-transcript--{slug}.md`.

**Examples:**

- `20260324-plan--api-refactor.md`
- `20260324-research--tailscale-overview.md`
- `20260324-decision--session-storage-approach.md`
- `20260324-log.md`

For the full list of types and their specific patterns, see the taxonomy files in `_Config/Taxonomy/`.

### Adding a New Temporal Type

When creating a new temporal artefact type, choose a short prefix that matches the type name (e.g. `report` for Reports, `friction` for Friction Logs). Follow the standard `yyyymmdd-{prefix}--{slug}.md` pattern unless the type has a genuine reason to differ (as logs do — one per day, no slug needed).

## Living Artefacts

**Default pattern:** `{slug}.md`

Most living types use a plain slug with no date or type prefix — the folder provides the type context (e.g. `Designs/gizmo.md` is a design, `Wiki/rust-lifetimes.md` is a wiki page).

**Date-prefixed living types:** Some user-facing living types prepend a creation date for chronological sort ordering in Obsidian's file explorer:
- **Notes** — `yyyymmdd - {Title}.md` (e.g. `20260315 - Rust Lifetimes.md`). The date helps users browse notes chronologically. The note itself is a living document — meant to be updated and expanded over time.
- **Daily Notes** — `yyyy-mm-dd ddd.md` (e.g. `2026-03-15 Sun.md`). A daily working document that the user builds throughout the day.

These date prefixes are a practical UX choice for human browsing, not a temporal signal. The files remain living artefacts — they evolve and can be updated at any time.
