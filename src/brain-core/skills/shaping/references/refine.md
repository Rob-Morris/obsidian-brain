# Refine workflow

Use refine for a formed convergent artefact. Work through the live decision and agent-work agenda until the artefact meets its taxonomy bar.

## Contents

- Set the agenda
- Reconciliation triggers
- Convergent shaping loop
- Decision authority
- Ending the current pass
- Red flags

If the parent or brainstorm workflow has not supplied an artefact path and transcript path, return to the root workflow and run `references/assess.md` first.

## Set the agenda

Review the artefact's open questions or decision table. Skip what is already resolved. On resumption, treat the artefact rather than old transcripts as the source of truth. The transcript explains how that current state was reached; do not replay it to rebuild the artefact.

Create or normalise these current-state sections when the artefact lacks an equivalent structure:

```markdown
## Shaping Decisions

| ID | Decision | State | Outcome / current context | Depends on |
|---|---|---|---|---|

## Shaping Work

| ID | Work | State | Evidence / result | Affects |
|---|---|---|---|---|
```

Use decision states `user-decision`, `proposed`, `deferred`, `blocked`, `resolved`, and `superseded`. Use work states `pending`, `in-progress`, `blocked`, and `completed`. Preserve information from an existing open-decisions table when normalising it.

Classify the agenda before asking anything:

- **User decision:** At least two genuinely viable outcomes remain, the difference materially affects the artefact, accepted constraints do not select a winner, and user judgement is the missing input.
- **Proposed resolution:** An earlier answer, accepted constraint, or strong evidence leaves a clear winner, but the user has not confirmed that derived conclusion.
- **Agent work:** Research, inventory, specification, consistency repair, or verification can determine the answer. Do the work; do not turn it into a preference question.
- **Deferred:** The user postponed it or a dependency must be resolved first.
- **Resolved or superseded:** Preserve the stable decision ID and outcome, but remove it from the active question agenda.

Decision IDs are stable within their owning source artefact. In cross-source events qualify them as `{artefact-key}:D4`; qualify work IDs similarly. Narrowing keeps the ID. A merge names one surviving ID and marks the others `superseded` with a pointer to it. A split keeps the parent as a superseded lineage record and creates child IDs such as `D4a` and `D4b`.

For a living source, `{artefact-key}` means its immutable canonical `{type}/{key}`. For a temporal source, use its source wikilink/path as recorded on the transcript's `**Source:**` line. This keeps cross-source references distinct and rename-safe under Brain's normal wikilink updates.

Asked-question IDs are immutable, transcript-scoped historical turn identifiers allocated during assessment. Once `Q3` has been asked, never rewrite it to mean a narrower question. Record how its answer affects decisions, work, and later prompts in reconciliation events. Before a prompt is asked, treat it as an ephemeral candidate prompt: narrow, merge, split, or retire it using old/new wording and the decision/work IDs it would address. Discovery uses a stable thread label when no D/W ID exists. Do not allocate prompt IDs or reconstruct candidate prompts on resumption; derive the next useful prompt from current source state. One question may resolve several decisions, and several questions may inform one decision.

Keep user decisions separate from agent work. The agenda is not a fixed questionnaire.

## Cold opening

Treat a new or resumed session as an orientation boundary, not as permission to batch the reconstructed agenda into one user turn.

- Apply lossless table normalisation, direct consequences, factual propagation, and narrowing that only removes impossible scope. Record and report these established changes without asking whether to reconcile them.
- Do not offer several inferred resolutions for bulk confirmation on the opening turn. Inventory their count at a high level, then present only the highest-priority proposal or genuine user decision in decision-ready form.
- For a proposed resolution, restate the uncertainty and why it matters, explain the accepted constraint or evidence that creates a winner, name the recommended outcome and its material consequence or trade-off, then ask for one commitment. If the explanation is not enough, offer the genuinely viable alternatives as multiple choice with a recommendation.
- Preserve the useful assessment report from `references/assess.md`, but keep it concise enough that the first material choice remains easy to evaluate.

