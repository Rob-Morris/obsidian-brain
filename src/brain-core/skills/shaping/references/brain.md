# Brain adaptor

This adaptor contributes Brain capabilities to the portable shaping workflow.
Use each capability only for the concern that selects it in the proposed
session plan. Brain availability does not select Brain persistence globally.

## Assess Brain context

Before proposing the session plan, inspect only enough context to make a sound
recommendation:

- Resolve a named Brain artefact with `artefact.read`. If creation is wanted,
  identify the type and use `artefact.create` only after the plan is confirmed.
- For a resolved Brain artefact, read its type with
  `resource.read(resource="type", reference="{type-key}")`. Its shaping flavour,
  bar, status behaviour, and completion status are target-specific defaults.
- Treat explicit choices for the artefact, decision/work record, transcript,
  and lifecycle as independent and authoritative. Ask one small setup question
  when an ambiguity would materially change where data is written.

Before proposing a plan that may create a Brain target or Brain transcript,
apply the
[standard creation-capability preflight](../../../standards/shaping.md#brain-command-effects).
Only select the create-then-continue operation after that preflight succeeds.

Do not create records, mutate an artefact, or call `shaping.start` while the
session plan is still a proposal.

## Show the proposed plan

Use the session-plan contract defined by the portable assessment workflow; do
not rename, remove, or redeclare its fields. Populate its environment
contributions by naming the selected capability separately for target
resolution/mutation, decision/work persistence, transcript persistence, and
lifecycle handling. Where Brain supplies a capability, name its exact role
rather than describing the whole session as a `Brain workflow`.

Ask whether the user is ready to begin or wants to change the proposal. Once
confirmed, it becomes the session contract. Reconfirm only the affected part if
a later request changes one of these locations or capabilities.

## Brain defaults are recommendations

For a Brain artefact, normally propose:

- the taxonomy flavour and bar for style and completion;
- the Brain artefact itself for material decisions and agent work when its
  normal body or shaping tables are appropriate;
- a Brain shaping transcript when the user wants a durable transcript; and
- normal Brain provenance, links, and lifecycle handling.

Use Brain persistence after confirmation as follows:

- **Brain target:** resolve and mutate through supported Brain interfaces.
  Apply the command-effect and revision rules in
  [the Brain shaping standard](../../../standards/shaping.md#brain-command-effects).
- **New Brain target:** after a confirmed `artefact.create`, call
  `runtime.refresh-router` before the next router-dependent mutation. This
  operation is selectable only after the availability preflight above.
- **Brain transcript and lifecycle together:** when the confirmed plan selects
  the standard Brain transcript and its declared lifecycle behaviour, select
  `shaping.start(target="{path}", mode="{mode}")` and apply the standard's
  command effects.
- **Lifecycle without a Brain transcript:** do not call `shaping.start`. For a
  transitioning taxonomy, select
  `artefact.set-status(path="{path}", status="shaping")` at opening; a
  preserving taxonomy selects no opening mutation. Apply the standard's
  lifecycle outcomes at completion. Persist the transcript independently in
  its selected local or scratch location.
- **Brain transcript without Brain lifecycle:** do not call `shaping.start`.
  Resolve the normal shaping-transcript artefact through its configured
  taxonomy. If none exists, create it with `artefact.create`, put the source
  wikilink in its body, and select the standard new-target refresh rule. If it
  already exists, select the standard read-revision-mutate rule. Add missing
  source links and backlinks under the standard provenance rules, leave
  lifecycle status unchanged, and append turns and reconciliations under the
  standard transcript conventions.
- **Brain durable records:** create them through the normal taxonomy and
  artefact workflow, using the appropriate type rather than an ad hoc vault
  file.
- **Local or scratch records:** use the selected local-file or OS-temporary
  mechanism. Do not create a Brain counterpart unless the user changes the
  plan.

When the plan selects any Brain role, follow `standards/shaping.md` for the
taxonomy, command-effect, lifecycle, provenance, and transcript invariants that
apply to that role. Brain transcript conventions do not apply to a transcript
stored elsewhere unless the user selects them.

## Required planning examples

### Brain artefact workflow

- Artefact: Brain design, resolved and mutated through Brain.
- Decisions and agent work: the design's normal shaping sections.
- Transcript: Brain shaping transcript.
- Environment contributions: Brain for target, decisions/work, transcript,
  provenance, and lifecycle; `shaping.start` after confirmation.

### Connected Brain, local document

- Artefact: the requested repository document through local-file tooling.
- Decisions and agent work: the requested local sidecar.
- Transcript: the requested OS-temporary scratch path.
- Environment contributions: local-file and scratch capabilities for those
  roles; no Brain record and no `shaping.start` merely because Brain is
  connected.

### Brain artefact with scratch transcript

- Artefact: Brain artefact, resolved and mutated through Brain.
- Decisions and agent work: the Brain artefact unless the user chose another
  record.
- Transcript: the explicit OS-temporary scratch path.
- Environment contributions: Brain for the target, selected decision/work
  persistence, and selected lifecycle; scratch storage for the transcript.
  Apply transition lifecycle with `artefact.set-status` while keeping the
  transcript in scratch. Do not call `shaping.start`, because it would create a
  Brain transcript and violate the confirmed plan.

### Brain transcript with lifecycle override

- Artefact: Brain artefact, resolved and mutated through Brain.
- Decisions and agent work: the Brain artefact unless the user chose another
  record.
- Transcript: Brain shaping-transcript artefact created or resumed through the
  normal taxonomy and linked to every source.
- Environment contributions: Brain for the target, decisions/work, and
  transcript; explicit lifecycle override leaves status unchanged. Resolve the
  existing transcript first. If none exists, create it with `artefact.create`
  and select the standard refresh rule; otherwise select the standard
  read-revision-mutate rule. In both cases, use the standard provenance rules.
  Do not call `shaping.start`.

### No durable artefact

- Artefact: in-session draft.
- Decisions and agent work: in-session agenda or omitted.
- Transcript: omitted unless requested.
- Environment contributions: none beyond session context.

## Guardrails

- Never treat a connected Brain as a session-wide persistence switch.
- Never collapse artefact, decision/work, transcript, and lifecycle choices
  into one `Brain workflow` flag.
- Never write before the proposed plan is confirmed.
- Never begin a create-then-continue sequence without confirming that the
  active profile can run the required router refresh.
- Never turn a Brain default into an unconditional requirement.
- Never read workflow instructions from a sibling `agent-skills` checkout at
  runtime; use this active Brain's versioned package.
