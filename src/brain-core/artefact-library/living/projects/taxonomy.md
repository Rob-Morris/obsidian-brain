# Projects

Living artefact. Project index files.

## Purpose

One file per project, serving as the living source of truth for the project's current state. The project file is the hub — designs, research, plans, decision logs, and other artefacts link back to it via the project tag. Updated as the project evolves; the hub reflects the current picture, not the history of how it got there.

## Naming

`{name}.md` in `Projects/`.

Example: `Projects/pistols-at-dawn.md`

## Frontmatter

```yaml
---
type: living/project
tags:
  - project/{slug}
---
```

Every file related to a project should use the nested project tag, e.g. `project/pistols-at-dawn`.

## Temporal Handshake

Research, decisions, plans, and logs tagged `project/{slug}` feed this hub. When a temporal artefact changes the project's current picture — a decision that alters scope, research that shifts direction, a plan that's been superseded — distil the change into the hub body. Temporals preserve what happened and when; the project hub reflects where things stand now.

## Ingestion

Match the effort to the input. Don't ask unnecessary questions — just create what you can and grow it later.

### Minimal input → minimal hub, no fuss

If the user gives you a project name and a sentence, create the hub immediately with what you have. Leave empty sections empty. Don't ask clarifying questions unless something is genuinely ambiguous. Look for a natural opportunity to expand later.

### Rich input → decompose into artefacts

If the user dumps a brief with lots of detail, decompose:

- **Goals, scope, current status** → the project hub body
- **Research findings** → research artefacts, tagged `project/{slug}`
- **Decisions made** → decision logs, tagged `project/{slug}`
- **Plans and next steps** → plans, tagged `project/{slug}`
- **Ideas surfaced** → idea logs, tagged `project/{slug}`
- **Timeline of what happened** → log entry

### Writing the hub

The project hub is an interpreted summary, not a raw dump. Sections should read as a concise brief on the current state: what the project is, what it's trying to achieve, where it stands, what's next.

### Contextual linking

Weave links to temporal artefacts into prose where they add depth. Don't list them as changelog entries — the link text should read naturally as part of the sentence.

### Create temporals first when decomposing

When there's rich input to decompose, spin out research, decisions, plans, and other temporals *before* writing the project hub. This ensures you have links to weave in, and forces you to separate evidence from summary.

## Template

[[_Config/Templates/Living/Projects]]
