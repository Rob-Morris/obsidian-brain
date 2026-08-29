# Session assessment and shared Q&A rules

## Build a proposed session plan

Understand the request and inspect the named artefact or relevant context before
choosing a workflow. Treat setup as planning: establish the resources and
preferences first, then show the proposal before creating new session records or
making shaping changes.

An available environment integration may offer target resolution and mutation,
shaping metadata, or persistence methods. Its availability is input to the
plan, not a session-wide override: select its contribution separately for the
artefact, decision/work record, transcript, and any lifecycle handling. An
integration can therefore shape a managed artefact while a local document and a
scratch transcript remain independent resources in the same session.

Resolve the plan in this order:

1. **Honour explicit user choices.** A requested artefact location, shaping
   style, completion goal, decision log, transcript location, or chosen
   environment takes precedence.
2. **Use target-specific context.** If an artefact resolves through an
   integration, use its type-specific style, bar, lifecycle behaviour, and
   preferred persistence as the proposed defaults for that artefact. When the
   user explicitly chooses an integration for another role, apply that selected
   contribution while preserving their choices for the other resources.
3. **Infer a portable default.** A request to settle a bounded outcome, make
   decisions, or produce a usable design normally suggests **convergent**
   shaping. A request to understand, capture, or develop a topic without a
   bounded decision agenda normally suggests **exploratory** shaping. Derive a
   concise completion bar from the stated purpose, constraints, and intended
   use.
4. **Ask when the choice would materially alter the session and evidence does
   not select a sensible recommendation.** Otherwise present the recommendation
   for confirmation.

The plan records these independent concerns:

- **Style and workflow:** convergent + brainstorm/refine, or exploratory +
  discover.
- **Completion bar:** the concrete condition for completing this shaping pass.
- **Artefact handling:** the documents or other resources to update, or an
  in-session working draft when the user has not chosen a destination yet.
- **Decision/work record:** the artefact itself, a sidecar, another
  user-selected record, or an in-session agenda. Keep a document clean by
  proposing a sidecar when that appears useful.
- **Transcript:** whether to retain the Q&A and material reconciliation history,
  and its chosen location. It may share a record with decisions when that is
  useful, remain separate, or be omitted.
- **Environment contributions:** the integration, capability, and scope chosen
  for each concern. An integration supplies capabilities; it does not override
  the plan.

When no persistence location is suitable and the user wants a record, use an
OS-temporary scratch location outside the user’s repository. State its path in
the proposed plan so the user can retain, move, or discard it deliberately.
When persistence is not wanted, retain only the in-session draft or agenda and
make that choice clear in the plan.

Present the result in a compact form, for example:

```text
Proposed shaping setup
- Style: convergent — settle the deployment and ownership decisions
- Done when: the design records the chosen approach, trade-offs, and remaining work
- Artefact: docs/deployment-design.md
- Decisions: docs/deployment-design.shaping.md
- Transcript: temporary scratch record
- Integration: local-file workflow

Ready to begin, or would you like to change any of these?
```

After approval, initialise the selected records and retain the plan as session
context. Re-show the affected part and obtain confirmation if a later request
changes where an artefact, decision record, or transcript is kept.

## Shared Q&A rules

- **One user commitment at a time.** A turn may ask one substantive question or
  one procedural question. A coupled question may address several decisions
  when one answer genuinely resolves them together.
- **Keep question and decision identities distinct.** When a decision or work
  log is kept, give its entries stable local IDs such as `D4` or `W2`. If a
  transcript is kept, give substantive questions and material reconciliations
  stable transcript-local IDs such as `Q3` and `R5`.
- **Reconcile before asking again.** Apply an answer across the affected
  artefact and records, retire or narrow affected prompts, and reprioritise the
  remaining work.
- **Follow useful momentum.** Start with dependency leverage and material
  impact, while letting the user's interest, deferrals, corrections, research
  requests, and stopping point steer the session.
- **Prefer real choices.** When several viable alternatives exist, explain the
  tension, present concise options, and recommend one. When evidence already
  selects a clear outcome, explain and apply it rather than manufacturing a
  choice.
- **Use evidence before asking for facts.** Complete bounded inspection or
  verification when it can eliminate or materially reframe a question. Offer a
  research spike when the work needs more time, authority, or a separate scope.
- **Record proportionately.** Keep the artefact or in-session draft as the
  current source of truth. Record material decisions, work transitions, and
  reconciliations in the locations chosen by the session plan; an in-session
  agenda is sufficient when no durable decision record was selected. Avoid
  logging routine copy-editing.
