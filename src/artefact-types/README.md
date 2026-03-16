# Artefact Type Library

Complete, ready-to-install artefact type definitions for Brain vaults.

The template vault ships with a minimal set of types (wiki, logs, plans, transcripts). This library contains all known types — including the template vault defaults — as a reference and install source.

## Available Types

### Living

| Type | Key | Description |
|---|---|---|
| [Wiki](living/wiki/) | `wiki` | Interconnected knowledge base. One page per concept. **Template vault default.** |
| [Daily Notes](living/daily-notes/) | `daily-notes` | High-level end-of-day summaries distilled from logs. |
| [Designs](living/designs/) | `designs` | Design documents, wireframes, and specs. |
| [Documentation](living/documentation/) | `documentation` | Guides, standards, and reference material. |
| [Ideas](living/ideas/) | `ideas` | Loose thoughts and concepts to explore. |
| [Notes](living/notes/) | `notes` | Flat knowledge base of date-prefixed interconnected notes. |
| [Projects](living/projects/) | `projects` | Project index files linking to related artefacts via project tags. |

### Temporal

| Type | Key | Description |
|---|---|---|
| [Logs](temporal/logs/) | `logs` | Append-only daily activity logs. **Template vault default.** |
| [Plans](temporal/plans/) | `plans` | Pre-work plans before complex work begins. **Template vault default.** |
| [Transcripts](temporal/transcripts/) | `transcripts` | Conversation transcripts. **Template vault default.** |
| [Design Transcripts](temporal/design-transcripts/) | `design-transcripts` | Q&A refinement transcripts tied to a source artefact. |
| [Idea Logs](temporal/idea-logs/) | `idea-logs` | Quick idea captures with graduation path to ideas then designs. |
| [Research](temporal/research/) | `research` | In-depth research notes on specific topics. |

## Structure

Each type lives in its own directory under `living/` or `temporal/`:

```
artefact-types/
├── living/
│   └── {type-key}/
│       ├── README.md          # What it is, when to use it, install paths
│       ├── taxonomy.md        # → _Config/Taxonomy/{Classification}/{key}.md
│       └── template.md        # → _Config/Templates/{Classification}/{Type Name}.md
└── temporal/
    └── {type-key}/
        ├── README.md
        ├── taxonomy.md
        └── template.md
```

## Installing a type

1. Copy `taxonomy.md` to `_Config/Taxonomy/{Living|Temporal}/{key}.md`
2. Copy `template.md` to `_Config/Templates/{Living|Temporal}/{Type Name}.md`
3. Create the storage folder (e.g. `_Temporal/{Type Name}/` or `{Type Name}/`)
4. Optionally add a conditional trigger to `_Config/router.md`

Each type's README includes the specific paths and an optional router trigger line.
