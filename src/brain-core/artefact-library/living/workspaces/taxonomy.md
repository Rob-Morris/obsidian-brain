# Workspaces

Living artefact. Workspace hub files.

## Purpose

One durable identity per workspace, connecting Brain artefacts to bounded working files. Canonical membership is the artefact's `workspace: workspace/{key}` field, never a relationship tag or filesystem location. The workspace hub is self-scoped even when its own `workspace` field is omitted.

Workspaces solve the problem of work that involves non-markdown files (CSVs, JSON, API dumps, spreadsheets), pipelines (raw data, processed output), and scratch material that only makes sense within the scope of that work.

## When To Use

When connecting vault artefacts to an external bounded container of working files — repos, data folders, pipelines, or scratch material that falls outside the vault's artefact taxonomy.

## Naming

`{Title}.md` in `Workspaces/`.

Example: `Workspaces/Yearly Taxes 2026.md`

## Frontmatter

```yaml
---
type: living/workspace
key: {key}
tags:
  - workspace/{key}
status: active
workspace_mode: embedded
default_parent: project/yearly-taxes-2026
default_tags: []
---
```

**Optional:** `status`, `workspace_mode`, `default_parent`, `default_tags`

`key` is the canonical identifier (see [[.brain-core/standards/keys]]). The platform generates it at create time.

Relationship tags such as `workspace/yearly-taxes-2026` remain useful for queries but do not assign membership. Adopt existing content explicitly with `artefact.set-workspace`; use recursive intent when it owns descendants. Ownership edges must stay wholly within one workspace or wholly unscoped.

`default_parent` is a canonical non-terminal living artefact in this workspace. It supplies the create parent after any explicit or applicable local override. `default_tags` is an additive list applied to surviving semantic mutation subjects, not incidental link rewrites or maintenance. Change shared policy with `workspace.update-policy`, not generic frontmatter editing.

Caller-local `.brain/local/workspace.yaml` stores the bare workspace key inside `links` and may supply a `parent` and additive `tags` inside `defaults`. Local overrides apply only when selecting that bound workspace; selecting another workspace uses its shared policy. Symbolic `workspace_context` selects a canonical workspace or `global`; filesystem paths are never semantic selectors.

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

- **Completed:** set `status: completed`; the lifecycle handler moves the hub to the `+Completed/` folder within its ownership location.
- **Deprecated:** set `status: deprecated`, add a reason callout, and let the lifecycle handler move the hub to the `+Deprecated/` folder within its ownership location:
  ```markdown
  > [!info] Deprecated — superseded by [[link|new workspace]]
  > [!info] Deprecated — abandoned: work was not pursued
  ```

No rename, no `archiveddate` — terminal hubs stay searchable and indexed in their `+Status` folder.

Terminal hubs remain valid historical membership identities, but cannot be selected for new scoped mutation. Archive/delete/key changes are refused while discoverable members, shared policy, or the active local binding refer to the hub. Ordinary type conversion cannot preserve hub self-scope and is refused. Configured default parents cannot be archived, deleted, made terminal, converted out of living classification, reassigned away, or rekeyed until policy is explicitly changed.

The embedded data folder at `_Workspaces/{key}/` does **not** move regardless of hub status. The data bucket sits outside the artefact taxonomy (see [[#Data Folder]]), so its layout is independent of hub status.

## Data Folder

The `_Workspaces/{key}/` folder (for embedded mode) is a freeform data bucket. Any file type is welcome — markdown, CSVs, JSON, scripts, images. These files are **not** brain artefacts: no frontmatter obligations, no naming conventions, no taxonomy rules. The brain does not index or enforce conventions inside `_Workspaces/`.

If a user needs a full artefact taxonomy for a body of work, the answer is to create a separate brain (an independent vault with its own `.brain-core/`).

## Template

[[_Config/Templates/Living/Workspaces]]
