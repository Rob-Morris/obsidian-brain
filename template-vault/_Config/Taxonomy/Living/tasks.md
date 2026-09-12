# Tasks

Living artefact. Persistent units of work — tracked, prioritised, and linked to the artefacts they serve.

## Purpose

Tasks track work that needs doing. Each task is a durable record of a deliverable — what it is, why it matters, and whether it is done. Tasks use canonical ownership when work is structurally part of a design, project, or other living artefact, keeping specifications and execution status cleanly separated.

Brain-native tasks are deliberately minimal. They don't compete with dedicated task tools (Undertask, Linear, Beads) — they're markdown files with status frontmatter and wikilinks. If you want boards, drag-and-drop, or rich nesting, use a task plugin. Brain-native tasks can serve as the sync target for external tools (see Mode 3 in the task management design).

## Lifecycle

| Status | Meaning |
|---|---|
| `open` | Default. The task exists but work hasn't started. |
| `shaping` | The task is being shaped — clarifying scope and requirements before work begins. |
| `in-progress` | Someone (human or agent) is actively working on this. |
| `done` | Completed. Terminal — move to `+Done/`. |
| `parked` | Set aside — can't proceed (dependency, question, external blocker) or chosen pause. Reason captured in a callout. Non-terminal; may resume. |
| `deprecated` | Cancelled or replaced — work won't be done here. Reason captured in a callout. Terminal — move to `+Deprecated/`. |

## Shaping

**Flavour:** Convergent
**Bar:** Clear and ready to be performed.
**Completion status:** `open`

See [[.brain-core/standards/shaping]] for the shaping process.

## Claiming Tasks

When a task moves to `in-progress`, set `assigned` (who's working on it) and `claimed_at` (ISO timestamp). If a claim goes stale (configurable TTL), the task is available for reclaim. This prevents abandoned work from blocking progress in multi-agent workflows.

## Kind

Optional classification of the work:

| Kind | Meaning |
|---|---|
| `bug` | Broken behaviour that needs fixing. |
| `feature` | New capability or deliverable. |
| `chore` | Maintenance, cleanup, or infrastructure. |
| `spike` | Timeboxed investigation or research. |
| `decision` | Resolve a design question and commit to a choice. |

## Priority

Optional named priority levels: `critical`, `high`, `medium`, `low`.

## Ownership and Grouping

Set `parent` when a task is a structural part of the artefact it delivers. Use ordinary links and tags when the relationship is contextual rather than ownership. An optional parent task can group a substantial workstream, but an extra board file is not required merely to connect tasks to another artefact.

### Subtasks

When a task needs decomposition, make each subtask canonically owned by that task:

```
Tasks/
  design~tooling-architecture/
    Obsidian CLI Rewrite.md
    obsidian-cli-rewrite/
      Binary Detection.md
      Availability Probing.md
```

## Terminal Status

When a task reaches a terminal status (`done` or `deprecated`):

- **Done:** set `status: done`; the lifecycle handler moves the task to the `+Done/` folder within its ownership location.
- **Deprecated:** set `status: deprecated`, add a reason callout, and let the lifecycle handler move the task to the `+Deprecated/` folder within its ownership location:
  ```markdown
  > [!info] Deprecated — cancelled: scope absorbed into [[link|task or design]]
  > [!info] Deprecated — replaced by [[link|new task]]
  > [!info] Deprecated — abandoned: no longer relevant
  ```

Terminal tasks remain searchable and indexed in their `+Status` folder. No rename, no `archiveddate`.

**Agent contract:** if you land on a terminal task, it's no longer active. Do not reopen — create a new task if follow-up work is needed.

## Parked Tasks

`parked` is the non-terminal pause state. Use it when work can't proceed (waiting on an external dependency, blocked on a decision, or chosen pause). Capture the blocker in a `> [!info] Parked — <reason>` callout or short prose note in the body so it's clear what unblocks the task. When work can resume, move the status back to `open` or `in-progress`.

## Naming

`{Title}.md` in `Tasks/`.

## Frontmatter

```yaml
---
type: living/task
key: {key}
tags:
  - task
status: open                 # open | shaping | in-progress | done | parked | deprecated
---
```

**Optional:** `status`

Optional fields:

```yaml
kind: feature                # bug | feature | chore | spike | decision
priority: medium             # critical | high | medium | low
assigned: claude             # freeform — agent name, human name
claimed_at: 2026-03-30T14:00:00+11:00  # ISO timestamp, set on claim
```

## Template

[[_Config/Templates/Living/Tasks]]
