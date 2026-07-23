# DD-057: Separate the shaping activity from its session-boundary primitive

**Status:** Implemented (v0.53.0)
**Extends:** DD-024, DD-045, DD-056

## Context

The public `start-shaping` action both commenced and continued shaping sessions. Its name described only the first transition, while its implementation accepted presentation concerns such as a transcript title and skill selector. The shaping skills separately chose the conversational workflow, but their routing depended on hard-coded type lists and repeated lifecycle writes that could bypass the invariant-preserving handlers established by DD-056.

Transcript identity was also filename-led. Renaming a source could mint another same-day transcript, while two distinct artefacts with the same title could accidentally share one. Missing templates and late write failures could leave a status transition without enough repair context.

## Decision

Use `shape` as the public `brain_action` verb. It means open or continue the shaping activity for a target artefact and accepts only the target plus the conversational mode already selected by the skill.

Keep the skill and action boundaries explicit:

- The shaping skill resolves or creates the artefact, reads its taxonomy contract, chooses `brainstorm`, `refine`, or `discover`, conducts Q&A, reviews against the bar, and applies the declared completion status.
- The internal `start_shaping_session()` primitive validates the compiled shapeability contract, transitions to `shaping` through the canonical lifecycle handler, creates or appends the transcript, maintains reciprocal provenance links, and returns its exact durable change set.

Compile a complete taxonomy `## Shaping` section into structured `flavour`, `bar`, and `completion_status` metadata, and reject shaping values absent from the taxonomy's explicit lifecycle. This metadata, rather than a hard-coded type list, determines whether and how the skill routes an artefact.

Treat reciprocal links as the durable source/transcript identity, resolving both full-path and unambiguous basename wikilinks. Continue a same-day transcript linked to that source even after the source moves or is renamed. Ignore stale or non-reciprocal historical backlinks; if a filename candidate belongs to a different source, create a disambiguated transcript rather than appending to it.

Resolve the transcript folder, filename, date source, frontmatter type, and
template from the compiled `temporal/shaping-transcript` taxonomy. The session
primitive owns no parallel naming convention.

Retain `start_shaping.py` only as a direct-script compatibility launcher. The old MCP action discriminator is removed because the public contract is pre-1.0 and internal callers ship with the same release.

## Alternatives Considered

### Keep `start-shaping`

Rejected because continuation is normal behaviour, not an edge case. A start-only verb makes a low-level caller appear to request a new session even when the invariant is to resume today's existing one.

### Call both layers `shape`

Rejected because the implementation primitive does not perform the full activity. Naming it `shape()` would imply ownership of conversational routing, Q&A, review, and completion that remain deliberately agent-level judgements.

### Put transcript and lifecycle mechanics in the skill

Rejected because those are deterministic integrity operations. Keeping them in prose would duplicate resolution, lifecycle, linking, and partial-apply behaviour across skills and clients.

## Consequences

- Callers use one coherent action for both commencement and continuation.
- The internal function name accurately identifies the narrower boundary it owns.
- New shapeable types opt in through taxonomy metadata without editing skill routing lists.
- Lifecycle hooks, terminal-folder revival, index dirtying, and partial-apply reporting use their canonical seams.
- External MCP callers using `start-shaping`, `title`, or `skill_type` must migrate to `shape`, `target`, and a required `mode`.
- Direct script callers retain a compatibility path while the legacy surface is phased out.

## Implementation Notes

- `compile_router.py` rejects incomplete shaping sections, validates flavour values, and requires `shaping` plus the completion status in the declared lifecycle. Its stable compatibility error code lets the v0.53 pre-compile migration repair only that condition before the strict compiler runs.
- The low-level primitive preflights shapeability and the router-declared shaping-transcript naming/template contract before changing lifecycle state.
- MCP queues only returned `changed_paths`; it marks router/index state dirty when lifecycle movement or partial application requires broader refresh.
- Contract tests guard the action schema, taxonomy-driven skill routing, completion-status handling, transcript identity, and compatibility launcher.
