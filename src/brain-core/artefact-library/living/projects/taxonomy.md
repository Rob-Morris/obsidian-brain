# Projects

Living artefact. Project index files.

## Purpose

One file per project, linking to all related artefacts across the vault. The project file is the hub — designs, research, plans, and other artefacts link back to it via the project tag.

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

## Template

[[_Config/Templates/Living/Projects]]
