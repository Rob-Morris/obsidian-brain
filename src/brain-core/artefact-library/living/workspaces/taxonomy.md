# Workspaces

Living artefact. Workspace hub files.

## Purpose

One file per workspace, linking to all related artefacts across the vault. The workspace hub connects brain artefacts (research, decisions, designs) to a bounded container of working files (`_Workspaces/`) that fall outside the vault's artefact taxonomy. Follows the hub pattern — the tag is the query mechanism, the hub is the index.

Workspaces solve the problem of work that involves non-markdown files (CSVs, JSON, API dumps, spreadsheets), pipelines (raw data, processed output), and scratch material that only makes sense within the scope of that work.

## Naming

`{Title}.md` in `Workspaces/`.

Example: `Workspaces/yearly-taxes-2026.md`

## Frontmatter

```yaml
---
type: living/workspace
tags:
  - workspace/{slug}
status: active
workspace_mode: embedded
---
```

Every file related to a workspace should use the nested workspace tag, e.g. `workspace/yearly-taxes-2026`.

`workspace_mode` is `embedded` (data in `_Workspaces/`) or `linked` (data in an external folder connected via `.brain/local/workspaces.json`).

## Lifecycle

| Status | Meaning |
|---|---|
| `active` | Default. Workspace is in use. |
| `paused` | Set aside temporarily. |
| `completed` | Work is done. Terminal — archive. |
| `archived` | Preserved for reference. Terminal — archive. |

## Archiving

When a workspace reaches `completed` status or `archived` status → add `archiveddate` → rename to `yyyymmdd-{slug}.md` → move to `Workspaces/_Archive/`. If an embedded data folder exists at `_Workspaces/{slug}/`, move it to `_Workspaces/_Archive/{slug}/`.

## Data Folder

The `_Workspaces/{slug}/` folder (for embedded mode) is a freeform data bucket. Any file type is welcome — markdown, CSVs, JSON, scripts, images. These files are **not** brain artefacts: no frontmatter obligations, no naming conventions, no taxonomy rules. The brain does not index or enforce conventions inside `_Workspaces/`.

If a user needs a full artefact taxonomy for a body of work, the answer is to create a separate brain (an independent vault with its own `.brain-core/`).

## Template

[[_Config/Templates/Living/Workspaces]]
