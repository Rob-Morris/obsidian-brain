# Projects

Living artefact. Project index files.

## Purpose

One file per project, serving as the living source of truth for the project's current state. The project file is the hub — designs, research, plans, decision logs, and other artefacts link back to it via the project tag. Updated as the project evolves; the hub reflects the current picture, not the history of how it got there.

## When To Use

When starting a new project that will generate multiple artefacts across the vault. The project file is the hub — create it early so other artefacts can link back via the project tag.

## Naming

`{Title}.md` in `Projects/`.

Example: `Projects/pistols-at-dawn.md`

## Frontmatter

```yaml
---
type: living/project
key: {key}
tags:
  - project/{key}
---
```

`key` is the canonical identifier (see [[.brain-core/standards/keys]]). The platform generates it at create time; edit it manually only when a memorable key is genuinely needed.

Files related to a project can use the relationship tag `project/pistols-at-dawn`. Child artefacts that are structurally owned by the project should also set `parent: project/pistols-at-dawn`. Living children use same-type `{key}/` folders or cross-type `{scope}/` folders; temporal children stay in their date folders.

## Temporal Handshake

Research, decisions, plans, and logs tagged `project/{key}` feed this hub. When a temporal artefact changes the project's current picture — a decision that alters scope, research that shifts direction, a plan that's been superseded — distil the change into the hub body. Temporals preserve what happened and when; the project hub reflects where things stand now.

## Ingestion

Match the effort to the input. Don't ask unnecessary questions — just create what you can and grow it later.

### Minimal input → minimal hub, no fuss

If the user gives you a project name and a sentence, create the hub immediately with what you have. Leave empty sections empty. Don't ask clarifying questions unless something is genuinely ambiguous. Look for a natural opportunity to expand later.

### Rich input → decompose into artefacts

If the user dumps a brief with lots of detail, decompose:

- **Goals, scope, current status** → the project hub body
- **Research findings** → research artefacts, tagged `project/{key}`
- **Decisions made** → decision logs, tagged `project/{key}`
- **Plans and next steps** → plans, tagged `project/{key}`
- **Ideas surfaced** → idea logs, tagged `project/{key}`
- **Timeline of what happened** → log entry

### Writing the hub

The project hub is an interpreted summary, not a raw dump. Sections should read as a concise brief on the current state: what the project is, what it's trying to achieve, where it stands, what's next.

### Contextual linking

Weave links to temporal artefacts into prose where they add depth. Don't list them as changelog entries — the link text should read naturally as part of the sentence.

### Create temporals first when decomposing

When there's rich input to decompose, spin out research, decisions, plans, and other temporals *before* writing the project hub. This ensures you have links to weave in, and forces you to separate evidence from summary.

## Template

[[_Config/Templates/Living/Projects]]
