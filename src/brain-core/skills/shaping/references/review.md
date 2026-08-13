# Optional four-Cs review workflow

Proactively recommend this shared workflow after an active shaping workflow reaches a candidate stopping or handoff point. Also honour an explicit user request for a four-Cs review at any time; a mid-pass review informs the active workflow without implying readiness to finish. The review is a repeatable quality option, not a mandatory completion gate.

Set `review-entry` to `candidate-exit` for the proactive offer or `mid-pass` for an explicit earlier request. Preserve that entry context through outcome handling.

## Agent-led offer

First determine whether a separate subagent is available. State the candidate condition provisionally, recommend review as the next step, and ask one procedural A/B/C question:

1. Name or closely paraphrase the taxonomy bar, then explain in one high-level sentence why the artefact meets it. Use shaping-state evidence such as resolved decisions, documented trade-offs, completed required work, and the passing self-check. Do not summarise the artefact section by section or restate its detailed design.
2. Explain the review in one concise sentence: it checks correctness, clarity, consistency, and completeness; the agent asks before applying fixes; it then either supports completion or returns decision-worthy gaps to the active shaping workflow. Do not define each C unless the user asks.
3. Present these outcomes, adapted to the mode:

   > **A. Run the [independent / separate] four-Cs review — recommended.**
   >
   > **B. Skip the review and [set the artefact to `{completion_status}` / hand off to refine / complete the current pass with status unchanged / exit explicit `shaping` to `{completion_status}`].**
   >
   > **C. Stop here without completing the current pass; leave lifecycle status unchanged.**
   >
   > Which option would you like?

For refine, choosing B supplies exact status approval. For brainstorm, B supplies handoff approval. For discovery with `status_behaviour: transition`, B supplies exact status approval. With `preserve`, B normally asserts only that the current pass is complete; when the artefact is already in `shaping`, B instead supplies exact approval for its declared completion-status exit. C never asserts completion or changes status. Say **independent** only when a separate reviewer is available; otherwise say **separate**. Keep A visibly recommended rather than presenting three equally weighted paths. If the agent knows of further shaping, there is no candidate exit and it must not show this menu. If the user chooses B or C, return immediately to that action.

## Review context

- Read the current source artefact or artefacts, the taxonomy shaping bar, and directly related sources needed to check claims or consistency.
- Review the artefact as it now stands. Consult the shaping transcript only when provenance is necessary to interpret an accepted decision.
- Use a read-only subagent when available. Give it the artefact and neutral review criteria, not the intended verdict, earlier conclusions, prior review dispositions, or proposed fixes. It must not edit. If subagents are unavailable, perform a clearly separated second pass.
- Apply the mode-specific meaning of completeness:
  - **brainstorm:** purpose, boundaries, and the decision/work surface are explicit enough for refine, including an explicitly empty agenda; brainstorm never applies the completion status itself;
  - **refine:** the taxonomy bar is met, with no unresolved proposal or required agent work;
  - **discover:** the taxonomy bar is met for the user's intended current scope. Do not demand exhaustive territory or treat acknowledged uncertainty as a gap when the taxonomy permits it.
- For `review-entry: mid-pass`, assess completeness relative to the artefact's current stage. Do not report already-known unfinished agenda as a defect or use the exit bar to imply failure; look for omissions, contradictions, or unclear state beyond the active workflow's known work.
- For time-sensitive claims, check present-day accuracy proportionately with available authoritative sources. Distinguish verified error from uncertainty or a suggestion to investigate.
- For discovery artefacts, correctness primarily means faithful capture of the user's account. Verify external claims only when material to the artefact's purpose; preserve self-reported uncertainty and dated change. Treat unmarked contradiction as a consistency issue, but do not mistake legitimate evolution or plural perspectives for inconsistency.

## Four Cs

1. **Correctness:** Claims, constraints, links, examples, current-state information, and accepted outcomes are accurate and supported.
2. **Clarity:** A fresh reader can understand the purpose, boundaries, terminology, and next use without the shaping conversation.
3. **Consistency:** Sections, decisions, related sources, terminology, lifecycle state, and provenance agree.
4. **Completeness:** The mode-specific current-pass bar is met and no material placeholder, unresolved ambiguity, or unfinished required work remains.

## Review outcome

