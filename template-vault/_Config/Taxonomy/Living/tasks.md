# Tasks

Living artefact. Persistent units of work — tracked, prioritised, and linked to the artefacts they serve.

## Purpose

Tasks track work that needs doing. Each task is a durable record of a deliverable — what it is, why it matters, and whether it's done. Tasks link to designs, projects, and other artefacts via the board-per-artefact pattern, keeping specs and status cleanly separated.

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
**Completion status:** The type's normal working status (e.g. `open`)

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

## Board-per-Artefact Pattern

Each artefact with associated work gets a **board** — a parent task that connects the task system to the artefact graph.

```
Tasks/
  Design~Tooling Architecture.md           (board — links to design)
  Design~Tooling Architecture/             (child tasks)
    Brain CLI Wrapper.md
    Capability Registry.md
    Obsidian CLI Rewrite.md
```

The board file uses the `ParentType~Name` naming convention. It wikilinks up to the design and carries the relevant tag. Individual child tasks inherit context from the board — they don't need to link back or tag themselves.

To find tasks for a design: follow backlinks from the board, query the tag, or use MCP search.

### Standalone tasks

Tasks not tied to any artefact sit at the root of `Tasks/`.

### Subtask nesting

Tasks that need decomposition get their own subfolder in the same recursive pattern:

```
Tasks/
  Design~Tooling Architecture/
    Obsidian CLI Rewrite.md
    Obsidian CLI Rewrite/              (subtasks)
      Binary Detection.md
      Availability Probing.md
```

## Terminal Status

When a task reaches a terminal status (`done` or `deprecated`):

- **Done:** set `status: done`, move to `Tasks/+Done/` (or the parent subfolder's `+Done/`).
- **Deprecated:** set `status: deprecated`, add a reason callout, move to `Tasks/+Deprecated/` (or the parent subfolder's `+Deprecated/`):
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

Board tasks: `{ParentType}~{Name}.md` (e.g. `Design~Brain Inbox.md`).

## Frontmatter

```yaml
---
type: living/task
tags:
  - task
status: open                 # open | shaping | in-progress | done | parked | deprecated
---
```

Optional fields:

```yaml
kind: feature                # bug | feature | chore | spike | decision
priority: medium             # critical | high | medium | low
assigned: claude             # freeform — agent name, human name
claimed_at: 2026-03-30T14:00:00+11:00  # ISO timestamp, set on claim
```

## Template

[[_Config/Templates/Living/Tasks]]
