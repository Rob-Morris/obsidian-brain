# Markdown Bootstrap

Explicit authored Markdown fallback for vault-local agents that cannot use MCP,
CLI or scripts, cannot run code or compile anything, and have no generated
assets such as `.brain/local/session.md`. Everything needed here is in the copied
`.brain-core/` instructions and authored vault files.

## Read First

1. `.brain-core/session-core.md` — the artefact model, system principles, and the standards index
2. `_Config/router.md` — vault-specific rules, workflow triggers and configuration links
3. `_Config/User/preferences-always.md` — the vault owner's standing instructions
4. `_Config/User/gotchas.md` — learned lessons and known pitfalls

## Then Route By Need

| Need | Read |
|---|---|
| Which types this vault has | `_Config/Taxonomy/Living/` and `_Config/Taxonomy/Temporal/` — one file per installed type |
| How to author one type | that type's file — it carries purpose, naming, folder, frontmatter, lifecycle, and template |
| When to create what | `_Config/router.md` — the workflow trigger table |
| Procedures this vault defines | `_Config/Skills/` — one folder per skill, each with a `SKILL.md` |
| Naming, keys, wikilinks, provenance, archiving | `.brain-core/standards/README.md` |
| A worked overview of day-to-day use | `.brain-core/guide.md` |

`_Config/Taxonomy/` is authoritative for this vault: read the type's file before
creating an artefact of that type, and reproduce every field its frontmatter
example declares. Values shown in braces are placeholders — substitute a real
value and never write the braces into an artefact. Every living artefact
requires a `key`. Every artefact of any type also carries `created` and
`modified` as ISO 8601 timestamps; tooling reconciles these on write, so set them
yourself when authoring by hand. Beyond these entry points, navigate by wikilink
from the router and the type files.

## When Tools Become Available

Return to `.brain-core/index.md` for the tool-backed bootstrap routes and finish
the canonical session before ordinary tool use. Its authorisation instructions
apply to tool-backed work; reading this fallback grants no additional authority.
