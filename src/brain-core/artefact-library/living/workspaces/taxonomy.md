# Workspaces

Living artefact. Workspace hub files.

## Purpose

One file per workspace, linking to all related artefacts across the vault. The workspace hub connects brain artefacts (research, decisions, designs) to a bounded container of working files (`_Workspaces/`) that fall outside the vault's artefact taxonomy. Follows the hub pattern — the tag is the query mechanism, the hub is the index.

Workspaces solve the problem of work that involves non-markdown files (CSVs, JSON, API dumps, spreadsheets), pipelines (raw data, processed output), and scratch material that only makes sense within the scope of that work.

## When To Use

When connecting vault artefacts to an external bounded container of working files — repos, data folders, pipelines, or scratch material that falls outside the vault's artefact taxonomy.

## Naming

`{Title}.md` in `Workspaces/`.

Example: `Workspaces/yearly-taxes-2026.md`

## Frontmatter

```yaml
---
type: living/workspace
key: {key}
tags:
  - workspace/{key}
status: active
workspace_mode: embedded
---
```

`key` is the canonical identifier (see [[.brain-core/standards/keys]]). The platform generates it at create time.

Every file related to a workspace should use the nested workspace tag, e.g. `workspace/yearly-taxes-2026`.

`workspace_mode` is `embedded` (data in `_Workspaces/`) or `linked` (data in an external folder connected via `.brain/local/workspaces.json`).

## Lifecycle

| Status | Meaning |
|---|---|
| `active` | Default. Workspace is in use. |
| `parked` | Set aside temporarily. Non-terminal; may resume. |
| `completed` | Work is done. Terminal — move to `+Completed/`. |
| `deprecated` | Abandoned, replaced, or no longer relevant. Reason captured in a callout. Terminal — move to `+Deprecated/`. |

## Terminal Status

When a workspace reaches a terminal status (`completed` or `deprecated`), move the hub file to the corresponding `+Status` folder:

- **Completed:** set `status: completed`, move to `Workspaces/+Completed/`.
- **Deprecated:** set `status: deprecated`, add a reason callout, move to `Workspaces/+Deprecated/`:
  ```markdown
  > [!info] Deprecated — superseded by [[link|new workspace]]
  > [!info] Deprecated — abandoned: work was not pursued
  ```

No rename, no `archiveddate` — terminal hubs stay searchable and indexed in their `+Status` folder.

The embedded data folder at `_Workspaces/{key}/` does **not** move regardless of hub status. The data bucket sits outside the artefact taxonomy (see [[#Data Folder]]), so its layout is independent of hub status.

## Data Folder

The `_Workspaces/{key}/` folder (for embedded mode) is a freeform data bucket. Any file type is welcome — markdown, CSVs, JSON, scripts, images. These files are **not** brain artefacts: no frontmatter obligations, no naming conventions, no taxonomy rules. The brain does not index or enforce conventions inside `_Workspaces/`.

If a user needs a full artefact taxonomy for a body of work, the answer is to create a separate brain (an independent vault with its own `.brain-core/`).

## Template

[[_Config/Templates/Living/Workspaces]]
