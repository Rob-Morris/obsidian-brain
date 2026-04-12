# Artefact Provenance

When an artefact spins out of another (e.g. an idea graduating to a design, or a log entry becoming a standalone note), record the lineage in both files:

**On the new artefact:**
- Add `**Origin:** [[source-file|description]] (yyyy-mm-dd)` in the body

**On the source artefact:**
- Add a callout **at the top of the body** (after frontmatter, before other content) so agents and humans hit it immediately:

```markdown
> [!info] Spun out to {type}
> [[new-file]] — yyyy-mm-dd
```

**When the source will be archived:** the source file gets renamed (e.g. `brain-workspaces` → `20260324-brain-workspaces`) and may share its original slug with the successor. To avoid ambiguity, use the post-rename identifier in the origin link and path-qualify the supersession callout. See the [archiving standard](archiving.md) for details.

**Terminal status:** If the source artefact has transferred all authority to the new one, set the terminal status in frontmatter and archive if the type supports it (see the [archiving standard](archiving.md)). Otherwise the callout alone suffices — the source remains active.

Individual artefact taxonomy files may document specific provenance patterns (e.g. idea graduation, log spinout) with their own terminology, but the underlying mechanism is always this: origin link on the child, callout on the parent.

## Transcript linking

When an artefact is shaped through Q&A (producing a shaping transcript), link bidirectionally:

**On the shaping transcript** (already required by the shaping-transcript taxonomy):
- First line after frontmatter: `Shaping transcript for [[Source/Path|Source Title]].`
- Multi-source transcripts list all sources: `Shaping transcript for [[Source1|Title1]], [[Source2|Title2]].`

**On the shaped artefact:**
- Add a `**Transcripts:**` line in the body listing all shaping sessions:
  ```markdown
  **Transcripts:** [[transcript-1|Session 1]], [[transcript-2|Session 2]]
  ```

This applies to any artefact type that gets shaped — designs, ideas, research, wiki, or anything else. The transcript list grows as new sessions occur.

## Adoption (idea → design)

When an idea is adopted into a downstream artefact (design, project, etc.), this is a provenance event — not a status-driven transition. Record the lineage using the standard spinout pattern above (origin link on the new artefact, callout on the idea). The idea's status changes to `adopted` as a consequence of the provenance event, but provenance may accompany a status change without prescribing it.
