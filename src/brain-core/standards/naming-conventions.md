# Naming Conventions

Standard for artefact file naming across the vault. Individual artefact types define their specific pattern in their taxonomy file — this document explains the principles behind those patterns.

## Filenames Are Human-Readable

Obsidian displays the filename as the note title everywhere — sidebar, tabs, graph view, search. Filenames should be as close to human-readable as possible while staying filesystem-safe.

**Rules:**
- Preserve spaces, capitalisation, and unicode.
- Strip only characters unsafe on macOS, Windows, and Linux: `/ \ : * ? " < > |`.
- Trim whitespace and collapse consecutive spaces.

The `title_to_filename()` function in `_common.py` implements this.

**Hub tags** (e.g. `project/{slug}`, `workspace/{slug}`) still use the aggressive lowercase-hyphenated format via `title_to_slug()`. These are machine identifiers, not display names.

## Temporal Artefacts

**Pattern:** `yyyymmdd-{type-prefix}~{Title}.md`

- The date prefix is the creation date in `yyyymmdd` format (no separators).
- The type prefix is a short, lowercase, hyphenated identifier matching the artefact type.
- A tilde (`~`) separates the type prefix from the title.
- The title is a human-readable description of the content, preserving spaces and capitalisation.

**Why the prefix matters:** Wikilinks become self-documenting. `[[20260324-report~Session Failure Analysis]]` tells you the artefact type without opening the file. Without the prefix, `[[20260324-Session Failure Analysis]]` could be research, a plan, a transcript, or anything else. Temporal artefacts share a flat date-ordered namespace within their month folder, so the prefix is the only type signal in the filename.

**Special cases:**
- **Logs** use `yyyymmdd-log.md` (no title). One log per day — the date is the only identifier.
- **Shaping Transcripts** embed the source document type: `yyyymmdd-{sourcedoctype}-transcript~{Title}.md`.

**Examples:**

- `20260324-plan~API Refactor.md`
- `20260324-research~Tailscale Overview.md`
- `20260324-decision~Session Storage Approach.md`
- `20260324-log.md`

For the full list of types and their specific patterns, see the taxonomy files in `_Config/Taxonomy/`.

### Adding a New Temporal Type

When creating a new temporal artefact type, choose a short prefix that matches the type name (e.g. `report` for Reports, `friction` for Friction Logs). Follow the standard `yyyymmdd-{prefix}~{Title}.md` pattern unless the type has a genuine reason to differ (as logs do — one per day, no title needed).

## Living Artefacts

**Default pattern:** `{Title}.md`

Most living types use the title directly as the filename with no date or type prefix — the folder provides the type context (e.g. `Designs/Brain App Auth.md` is a design, `Wiki/Rust Lifetimes.md` is a wiki page).

**Date-prefixed living types:** Some user-facing living types prepend a creation date for chronological sort ordering in Obsidian's file explorer:
- **Notes** — `yyyymmdd - {Title}.md` (e.g. `20260315 - Rust Lifetimes.md`). The date helps users browse notes chronologically. The note itself is a living document — meant to be updated and expanded over time.
- **Daily Notes** — `yyyy-mm-dd ddd.md` (e.g. `2026-03-15 Sun.md`). A daily working document that the user builds throughout the day.

These date prefixes are a practical UX choice for human browsing, not a temporal signal. The files remain living artefacts — they evolve and can be updated at any time.
