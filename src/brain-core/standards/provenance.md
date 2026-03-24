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

**When the source will be archived:** the source file gets renamed (e.g. `brain-workspaces` → `20260324-brain-workspaces`) and may share its original slug with the successor. To avoid ambiguity, use the post-rename identifier in the origin link and path-qualify the supersession callout. See [[.brain-core/standards/archiving]] for details.

**Terminal status:** If the source artefact has transferred all authority to the new one, set the terminal status in frontmatter and archive if the type supports it (see [[.brain-core/standards/archiving]]). Otherwise the callout alone suffices — the source remains active.

Individual artefact taxonomy files may document specific provenance patterns (e.g. idea graduation, log spinout) with their own terminology, but the underlying mechanism is always this: origin link on the child, callout on the parent.
