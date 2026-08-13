# Discover workflow

Use discovery to develop an artefact through open-ended refinement: expand it, clarify what it says, improve accuracy, and follow useful threads without a predetermined amount of exploration. The conversation is piece-of-string even when the artefact has a bounded lifecycle bar.

If the parent workflow has not supplied an artefact path and transcript path, return to the root workflow and run `references/assess.md` first.

## Discovery loop

After every authoritative change, including an answer, correction, resumed session, relevant evidence result, source expansion, or accepted review finding:

1. **Apply it faithfully and respect the artefact's time model.** Update every affected section of every in-scope artefact. For a living artefact, maintain the current picture by replacing or qualifying stale current-state claims and retaining dated history when useful. For a temporal artefact, preserve the bounded moment: add dated qualification or provenance instead of rewriting history with later state, and update a related living artefact when that is the proper home for the current picture.
2. **Refresh the thread map.** Maintain a lightweight working view of what is covered, active or hinted, uncertain or potentially outdated, declined, and outside the user's present interest. Do not force this into a decision table. Persist durable current-state cues such as "declined for now", "verify later", or "outside present interest" in natural artefact prose when forgetting them could cause stale questions on resumption. Use reconciliation events for how those cues and possible prompts changed, but reconstruct current state from the artefact rather than replaying the transcript.
3. **Reconcile the remaining prompts.** Retire prompts answered directly or indirectly, merge overlaps, narrow ambiguous prompts with the new context, and surface useful adjacent possibilities. Never ask a stale prewritten question merely because it was next on a list.
4. **Follow the user's energy.** Deepen the current thread when the answer shows interest or leaves salient material incomplete; broaden when an adjacent area would improve the artefact more. Offer useful suggestions without steering away from the direction the user is taking.
5. **Improve current accuracy proportionately.** Use available linked context and authoritative sources to verify time-sensitive claims when the artefact's purpose calls for it. Otherwise preserve uncertainty or ask whether the user wants verification rather than turning discovery into unsolicited research.
6. **Record material reconciliation.** Keep Agent/User turns verbatim. Add `### Reconciliation Rn` to the transcript for material reinterpretation; thread or candidate-prompt addition, reframing, merging, deferral, closure, or reopening; present-state correction; scope change; or multi-source propagation. The transcript owns this mutation history and each affected source holds the resulting current content. Do not duplicate the full event in another audit artefact or log literal capture, formatting, or copy-editing merely for volume.
7. **Choose one next turn.** Ask one useful question, make one compact suggestion, or offer a single navigation choice between continuing the most promising thread and stopping. Terse state deltas are preferable to generic progress chatter.

Example progress signal:

> Captured origins and current interests; that also covers how the relationship began. One hinted thread remains: how you work together. Continue there, or stop for now?

## Pausing or ending the current pass

- **Conversation stop:** The user may stop whenever they choose. If the taxonomy bar is not yet met, close or pause the session without pressure and leave the lifecycle status unchanged. Briefly say what remains optional for a later pass.
- **Shaping completion:** Assess the artefact against its taxonomy bar for the user's intended current scope. Do not use the optional review as the only bar check. Acknowledged uncertainty may be complete when the taxonomy permits it. For `status_behaviour: transition`, completion includes an approved lifecycle transition; for `preserve`, it closes only the current discovery pass.
- When the bar is met and the user is ready to stop, treat the pass as a completion candidate and use the recommended A/B/C four-Cs offer in `references/review.md`. State at a high level why the pass meets the bar and recommend review first. For `transition`, B skips review and sets `{completion_status}`. For `preserve`, B completes the pass with status unchanged unless the artefact is already in `shaping`; in that case B names and approves the declared `{completion_status}` exit. C stops without asserting pass completion and always leaves lifecycle state unchanged. Do not add a generic continue-shaping option when the user has already chosen to stop.
- If the user requests review, validate its inventory before asking for consent. Only accepted substantive gaps re-enter this discovery loop.
- After review-driven fixes, assess the pass again and use the same recommended A/B/C offer. After substantive reopening, wait until the pass again reaches a useful stopping point before offering review. The user may bypass review or stop without lifecycle promotion on every cycle.
- For `transition`, if exact status approval was not already supplied through B, confirm before changing status: "Set status to `{completion_status}`?" Apply the taxonomy's exact value with `artefact.set-status(path="{path}", status="{completion_status}")`. For `preserve`, do not change an existing enduring status merely to open or complete shaping. If the artefact is already in `shaping` and the taxonomy declares a completion status, obtain the same exact approval and apply that status as its exit.
- Signal either "Current discovery pass complete — [artefact] is `{completion_status}`" after a transition or explicit exit from `shaping`, or "Current discovery pass complete — [artefact] remains `{current_status}`" when preserving an enduring status. For a revisitable living artefact, add that it can be refined again whenever useful. For a bounded temporal artefact, do not imply that later current-state changes should rewrite the record.

## Red flags

- Treating open-ended discovery as a reason to ignore the taxonomy bar
- Treating a temporal record as a living current-state artefact
- Walking a fixed territory checklist regardless of user interest
- Broadening automatically when the current thread is clearly valuable
- Re-asking information the latest answer supplied indirectly
- Making the user feel they need to continue
- Promoting lifecycle status merely because the user chose to stop
- Treating pass completion as a lifecycle transition when status behaviour is `preserve`, except for an approved declared exit from an existing `shaping` state
- Offering or running the four-Cs review as a mandatory gate