The reviewer returns evidence-backed findings with affected artefacts or decision IDs and severity: `blocking` when the current bar cannot honestly be met, `material` when the refinement has meaningful user value, and `minor` when it is optional polish. Reviewer output is candidate evidence, not authoritative shaping state. The main agent validates and deduplicates it before showing it to the user. Assign stable transcript-scoped finding IDs such as `C1`, `C2`, and allocate them monotonically across reruns and same-day resumptions. Keep the same `Cn` when wording, evidence, or a partial fix changes but the underlying defect and affected contract remain the same; allocate a new ID for an independent defect.

Classify the validated result:

1. **No refinement needed.** Tell the user the review found no material refinement. At `candidate-exit`, recommend the named handoff or status and ask for exact confirmation; at `mid-pass`, return to the active workflow.
2. **Proposed fixes.** Inventory concrete corrections or clarifications, recommend the next fix or one coherent fix bundle, and ask for one commitment before applying any review-originated edit.
3. **Further shaping suggested.** Inventory gaps that require user judgement or meaningful exploration, recommend the highest-priority reopening, and ask one question about it. Do not silently convert several findings into decisions or questions.

When findings exist, present a compact high-level inventory so the user understands the review result, but request only one commitment. Prioritise blocking findings, then dependency leverage and material impact; use correctness and completeness to break ties before clarity polish. A bundle is valid only when its items are decision-ready and form one coherent correction the main agent recommends as a unit. Otherwise begin with the highest-priority finding. If the user asks to review all, apply that phrase only to the most recently presented finding inventory, create a queue, and explain only the first item; after each disposition, reconcile and reprioritise before continuing. Explain the finding's evidence, impact, recommended action, and material trade-off; if that remains unclear, fall back to genuinely viable multiple-choice alternatives with a recommendation. Treat the review as evidence, not authority. Do not let the reviewer mutate artefacts or make product choices. Only a finding the user accepts becomes a reconciliation trigger.

Apply only what the user accepts. Route accepted changes through the active workflow:

- brainstorm -> shape synthesis loop, with promotion when a finding has matured into a decision or work item;
- refine -> impact sweep and reconciliation;
- discover -> thread-map reconciliation that follows the user's interests.

Record every material accepted review-driven change as `### Reconciliation Rn` in the transcript, including any decision, work, or prompt-agenda transition it causes. Affected source artefacts hold the resulting current state rather than a duplicate event history. A declined finding remains a review disposition in the verbatim transcript, not an outstanding shaping item, unless the user asks to retain it.

After the user declines one or more findings, reassess the candidate exit against the validated evidence. If the remaining findings do not prevent the taxonomy bar from being met, recommend the named handoff or status and ask for exact confirmation. If a blocking finding remains true, do not claim readiness: recommend revisiting the highest-priority blocker or stopping without lifecycle promotion, one commitment at a time.

At `candidate-exit`, assess again after accepted fixes. When the known agenda is resolved and the self-check passes, use the same recommended A/B/C offer; do not use a neutral finish/rerun/continue navigator. After substantive reopening or material new content, return to the active workflow and make the normal agent-led offer when it next reaches a candidate exit. At `mid-pass`, return directly to the active workflow after accepted findings are reconciled; recommend review or offer completion only if that reconciliation independently reaches a candidate exit. This keeps review repeatable without falsely implying readiness.

Keep the reviewer neutral on a rerun, then let the main agent deduplicate its output against earlier finding IDs and user dispositions in the current transcript. Do not resurface a declined finding unless relevant content or evidence changed; if it did, explain what changed. No correctness rule depends on an implicit shaping-pass namespace: session headings mark interaction boundaries while Q, R, and C sequences remain transcript-scoped.

## Red flags

- Running the review before the user accepts the offer
- Treating a review as a mandatory gate
- Treating reviewer output as automatically correct
- Applying review-originated fixes without consent
- Reconciling an unaccepted review finding into shaping state
- Letting a reviewer edit or see a desired verdict
- Using completeness to force exhaustive discovery
- Fixing a substantive choice as though it were mechanical
- Reopening shaping without user agreement
- Re-presenting an unchanged finding the user already declined
- Claiming final readiness before review or explicit bypass
- Giving too little context for an informed exit choice
- Explaining the four Cs individually or summarising artefact details in the exit offer
- Presenting an unranked exit menu instead of recommending the review
- Explaining several queued findings in one review turn
- Offering generic continued shaping when the agent knows of no remaining work
