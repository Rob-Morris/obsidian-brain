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
| `blocked` | Can't proceed — dependency, question, or external blocker. |

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

When a task reaches `done` status:

- Set `status: done`
- Move to `Tasks/+Done/` (or the parent subfolder's `+Done/`)

Done tasks remain searchable and indexed in the `+Done/` folder. No rename, no `archiveddate`.

**Agent contract:** if you land on a done task, it's completed work. Do not reopen done tasks — create a new task if follow-up work is needed.

## Naming

`{Title}.md` in `Tasks/`.

Board tasks: `{ParentType}~{Name}.md` (e.g. `Design~Brain Inbox.md`).

## Frontmatter

```yaml
---
type: living/task
tags:
  - task
status: open                 # open | shaping | in-progress | done | blocked
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
