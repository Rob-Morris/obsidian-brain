# Shaping

The [portable shaping workflow](../skills/shaping/portable.md) owns session
setup, the plan schema, workflow routing, question flow, completion, and review.
The [Brain adaptor](../skills/shaping/references/brain.md) maps a confirmed plan
onto selected Brain capabilities and is the canonical owner of Brain operation
selection. This standard defines only the Brain taxonomy, command-effect,
lifecycle, provenance, and transcript invariants used by that adaptor.

A connected Brain does not select Brain persistence. The confirmed shaping plan
may use Brain independently for the target, decisions and work, transcript, or
lifecycle, and may instead select local, scratch, or in-session handling for any
of those roles.

## Shapeable Brain artefacts

A Brain artefact type is shapeable when:

1. Its taxonomy declares `shaping` in its lifecycle.
2. Its taxonomy has a complete `## Shaping` section defining:
   - its primary flavour, `Convergent` or `Discovery`;
   - the concrete completion bar for one pass;
   - optional `Status behaviour: preserve` for a discovery type whose lifecycle
     represents an enduring domain state; and
   - for transition behaviour, the completion status to apply after approval.

Status behaviour defaults to `transition`. A preserving type leaves an
enduring non-terminal status unchanged when shaping opens and completes. It may
declare a completion status only as the approved exit for a target that was
already in `shaping`. A preserving target in a terminal status is rejected; the
user must first assign a non-terminal status explicitly.

The router compiler rejects incomplete shaping metadata, `preserve` on a
non-discovery type, or references to lifecycle statuses the taxonomy does not
declare.

## Brain command effects

`shaping.start(target="...", mode="...")` is a combined Brain session-boundary
operation. It:

1. validates the target's shaping taxonomy;
2. resolves the target by name or path;
3. creates or continues today's Brain shaping transcript and establishes
   source/backlinks; and
4. applies the taxonomy's opening lifecycle behaviour.

Those effects are deliberately coupled. The Brain adaptor is the canonical
owner of whether the confirmed plan selects that combined operation or separate
commands for mixed persistence and lifecycle roles. This standard does not
select an operation merely because Brain is available.

Creating a Brain artefact changes the indexed inventory. After
`artefact.create`, call `runtime.refresh-router` before a router-dependent
mutation. Do not use the deliberately stale pre-creation router.

Before a confirmed plan creates a Brain target or Brain transcript, verify that
the active command profile exposes both operations. `artefact.create` requires
Contributor authority and `runtime.refresh-router` requires Maintainer
authority. When refresh is unavailable, do not create first: select an existing
target or non-Brain persistence, or obtain the required authority before any
write.

For document mutations, read the current revision immediately before calling
`document.structured-edit`, `document.replace-text`, `document.write-body`, or
`document.update-frontmatter`. Use `artefact.set-status` for lifecycle changes
so folder moves, timestamps, hooks, and links remain coherent.

## Brain lifecycle outcomes

The portable workflow decides when a pass has reached its candidate exit and
obtains the required approval. Brain then applies only the lifecycle role
selected by the confirmed plan:

- **Transitioning refine or discovery:** enter `shaping` when the pass opens and
  apply the taxonomy's exact completion status only after approval.
- **Preserved discovery:** leave an enduring non-terminal status unchanged when
  the pass opens and completes. If the target began the pass already in
  `shaping`, apply its declared completion-status exit only after approval.
- **Brainstorm:** hand off to refine without applying a completion status.

A local or scratch transcript does not disable a separately selected Brain
lifecycle role, and a Brain transcript does not imply lifecycle mutation when
the plan overrides it.

## Brain decision and work persistence

When a convergent plan stores decisions and agent work in the Brain artefact,
the artefact carries separate `## Shaping Decisions` and `## Shaping Work`
tables once concrete items exist. Stable decision and work IDs remain in the
owning source artefact so its current state is resumable without replaying a
transcript.

Discovery types use natural current-state prose and a lightweight working
thread map rather than compulsory decision tables. Records selected outside
Brain follow the confirmed plan and do not acquire these Brain conventions.

## Brain transcript provenance

The Brain artefact is the source of truth for current content, decisions, and
work. A selected Brain shaping transcript is the chronological event history:
verbatim turns plus material reconciliations explaining what changed and why.
Do not create a second temporal audit record or require a future agent to replay
events to reconstruct current state.

The transcript's first body line identifies every source:

```markdown
**Source:** [[Artefact1|Title1]], [[Artefact2|Title2]]
```

Each source links back through `**Transcripts:**` according to the
[provenance standard](provenance.md). When scope expands, add the new source to
the transcript and add the backlink to that source through the normal
read-revision-mutate loop.

## Brain transcript conventions

Brain shaping transcripts follow the shaping-transcript taxonomy with these
additional invariants:

- **Naming:** `yyyymmdd-shaping-transcript~{Title}.md` under
  `_Temporal/Shaping Transcripts/`.
- **Verbatim dialogue:** `### Agent` and `### User` contain exact turns rather
  than inferred synthesis.
- **Reconciliation:** source-qualified `### Reconciliation Rn` events record
  material propagation, decision/work/thread transitions, authority, evidence,
  confirmation, and multi-source effects. Source artefacts contain the resulting
  current state, not a duplicate event history.
- **One file per day per source identity:** same-day resumption follows the
  source backlink and appends a new session heading. It does not reuse an
  unrelated same-title transcript.
- **ID scope:** question, reconciliation, and review-finding IDs are monotonic
  within the transcript across same-day session headings.

A transcript stored outside Brain follows the confirmed session plan rather
than acquiring Brain taxonomy or provenance rules automatically.

## Compatibility

Existing shapeable artefacts remain valid without shaping decision, work, or
reconciliation sections; the active portable workflow normalises selected Brain
records lazily when they are next shaped. Existing dialogue-only transcript
formats remain valid. Begin reconciliation numbering at `R1` when a new
material event occurs, and never infer or backfill historical events from old
dialogue.