## Reconciliation triggers

Run reconciliation whenever authoritative shaping state changes:

- session start or resumption;
- user answer, correction, or volunteered decision;
- research or agent-work result;
- source artefact addition or scope change;
- accepted four-Cs review finding.

Reconcile before asking another question, reporting progress, handing off, or claiming a candidate exit. A user statement that explicitly supplies an outcome is a direct answer even when the active question did not reference that decision. An outcome merely implied by the statement is derived and remains proposed.

## Convergent shaping loop

1. **Apply the trigger.** Update every affected section of every in-scope source artefact, including prose, examples, constraints, acceptance criteria, and tables. A direct, unambiguous user statement closes every decision it explicitly answers without duplicate confirmation. Apply an answer that introduces a new source to that source immediately.
2. **Run the impact sweep to a fixed point.** Re-read all remaining decisions and work. Narrow or reformulate stale wording, identify merges or splits, expose new items, and propose clear derived resolutions or conversions to agent work. Repeat until the trigger produces no further consequence; do not create checkpoint cascades.
3. **Do evidence work before asking.** Complete reasonably bounded shaping research, inventory, specification, or verification that could eliminate, narrow, or materially reframe a user decision, then reconcile its result. Agent work defines the artefact; it is not implementation. If the work is expensive, requires authority, or is blocked, offer a research spike or record it as blocked.
4. **Apply only established changes and persist proposals.** Apply factual propagation and narrowing that merely removes impossible scope immediately. Keep inferred resolutions, merges, splits, supersessions, and conversions from an existing user decision to agent work pending until confirmed. Represent each pending transformation in the owning source table with state `proposed`, its exact proposed transition and outcome in `Outcome / current context`, and the originating `Rn` once logged. Do not create split children, supersede rows, or state a pending outcome as normative body content before acceptance. On acceptance, apply the transition. On decline, restore the prior active state and retain a concise current-state cue that the proposal was declined and must not be resurfaced unless relevant content or evidence changes. Deferral uses `deferred`; later reconsideration uses `reopened` in the event and the appropriate active table state.
5. **Record material reconciliation.** The transcript is the authoritative chronological mutation log. Log semantic body propagation; decision, work, and candidate-prompt additions or transitions; direct decisions made; automatic resolutions or refinements; question retirement; and evidence-driven work completion. Do not log formatting or copy-editing. Append one source-qualified event:

   ```markdown
   ### Reconciliation R4
   - Trigger: Q3 answer
   - Resolved: living/design/design-a:D4 — outcome Y; authority: direct user answer
   - Reframed: living/design/design-a:D6 — user-decision narrowed from X-or-Z to Z details
   - Added: living/design/design-b:D7 — proposed resolution A
   - Retired prompt: X ownership — answered by Q3
   - Work: living/design/design-a:W2 — in-progress → completed; evidence: repository inventory
   - Propagated: living/design/design-a Results; living/design/design-b acceptance criteria
   - Basis: living/design/design-a:D2 fixes the structural boundary
   - Confirmation: pending checkpoint
   - Next: living/design/design-a:D9 — highest dependency leverage
   ```

   Use concise event verbs such as `Added`, `Reframed`, `Split`, `Merged`, `Deferred`, `Resolved`, `Reopened`, `Superseded`, `Retired prompt`, `Work`, and `Propagated`. Include the resulting outcome and authority when a decision resolves. Append later confirmation as a new event rather than rewriting history. Do not duplicate the full event in a source artefact or create another temporal audit artefact; its body and current-state tables are the materialised result. A compact source-local pointer to the transcript event is optional when useful for navigation and required in a pending proposal row so its exact audit context remains resolvable.
