# Markdown Bootstrap

Explicit degraded fallback for environments without MCP or a generated
`.brain/local/session.md` mirror. Everything routed from here is plain markdown
shipped in the vault — no scripts required.

## Read First

1. `.brain-core/session-core.md` — the artefact model, system principles, and the standards index
2. `_Config/User/preferences-always.md` — the vault owner's standing instructions
3. `_Config/User/gotchas.md` — learned lessons and known pitfalls

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

## Tooling

Relevant only where code can run. Both routes share the same typed request,
semantic owner, structural result, profile gate, and vault mutation lock.

- `brain <noun> <verb> --request-json '<object>' --json` — preferred. The launcher enters the vault's managed runtime, so managed-tier commands such as `session.start` are available.
- `brain command list --owner all --json` — discover the composed local catalogue.
- `python3 .brain-core/scripts/command.py <noun> <verb> --request-json '<object>' --json` — direct projection. Runs at whatever tier the calling interpreter provides, so managed-tier commands are unavailable outside the managed runtime.
- `python3 .brain-core/scripts/command.py command list --request-json '{}' --json` — discover the installed selected-Brain commands.
