# Ideas

Living artefact. Loose thoughts, concepts, and things to explore.

## Purpose

A scratchpad for ideas that don't yet have a home elsewhere. An idea might mature into a project, a design, or a note — or it might stay here. No structure required beyond a title and tags.

## Lifecycle

| Status | Meaning |
|---|---|
| `new` | Default. The idea exists but hasn't been developed. |
| `graduated` | Promoted to a design doc. The idea is no longer actively developed here. |
| `parked` | Set aside — not abandoned, but not being pursued. |

## Graduation to Design

When an idea is ready for structured design work:

1. Create a new design doc in `Designs/`
2. Set the idea's `status: graduated`
3. Add a graduation callout to the idea body:
   ```markdown
   > [!info] Graduated to design
   > This idea has been graduated to a design doc: [[Designs/slug|Title]].
   ```
4. Add an **Origin** line to the design doc body linking back to the idea
5. Carry forward open questions from the idea as decisions in the design doc
6. Carry forward the project tag (e.g. `project/my-project`)
7. Move the idea to `Ideas/_Archive/`

## Archiving

Graduated ideas are archived automatically as part of the graduation workflow (step 7 above). The graduation callout already serves as the supersession link. Wikilinks resolve by filename — moves within the vault don't break links.

**Agent contract:** if you land on an archived idea, follow the graduation callout link to find the design doc. Do not modify archived ideas.

## Lineage

When an idea originates from an idea log, add an origin line at the top of the body:

```markdown
**Origin:** [[idea-log-slug|Source idea log]]
```

## Naming

`{slug}.md` in `Ideas/`.

Example: `Ideas/voice-controlled-task-manager.md`

## Frontmatter

```yaml
---
type: living/idea
tags:
  - idea
status: new                 # new | graduated | parked
---
```

## Template

[[_Config/Templates/Living/Ideas]]