6. **Confirm pending changes one commitment at a time.** Separate established changes from proposals:

   > Reconciliation applied:
   >
   > - design-a:D4 — narrowed from X to Y; Z was already fixed by D2.
   >
   > Next proposed resolution:
   >
   > - design-b:D7 — resolve as A; the accepted constraints remove B's only advantage. This trades X for Y.
   >
   > Accept D7 as proposed, adjust it, or review the viable alternatives?

   If there are no proposals, report applied changes informationally and continue without a checkpoint. At a cold opening, always handle proposals individually. During an active pass, offer a proposal bundle only when every item arose from the same fresh trigger, is decision-ready from the current conversation, and forms one coherent outcome the agent can recommend as a unit. Ask whether to accept that bundle or review it; do not ask for several independent dispositions. If the user asks to review all or does not accept the bundle, create a review queue and present only its first item. “Review all” applies to the most recently presented explicit inventory; never combine proposal and review-finding queues. After every answer, reconcile and reprioritise before presenting the next item because the queue may narrow, reorder, or collapse. Honour volunteered partial acceptance, adjustment, or deferral without turning it into a multi-item form.
7. **Choose the next action.** If bounded agent work can still collapse a decision, do it first. Otherwise ask only when the user-decision test passes. Prioritise dependency leverage, then material impact or irreversibility, then ambiguity and conversational flow. Let useful user momentum break near-ties. If a tie remains, choose the item with the clearest next consequence and state the reason; do not ask the user to manage an equally useful queue. Use a concise heading such as `Q3 — D2 + D4: Ownership boundary`; add the shortest unique source label only when a multi-source session is ambiguous. Follow the shared Q&A rules loaded during assessment.
8. **Show honest progress.** Report user decisions and agent work separately, for example: "User decisions: 3 of 4 resolved; 1 proposed. Agent work: 2 of 5 complete." Never keep reclassified agent work in the user-decision denominator.

## Decision authority

- A direct, unambiguous user answer confirms every decision it explicitly addresses.
- An inferred product or preference decision remains proposed until the user accepts it in a reconciliation checkpoint or individual review.
- Agent work can be completed from evidence without preference confirmation. Converting an existing user decision to agent work still requires a reconciliation checkpoint because it changes the agreed agenda.
- Never silently decide between genuinely viable product outcomes for the user.

## Ending the current pass

- When all user decisions and required agent work are resolved, run a concise self-check for placeholders, contradictions, vague language, scope drift, and unpropagated outcomes. Reconcile any material issue through this loop.
- Once the known agenda is resolved and the self-check passes, assess the current artefact explicitly against the taxonomy bar. Only when that bar is met is it a candidate for completion; then use the recommended A/B/C four-Cs offer in `references/review.md`. Do not claim final readiness before review or an explicit skip-and-complete choice. The completion option must name the exact `{completion_status}` so choosing it supplies status approval; the stop option leaves lifecycle state unchanged.
- If the user accepts review, validate its inventory before asking for consent. Only accepted findings enter this loop and may reopen decisions or work.
- After review-driven fixes, assess the artefact again and use the same recommended A/B/C offer. After substantive reopening, reach the candidate exit again before offering review. The user may bypass review or stop without lifecycle promotion on every cycle.
- If exact status approval was not already supplied through the review bypass, confirm: "Set status to `{completion_status}`?" Apply the exact taxonomy value with `artefact.set-status(path="{path}", status="{completion_status}")`.
- Signal: "Fully shaped — [artefact] is `{completion_status}`."

## Red flags

- Asking several substantive questions in one turn
- Bulk-offering proposals on a cold opening
- Explaining every queued proposal after the user asks to review all
- Asking the user to choose when accepted constraints or evidence already supply a clear winner
- Moving on without reconciling and reprioritising the agenda
- Updating only a decision table while affected prose or related artefacts remain stale
- Closing an inferred product decision without confirmation
- Mixing agent-owned work into the user-decision progress count
- Inventing completion criteria instead of reading the type's bar
- Declaring completion without offering the optional four-Cs review
- Presenting finish, review, and unmotivated further shaping as equally weighted exit options
- Treating the review as mandatory after the user declines
- Changing status without explicit approval
- Presenting strawman alternatives or jumping to solutions before framing the decision
