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

The double-dash `--` separates structural prefixes from the slug.

## Month Folders

All temporal content is grouped into `yyyy-mm/` month folders. Files sit flat in their month folder — no subfolders within months.

Example: `_Temporal/Logs/2026-03/log--2026-03-10.md`

## Frontmatter

Every artefact file (living and temporal) has YAML frontmatter with at least `type` and `tags`. See [[.brain-core/v1.0/artefacts]] for the full specification.

## Wikilinks

Use Obsidian wikilinks with vault-relative paths:

```
[[folder/file|display text]]
```

In artefact content, labels are fine for readability. In config and core documentation files (`_Config/`, `.brain-core/`), always use bare paths — no `|label` aliases — so agents see exactly where each link points:

```
[[.brain-core/v1.0/artefacts]]
```
