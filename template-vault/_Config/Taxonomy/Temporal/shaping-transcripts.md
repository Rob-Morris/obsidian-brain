# Shaping Transcripts

Temporal artefact. Q&A refinement transcripts tied to source artefacts.

## Purpose

A record of shaping sessions that refine an artefact — a design, research note, idea, plan, or anything else being refined through back-and-forth. It is both the verbatim dialogue record and the authoritative chronological mutation log for shaping decisions, work, and possible questions. A transcript may serve multiple source artefacts if shaping expands in scope.

## How to Write Shaping Transcripts

- **One file per day per artefact.** If multiple shaping sessions happen on the same artefact in one day, they share a file. The `shape` action follows the source backlink, appending to that file even if the source has been renamed. Same-title artefacts receive distinct transcripts.
- **Heading hierarchy:**
  - The filename identifies the transcript; no body `#` heading is required
  - `##` — session boundary: `## Refine session start — 14:30` (set by `shape`)
  - `###` — speaker turn (`### Agent`, `### User`) or generated audit event (`### Reconciliation R4`)
- **Both speakers treated equally.** Agent text and user text are both prose under `###` headings. No blockquotes.
- **Keep dialogue literal.** `### Agent` and `### User` contain exactly what was said. Do not add editorial synthesis inside speaker turns.
- **Record material reconciliation explicitly.** `### Reconciliation Rn` is a non-speaker, append-only audit event for semantic body propagation; decision, work, thread, and candidate-prompt additions or transitions; direct decisions made; automatic narrowing/refinement; proposed resolutions; evidence-driven completion; and later confirmation. Allocate `Rn` monotonically within the transcript: inspect existing reconciliation headings and use one greater than the highest number, including across same-day resumptions and multi-source expansion. Include source-qualified IDs, trigger, basis, affected sections, outcome and authority, confirmation state, and next action as applicable. Use concise verbs such as `Added`, `Reframed`, `Split`, `Merged`, `Deferred`, `Resolved`, `Reopened`, `Superseded`, `Retired prompt`, `Work`, and `Propagated`. A reconciliation event may follow session resumption, agent work, source expansion, or accepted review and therefore does not require an adjacent `### User` turn. Do not log formatting or copy-editing.
- **Keep historical questions immutable.** `Qn` identifies the question as it was asked in an Agent turn. Do not rewrite it when later context changes the live agenda. Record subsequent narrowing, closure, or replacement against the decisions/work it affects or as a described candidate-prompt event. One question may resolve several decisions, and several questions may inform one decision.
- **Scope IDs to the transcript.** Allocate Q, R, and review-finding C numbers monotonically across session headings and same-day resumptions. Find the next Q only from leading `Qn` labels in Agent turns, not incidental references. A new daily transcript may restart each sequence. Session headings are interaction boundaries; no identifier depends on an implicit pass namespace.
- **Keep candidate prompts ephemeral.** Record a material prompt mutation with old/new wording plus its source-qualified decision/work IDs, or a stable discovery-thread label when no D/W ID exists. Do not assign prompt IDs or rebuild a hidden prompt list by replay.
- **Separate current state from event history.** Source artefacts contain the resulting semantic body and current shaping state; this transcript contains how that state changed. Do not duplicate full reconciliation events in another temporal artefact or require state to be reconstructed by replay. A compact source-local pointer to an event is optional.
- **Link to the source.** The `**Source:**` line (set by template) lists wikilinks to all source artefacts.
- **Multi-source.** As shaping expands to touch additional artefacts, append them to the source line.
- **Joint continuation.** The source line is canonical session-scope metadata. On resumption, read every listed source without replaying events. If a source has several linked same-day transcripts, continue the one with the widest distinct declared source-link set, including archived or unresolved links; reject a tie instead of merging or copying append-only histories.

## Compatibility

This is an additive, forward-only format. Every existing dialogue-only transcript remains valid, including older `Q.` / `> A.` turns and `### Agent` / `### User` turns. Use `R1` for its first new reconciliation event and do not backfill inferred history. Existing source artefacts remain valid without shaping-state sections and are normalised lazily by the current skill when next shaped. Artefact sync updates this taxonomy but does not rewrite user artefacts or transcripts, so no version-bound user-data migration, check remediation, or repair pass is required.

## Naming

`yyyymmdd-shaping-transcript~{Title}.md` in `_Temporal/Shaping Transcripts/`.

Example: `_Temporal/Shaping Transcripts/20260307-shaping-transcript~Pistols at Dawn Discord Bot.md`

## Frontmatter

```yaml
---
type: temporal/shaping-transcript
tags:
  - transcript
---
```

## Trigger

After the shaping plan is confirmed, create a linked Brain shaping transcript
only when that plan selects Brain for transcript persistence.

## Template

[[_Config/Templates/Temporal/Shaping Transcripts]]

## See Also

[[.brain-core/standards/shaping]]
