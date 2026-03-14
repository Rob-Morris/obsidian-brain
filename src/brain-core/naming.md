# Naming Conventions

## Slug Format

Most files use descriptive slugs: lowercase words separated by hyphens. Keep slugs concise but descriptive.

Examples: `pistols-at-dawn-discord-bot.md`, `ai-writing-style-guide.md`

## Date Prefixes

Temporal files use date prefixes for chronological sorting:

| Pattern | Used by | Example |
|---|---|---|
| `log--yyyy-mm-dd.md` | Logs | `log--2026-03-10.md` |
| `yyyymmdd-{slug}.md` | Most temporal types | `20260310-vault-restructure.md` |
| `yyyymmdd-{type}-transcript--{slug}.md` | Transcripts | `20260307-design-transcript--discord-bot.md` |

The double-dash `--` separates structural prefixes from the slug.

## Month Folders

All temporal content is grouped into `yyyy-mm/` month folders. Files sit flat in their month folder — no subfolders within months.

Example: `_Temporal/Logs/2026-03/log--2026-03-10.md`

## Frontmatter

Every artefact file (living and temporal) has YAML frontmatter with at least `tags`. See [[.brain-core/v1.0/artefacts|Artefacts]] for the full specification.

## Wikilinks

Use Obsidian wikilinks with vault-relative paths:

```
[[folder/file|display text]]
```

For core documentation:

```
[[.brain-core/v1.0/artefacts|Artefacts]]
```
