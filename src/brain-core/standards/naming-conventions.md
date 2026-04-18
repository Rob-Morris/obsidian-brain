# Naming Conventions

Standard for artefact file naming across the vault. Individual artefact types define their specific pattern in their taxonomy file — this document explains the principles behind those patterns.

## Filenames Are Human-Readable

Obsidian displays the filename as the note title everywhere — sidebar, tabs, graph view, search. Filenames should be as close to human-readable as possible while staying filesystem-safe.

**Rules:**
- Preserve spaces, capitalisation, and unicode.
- Strip only characters unsafe on macOS, Windows, and Linux: `/ \ : * ? " < > |`.
- Trim whitespace and collapse consecutive spaces.

The `title_to_filename()` function in `_common/_slugs.py` implements this.

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

**Date-prefixed living types:** Some user-facing living types prepend a date for chronological sort ordering in Obsidian's file explorer:
- **Notes** — `yyyymmdd - {Title}.md` (e.g. `20260315 - Rust Lifetimes.md`). Date helps users browse notes chronologically. The note itself is a living document — meant to be updated and expanded over time.
- **Daily Notes** — `yyyy-mm-dd ddd.md` (e.g. `2026-03-15 Sun.md`). A daily working document that the user builds throughout the day.
- **Writing** — `yyyymmdd-{Title}.md` once published (drafts use `{Title}.md`).

Date prefixes are a synchronised signal derived from a source-of-truth frontmatter field. Humans and agents can read the filename; frontmatter remains authoritative. If filename and frontmatter disagree, frontmatter wins and the file is renamed on next edit — never the other way around.

## Date Source and Reconciliation

Every naming rule that uses a date token (`yyyymmdd`, `yyyy-mm-dd`, etc.) binds those tokens to a specific frontmatter field via `date_source`. The compiler validates this at router build time.

**Classification defaults and overrides:**

- **Temporal** artefacts default to `date_source: created` — no declaration needed on the rule.
- **Living** artefacts with date tokens *must* declare `date_source` explicitly. Compile fails otherwise.

**Declared `date_source` examples:**

- `living/daily-notes` → `date_source: date` (a dedicated per-type field; the subject date of the note, which may differ from physical creation when notes are backfilled)
- `living/writing` (on the `published` rule) → `date_source: publisheddate`
- `living/release` (on the `shipped` rule) → `date_source: shipped_at`

**The `{status}_at` convention.** When a status transition is observed, the runtime sets `{status}_at = now()` unless the type declares an `on_status_change` override. Example: `writing` transitioning to `published` runs `on_status_change: { published: { set: { publisheddate: now } } }` because its date field is `publisheddate`, not `published_at`. Types whose status-date field follows the `{status}_at` convention need no override.

**Reconciliation cascade.** When the runtime (edit, migration) needs a timestamp and frontmatter is incomplete, it applies this cascade, in order:

1. Frontmatter value if present and parseable.
2. Date prefix from the filename (`yyyymmdd` / `yyyy-mm-dd`) — `created` only.
3. File `mtime` — last resort.
4. `now()` — only when nothing else is available.

Reconciliation is idempotent: a second pass is a no-op. It runs only on write paths (`edit`, `rename`, migration) — `brain_read` is side-effect-free. Once reconciled, the values are written back to frontmatter and the filename is re-rendered from the selected rule; any disagreement is resolved in favour of the reconciled frontmatter.

**One-time migration bundle.** Vaults upgrading through v0.29.0 run the `migrate_to_0_29_0.py` bundle via `upgrade.py`. Its `pre_compile_patch` stage first remediates blocking missing-`date_source` taxonomy definitions narrowly enough to satisfy the new compiler gate, then its normal post-compile migration backfills `created`, `modified`, and any type-specific `date_source` fields across every artefact. Temporal artefacts whose resolved `created` falls in a different month than their current folder are relocated (wikilink-safe). The `check missing-timestamps` warning surfaces stragglers that arrive afterwards — a single edit reconciles them.

## Status-Aware and Frontmatter-Backed Naming

Most types declare a single naming pattern as a one-line `## Naming` entry (e.g. `` `{Title}.md` in `Wiki/` ``). Types whose filename depends on frontmatter state use the canonical **advanced `## Naming`** form — a `### Rules` table keyed on a frontmatter field, plus a `### Placeholders` table declaring any non-built-in placeholder.

**Built-in placeholders** (always available, need no declaration): `{Title}`, `{title}`, `{name}`, `{slug}`, `yyyymmdd`, `yyyy-mm-dd`, `yyyy`, `mm`, `dd`, `ddd`, `{sourcedoctype}`.

Any other placeholder must be declared with a backing frontmatter field.

**Canonical advanced form:**

```md
## Naming

Primary folder: `Releases/{Project}/`.

### Rules

| Match field | Match values | Pattern |
|---|---|---|
| `status` | `planned`, `active`, `cancelled` | `{Title}.md` |
| `status` | `shipped` | `{Version} - {Title}.md` |

### Placeholders

| Placeholder | Field | Required when field | Required values | Regex |
|---|---|---|---|---|
| `Version` | `version` | `status` | `shipped` | `^v?\d+\.\d+\.\d+$` |
```

**Cell grammar:**

- Values in `Match values` and `Required values` are backticked literals separated by commas (e.g. `` `planned`, `active` ``).
- `*` is the reserved wildcard — matches any value of the named field.
- Blank cells are invalid; use `*` when you mean unconditional.
- The first rule whose match succeeds wins.

**How the compiled contract drives tooling:** the compiled naming contract is the single source of truth for render (`create`, `convert`), validate (`check`), reverse-parse (`edit`, `convert`, `migrate_naming`), and rename-on-change (`edit`). When frontmatter changes select a different rule, `edit` re-renders and renames automatically. `rename` validates the destination against the contract with no bypass. See [[Status-Aware Artefact Naming]] for the full design.
