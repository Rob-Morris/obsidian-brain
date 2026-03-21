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
3. Add `archiveddate: YYYY-MM-DD` to the idea's frontmatter
4. Rename the idea to `yyyymmdd-{slug}.md` using `brain_action("rename")` — updates all wikilinks automatically
5. Add a graduation callout to the idea body:
   ```markdown
   > [!info] Graduated to design
   > This idea has been graduated to a design doc: [[Designs/slug|Title]].
   ```
6. Add an **Origin** line to the design doc body linking back to the idea
7. Carry forward open questions from the idea as decisions in the design doc
8. Carry forward the project tag (e.g. `project/my-project`)
9. Move the idea to `Ideas/_Archive/`

## Archiving

Graduated ideas are archived automatically as part of the graduation workflow (steps 3–4 and 9 above). The graduation callout serves as the supersession link. `archiveddate` and the date-prefixed filename are set during graduation. `brain_action("rename")` handles wikilink updates automatically.

**Agent contract:** if you land on an archived idea, follow the graduation callout link to find the design doc. Do not modify archived ideas.

## Lineage

When an idea originates from an idea log, add an origin line at the top of the body:

```markdown
**Origin:** [[idea-log-slug|Source idea log]]
```

## Naming

`{name}.md` in `Ideas/`.

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
