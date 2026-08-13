# Shaping

Shaping is the iterative development of an artefact through structured, adaptive Q&A. It expands, refines, clarifies, corrects, and reconciles the artefact while recording how material changes were reached. Convergent shaping has a defined readiness goal; discovery shaping uses an open-ended method whose amount and direction emerge from the conversation rather than a fixed questionnaire.

Shaping produces an improved, self-sufficient artefact and a transcript containing both verbatim dialogue and an append-only event history of material content, decision, work, and prompt-agenda changes.

## Shapeable Artefacts

A type is shapeable if:
1. Its taxonomy declares `shaping` in its lifecycle
2. Its taxonomy defines a `## Shaping` section specifying:
   - Its primary flavour (`Convergent` or `Discovery`)
   - What "fully shaped" means for this type (the bar)
   - Optionally, `Status behaviour: preserve` for discovery types whose lifecycle describes an enduring domain state
   - For `transition`, what status to apply when shaping completes (e.g. `ready`); a preserving type may declare one only as the explicit exit for an artefact already in `shaping`

Each type owns its definition of readiness for lifecycle completion. For convergent types this may mean "fully shaped"; for discovery types it is the bar for completing the current pass, distinct from the user's freedom to pause earlier. The bar should be concrete enough that an agent can judge whether the completion status is warranted. Examples:

- **Ideas:** Clear *what* the idea is and *why* it matters. Open questions about *how* are fine — that's design territory.
- **Designs:** All decisions resolved, core goal clear, approach concrete enough to plan, no internal inconsistencies.
- **People:** For the intended current scope, the card faithfully and clearly reflects what the user wants recorded, with material uncertainty or inconsistency explicit; the current discovery pass may end while the person card keeps its enduring lifecycle status.

Types opt in with an explicit lifecycle contract and a complete `## Shaping` section. `compile_router.py` parses the flavour, bar, optional status behaviour, and completion status when applicable, then exposes them as the type's `shaping` metadata. Status behaviour defaults to `transition`, preserving every existing contract. `preserve` is valid only for discovery shaping and leaves an enduring non-terminal status unchanged when shaping opens or completes. It may declare a completion status solely as the approved exit for an artefact already in `shaping`. Both behaviours still require `shaping` in the declared lifecycle so users may assign it explicitly. An incomplete or contradictory section, or one that references an undeclared lifecycle value, is a compile error.

## Shaping Flavours

Not all shaping works the same way. The process adapts to the artefact's nature:

- **Convergent shaping** drives toward specific decisions. The decision list is the primary tracking mechanism, and shaping completes when all decisions are resolved and the artefact is internally consistent. Examples: Designs, Plans, Tasks.

- **Discovery shaping** develops an artefact without a predetermined amount or route of exploration. It follows the user's interests to expand, refine, clarify, and improve accuracy. Living artefacts maintain a revisitable current picture; temporal artefacts preserve a bounded moment and must not be rewritten as though they were current-state records. A conversation may stop whenever the user chooses, while lifecycle completion still depends on the type's taxonomy bar. Examples: People, Journal Entries, Cookies.

Most artefacts lean one way, but a session can blend both — a design might start with discovery (what are we even designing?) before converging on decisions. The type's `## Shaping` section should indicate which flavour is primary.

**Convergent artefacts** must carry a shaping-decisions table and a separate agent-work table once concrete agenda items emerge so current state is resumable. A template may provide them; otherwise refine creates them when the agenda is first set, while brainstorm promotes only mature items. **Discovery types** do not need these tables. They use a lightweight working thread map and persist material automatic reinterpretation or narrowing in the reconciliation audit.

## Opening or Continuing a Shaping Session

The shaping skill owns the end-to-end activity: resolve or create the artefact, read its taxonomy contract, choose a mode, run the adaptive Q&A loop, recommend an optional four-Cs review at a candidate exit, and apply the taxonomy's completion behaviour with user approval. Once the target and mode are known, it calls `shaping.start(target="...", mode="refine")`.

The low-level `shaping.start` command opens or continues the session mechanics only:

1. **Validates shapeability** — uses the compiled taxonomy contract
2. **Identifies the artefact** — resolves an existing artefact by name or path
3. **Creates or appends to today's transcript** — linked to the artefact, with provenance in both directions
4. **Applies status behaviour** — `transition` enters `shaping` through the lifecycle handler, including moves and hooks; `preserve` leaves a non-terminal status unchanged

A preserved target in a terminal status is rejected before any mutation. The user must explicitly assign a non-terminal lifecycle status before shaping it; merely opening a discovery session must not revive a terminal record.

The action does not conduct Q&A, choose what to ask, or decide that shaping is complete. Those are skill-level judgements. Internally, `start_shaping_session()` names this narrower session-boundary primitive; `start_shaping.py` remains a compatibility launcher for direct script consumers.

Sometimes shaping begins before the user knows what they're shaping. In this case, the first questions are exploratory — identifying the artefact type and creating it is part of the skill process. The skill calls `shaping.start` once the target is clear.

### Source linking

The transcript's first body line identifies all source artefacts:
```
**Source:** [[Artefact1|Title1]]
```

As shaping expands to touch additional artefacts, append them:
```
**Source:** [[Artefact1|Title1]], [[Artefact2|Title2]]
```

Each source artefact links back via `**Transcripts:** [[transcript|Session]]` — see the [provenance standard](provenance.md).

### Current state and event history

The artefact is the source of truth for where shaping stands: its semantic body, current decisions, current agent work, and durable current-state cues. The shaping transcript is the authoritative chronological event history: verbatim turns plus material mutations that explain what was added, changed, resolved, reopened, or propagated and why.

Do not make a second temporal audit artefact and later recombine it with the transcript. Do not require a future agent to rebuild current state by replaying events. An agent resuming shaping reads the artefact first and consults transcript events only for provenance such as why a decision changed. A compact source-local pointer to an important reconciliation event is permitted for navigation, but it is not a duplicate log or a second source of truth.

### Setting the agenda

Review the artefact's current state and identify what needs to be decided. If the artefact is new and blank, the first question establishes what this is about.

For convergent shaping, the agenda is a live reconciliation of the artefact, not a fixed questionnaire. Distinguish:

- genuine **user decisions**, where materially different viable outcomes remain and user judgement is missing;
- **proposed resolutions**, where accepted constraints or evidence now leave a clear winner;
- **agent work**, where research, specification, consistency repair, or verification should determine the answer;
- deferred, resolved, merged, and superseded items.

Preserve stable decision IDs as wording and state evolve. Rebuild and reprioritise the agenda after every answer.

Use these sections, creating or normalising them when needed:

```markdown
## Shaping Decisions

| ID | Decision | State | Outcome / current context | Depends on |
|---|---|---|---|---|

## Shaping Work

| ID | Work | State | Evidence / result | Affects |
|---|---|---|---|---|
```

Decision states are `user-decision`, `proposed`, `deferred`, `blocked`, `resolved`, and `superseded`. Work states are `pending`, `in-progress`, `blocked`, and `completed`. Existing decision tables are normalised without discarding their content.

IDs are stable within the owning source artefact and source-qualified in cross-artefact events (`living/design/key:D4`, `living/design/key:W2`). A living source uses its immutable canonical `{type}/{key}`; a temporal source uses its source wikilink/path from the transcript `**Source:**` line. Narrowing preserves the ID. A merge keeps one survivor and points superseded IDs to it. A split preserves the parent as a superseded lineage record and creates child IDs such as `D4a` and `D4b`.

Question IDs identify immutable asked turns and are scoped to one daily transcript. Allocate the next number by scanning leading `Qn` labels in Agent turns and taking one greater than the highest value; ignore incidental references elsewhere. Once `Q3` is recorded, do not rewrite it when later context narrows the live agenda. Before asking, a possible prompt is ephemeral and may be narrowed, merged, split, or retired using old/new wording plus the decision/work IDs it would address, or a stable discovery-thread label when no D/W ID exists. Do not assign prompt IDs or reconstruct candidate prompts on resumption. Reconciliation events connect historical questions to current-state mutations. One question may resolve several decisions, and several questions may inform one decision. In user-facing headings, keep the distinction lightweight: use `Q4 — D11: Responsive repeated quit` for a single source and prefix the ID with the shortest unique source title or key only when a multi-source session would otherwise be ambiguous. Keep full immutable source qualification in audit events and source-state pointers. Proposal confirmations, review-finding dispositions, navigation choices, and status confirmations are procedural and remain unnumbered; their effects are connected through D/W/C IDs and reconciliation events.

## During Shaping

### One user commitment per turn

Each user-facing turn asks for one substantive decision or one procedural decision. Informational reconciliation may accompany it, but must not create another choice. A coupled question may address several D/W IDs only when one answer genuinely resolves them together. Each normal shaping-question Agent turn begins with its transcript-scoped label (Q1, Q2, …) and concise related D/W references. The next question is chosen after reconciliation based on dependency leverage, material impact, remaining ambiguity, conversational flow, and the user's demonstrated interest — not by walking a pre-made list in order.

A convergent **Reconciliation checkpoint** is procedural rather than a numbered shaping question, but it follows the same one-commitment rule. On a cold opening or resumption, never bulk-offer inferred resolutions: report established normalisation and narrowing, inventory proposal count at a high level, and present only the highest-priority proposal or genuine decision in decision-ready form. During an active pass, a bundle is allowed only when its proposals share the same fresh trigger, are understandable from current context, and form one coherent outcome the agent recommends as a unit. If the user asks to review all, enter a queue and present one item at a time, reconciling and reprioritising after each disposition.

Wait for the user to signal they are done answering before moving on — a response to one question does not mean the user has nothing more to say.

### Deferred questions and research

The user may:
- **Defer a question** — mark it as deferred and move to the next highest-impact question. Return to it later.
- **Need to answer another question first** — reorder on the fly. The agent follows the user's lead.
- **Need research to answer** — allocate a background subagent to research while shaping continues on other questions. When research completes, return to the deferred question with findings.

### Scope expansion

A shaping session may discover that additional artefacts are involved. When this happens:
- Add the new source to the transcript's source line
- Add the transcript link to the new source's `**Transcripts:**` line
- Apply the answer that introduces the source, and every subsequent answer, to each in-scope source artefact whose body or decision state it affects
- Treat the active transcript's source line as canonical session-scope metadata. On resumption, re-read every listed source; this is not event replay.
- If the new source already has another linked transcript for the day, keep that history intact. The active transcript becomes the sole joint-continuation owner. Session bootstrap chooses the linked transcript with the widest distinct declared source-link set, counting archived or unresolved links, and rejects a tie rather than merging or copying event streams.
- Continue shaping — the transcript can serve multiple related artefacts

### Convergent shaping process

Each question references the decision(s) it relates to (e.g. `Q3 — D2 + D4: Ownership boundary`). Questions and decisions are separate tracking concerns — one question may resolve multiple decisions, or several questions may be needed for one decision.

Reconciliation is event-driven, not only answer-driven. Run it after session start or resumption, a user answer or correction, a research or agent-work result, a source addition, or an accepted four-Cs review finding. Reviewer output that the user has not accepted is candidate evidence, not shaping state. Run reconciliation before asking another question, reporting progress, handing off, or claiming completion.

For each trigger:

1. **Apply established content.** Update every affected body section and current-state table in every in-scope source artefact. A user statement that explicitly supplies an outcome is direct even if the active question did not name that decision; it closes the decision without duplicate confirmation. A merely implied outcome is derived and remains proposed.
2. **Sweep to a fixed point.** Re-read all remaining decisions and work. Narrow stale wording, expose new items, identify merges or splits, propose clear derived resolutions, and identify conversions to agent work. Repeat until the trigger produces no further consequence so one answer does not cause a cascade of checkpoints.
3. **Put evidence before preference questions.** Complete reasonably bounded shaping research, inventory, specification, or verification that could eliminate, narrow, or materially reframe a user decision, then reconcile its result. This is work on defining the artefact, not implementing it. Offer a research spike or mark work blocked when it is expensive, requires new authority, or cannot proceed.
4. **Respect and persist pending state.** Apply factual propagation and narrowing immediately. Keep inferred resolutions, merges, splits, supersessions, and conversions from an existing user decision to agent work pending until confirmed. Store each exact proposed transition in the owning table using state `proposed`, current context, and its reconciliation-event pointer; do not create split children or supersede rows before acceptance. On decline, restore the prior active state and retain a concise current-state cue so an unchanged proposal is not resurfaced. Do not write a pending result as normative body content.
5. **Record material audit events.** The transcript owns the full mutation history. Log semantic body propagation; decision, work, and candidate-prompt additions or transitions; direct decisions made; automatic resolutions or refinements; question retirement; and evidence-driven work completion — not copy-editing. Append a source-qualified `### Reconciliation Rn` event with trigger, event verbs and IDs, outcomes and authority, propagated sections, basis, confirmation, and next action as applicable. A reconciliation event does not require an adjacent user turn. Update the affected source artefacts to the resulting current state, but do not duplicate the full event there or in another temporal artefact. Later confirmation is a new event; history is not rewritten.
6. **Checkpoint proposals one commitment at a time.** Present applied changes informationally. For each proposal under review, explain the original uncertainty and impact, the basis for the clear winner, the recommendation, and its material consequence or trade-off. If that is insufficient, present genuinely viable alternatives as multiple choice with a recommendation. Never bundle proposals on a cold opening. During an active pass, offer an accept-all bundle only when it is one coherent, decision-ready outcome. “Review all” applies to the most recently presented explicit inventory and creates a sequential queue; never combine proposal and review-finding queues. Reconcile after every item because later proposals may narrow, reorder, or collapse. Accepted proposals apply. Every unaccepted proposal stays narrowed but open for individual review.
7. **Choose the next action.** Finish bounded agent work that could still collapse a choice. Otherwise ask only about a material choice with at least two genuinely viable outcomes where user judgement is missing. Prioritise dependency leverage, then material impact or irreversibility, then remaining ambiguity and conversational flow. If a tie remains, choose the item with the clearest next consequence and state why instead of asking the user to manage the queue.

**Decision authority.** A direct user statement is confirmation for the outcomes it explicitly supplies. An inferred product or preference outcome remains proposed until accepted through a reconciliation checkpoint or individual review. Agent work can be completed from evidence without preference confirmation, but converting an existing user decision to agent work goes through a checkpoint because it changes the agreed agenda. Agent work must not conceal a genuine product choice.

**Decision visibility.** After each turn, report user decisions separately from agent work, for example: "User decisions: 3 of 4 resolved; 1 proposed. Agent work: 2 of 5 complete." Never leave reclassified work in the user-decision denominator. When all user decisions and required agent work are complete, signal a provisional candidate exit and recommend review — do not claim final readiness before review or explicit bypass (see [Completing a Shaping Pass](#completing-a-shaping-pass)).

### Discovery shaping process

Discovery has no fixed decision list or predetermined amount of refinement. Its goal is to develop the artefact: expand useful information, refine and clarify existing material, and improve accuracy proportionately to the artefact's purpose.

Reconcile after every authoritative change: a user answer or correction, session resumption, evidence result, source expansion, or accepted review finding.

1. **Apply it throughout with the correct time model.** Update every affected section and in-scope source. Maintain the current picture in living artefacts by replacing or qualifying stale claims. Preserve bounded temporal records by adding dated context or provenance rather than rewriting history; place later current-state information in a suitable living artefact.
2. **Refresh a lightweight thread map.** Track what is covered, active or hinted, uncertain or potentially outdated, declined, and outside the user's present interest. This is not a compulsory decision table. Persist durable current-state cues in natural prose when forgetting them could cause a stale question on resumption; keep their mutation history in transcript reconciliation events.
3. **Reconcile possible prompts.** Retire directly or indirectly answered prompts, merge overlaps, narrow ambiguous prompts, and surface useful adjacent possibilities.
4. **Follow useful momentum.** Deepen when the user's latest answer shows energy or leaves salient material incomplete; broaden when an adjacent area would improve the artefact more. Make suggestions that support rather than redirect the user's flow.
5. **Verify proportionately.** Consult linked context and authoritative sources for time-sensitive facts when the artefact's purpose calls for current accuracy. Otherwise preserve uncertainty or ask whether verification is wanted.
6. **Audit material automatic refinement.** Record verbatim dialogue normally. Add a source-qualified `### Reconciliation Rn` transcript event for material reinterpretation; thread or candidate-prompt addition, reframing, merging, deferral, closure, or reopening; present-state correction; scope change; or multi-source propagation. The affected artefacts hold the resulting current content, not a duplicate event history. Do not log literal capture, formatting, or copy-editing merely for volume.
7. **Choose one next turn.** Ask one useful question, make one compact suggestion, or offer one navigation choice. Prefer an informative state delta over generic progress chatter.

A discovery conversation may pause whenever the user signals they are done; do not pressure them to satisfy a lifecycle bar. It reaches a completion candidate only when the agent has also checked that the taxonomy bar is met for the user's intended current scope. If the bar is not met, leave the status unchanged and make any remaining gap available for a later pass.

### Brainstorm shaping process

Brainstorm uses the same adaptive-consequence principle without premature decision machinery. After each authoritative change, apply it across affected content, refresh the working shape (purpose, users, boundaries, constraints, success, assumptions, unknowns, and approaches), retire or narrow covered prompts, complete bounded evidence work, and reprioritise. Follow productive user momentum when it remains useful.

Promote only mature trade-offs into `## Shaping Decisions` and agent-owned definition or verification into `## Shaping Work`. Record material propagation, scope changes, automatic narrowing, and promotions through reconciliation events. Apply established content directly; ask for approval only where a synthesis contains a material interpretation that could reasonably diverge from the user's intent.

## Completing a Shaping Pass

### Candidate exit

For refine, a candidate exit exists when all known user decisions and required agent work are resolved and the taxonomy bar is met. Proposed resolutions are not resolved for this purpose. For brainstorm, a candidate handoff exists when purpose, boundaries, and the decision/work surface are explicit enough for refine, including an explicitly empty agenda; brainstorm never sets the completion status. For discovery, a completion candidate requires both a willing conversation stop and the taxonomy bar; a user may pause before that point without pressure or status promotion.

### Recommended optional four-Cs review

At a candidate exit, state at a high level why the artefact meets its taxonomy bar, explain the four-Cs review in one concise sentence, and ask one procedural A/B/C question. Recommend A, the review. B skips review and performs the mode's completion action: set `{completion_status}` for transition, hand off brainstorm to refine, complete a preserved discovery pass with status unchanged, or apply a preserving type's declared completion status when exiting an existing `shaping` state. C stops without asserting pass completion and leaves lifecycle status unchanged. Name A an **independent review** only when a separate subagent is available; otherwise offer a **separate review**. Do not summarise artefact details or define each C unless asked. If the agent knows of further shaping, there is no candidate exit.

Track whether review began at `candidate-exit` or `mid-pass`. Honour an explicit mid-pass request without implying readiness: assess completeness relative to the known current stage rather than treating its open agenda as a defect. After reporting or reconciling accepted findings, return to the active workflow. Recommend review or present its named bypass only if reconciliation independently reaches a candidate exit.

If accepted, use a read-only subagent when available, with a clearly separated main-agent pass as fallback. Give the reviewer the current artefact, taxonomy bar, and directly related sources needed for verification, but not an intended verdict, earlier conclusions, prior dispositions, or proposed fixes.

Review the four Cs:

- **Correctness:** claims, constraints, links, examples, current-state information, and accepted outcomes are accurate and supported.
- **Clarity:** a fresh reader can understand the purpose, boundaries, terminology, and next use without the shaping conversation.
- **Consistency:** sections, decisions, related sources, terminology, lifecycle state, and provenance agree.
- **Completeness:** the mode-specific exit bar is met and no material gap, unresolved ambiguity, placeholder, or required agent work remains.

For brainstorm, completeness means purpose, boundaries, and the decision/work surface are explicit enough for refine, including an explicitly empty agenda. For discovery, it means the taxonomy bar is met for the user's intended current scope, without demanding exhaustive territory or treating permitted acknowledged uncertainty as a gap. For refine, use the taxonomy's completion bar.

For discovery artefacts, correctness primarily means faithful capture of the user's account. Verify external claims only when material to the artefact's purpose; preserve self-reported uncertainty and dated change. Treat unmarked contradiction as a consistency issue, but do not mistake legitimate evolution or plural perspectives for inconsistency.

Treat reviewer output as candidate evidence, not authoritative shaping state. The reviewer does not edit. The main agent validates and deduplicates it, assigns stable finding IDs such as `C1`, and classifies severity as `blocking` when the current bar cannot honestly be met, `material` when the refinement has meaningful user value, or `minor` when it is optional polish. Keep the same `Cn` when wording, evidence, or a partial fix changes but the underlying defect and affected contract remain the same; allocate a new ID for an independent defect. Group the result into:

- **No refinement needed** — report that and proceed;
- **Proposed fixes** — inventory corrections or clarifications, recommend the next fix or one coherent fix bundle, and ask for one commitment before applying it;
- **Further shaping suggested** — inventory gaps requiring judgement or exploration, recommend the highest-priority reopening, and ask one question about it.

When there are findings, present a compact high-level inventory, then drive one recommended next commitment. Prioritise blocking findings, then dependency leverage and material impact; use correctness and completeness to break ties before clarity polish. A bundle is valid only when its items are decision-ready and form one coherent correction. Otherwise begin with the highest-priority finding. “Review all” applies to that most recently presented inventory and creates a sequential queue whose remaining items are reconciled and reprioritised after each disposition.

> **Proposed fixes:** F1 …, F2 …
>
> **Possible further shaping:** S1 …
>
> I recommend starting with F1 because …. Apply F1, adjust it, or review the alternatives?

Apply only accepted review-originated changes. Only an accepted finding becomes a reconciliation trigger. Reconcile accepted changes through the active workflow and record their material body and agenda transitions in the transcript. Affected source artefacts hold the resulting current state. A declined finding remains a disposition in the verbatim transcript, not an outstanding shaping item, unless the user asks to retain it.

After declined findings, reassess the candidate exit against the validated evidence. If the remaining findings do not prevent the taxonomy bar from being met, recommend the named handoff or status and ask for exact confirmation. If a blocking finding remains true, do not claim readiness: recommend revisiting the highest-priority blocker or stopping without lifecycle promotion, one commitment at a time.

After accepted fixes from a candidate-exit review, assess the artefact again. When the known agenda is resolved and the self-check passes, recommend another four-Cs review with the same explicit finish or handoff bypass rather than a neutral finish/rerun/continue navigator. After substantive reopening or material new content, return to the active workflow and make the normal agent-led offer at its next candidate exit. A mid-pass review returns directly to the active workflow unless reconciliation reaches a candidate exit. Keep a rerun reviewer neutral, then deduplicate its output against stable IDs and earlier user dispositions. Do not resurface an unchanged declined finding.

### Closing the pass

When the current pass is ready and any accepted review findings are resolved, or the user chooses B:
1. **Brainstorm:** hand off to refine. Do not apply the completion status.
2. **Refine or transitioning discovery pass:** obtain exact status approval through B or a separate confirmation, then set the taxonomy's completion status.
3. **Preserved discovery pass:** record pass completion and leave an enduring status unchanged; if the artefact was already in `shaping`, apply its declared completion-status exit only with approval.
4. Close the transcript for this pass.
5. For convergent completion, signal: "Fully shaped — [artefact] is `{completion_status}`." For discovery, name either the completion status or the unchanged current status according to the taxonomy behaviour. Add revisitation language only for a living artefact; do not imply that later current-state changes should rewrite a bounded temporal record.

The artefact's lifecycle continues beyond shaping. The type's taxonomy defines subsequent statuses (e.g. `adopted` for ideas, `implemented` for designs).

## Transcript Conventions

Shaping transcripts follow the shaping-transcript taxonomy with these additions:
- **Naming:** `yyyymmdd-shaping-transcript~{Title}.md` in `_Temporal/Shaping Transcripts/yyyy-mm/`
- **Multi-source:** The `**Source:**` line lists all source artefacts, growing as scope expands
- **Verbatim dialogue:** `### Agent` and `### User` contain exact text, without inferred synthesis
- **Reconciliation:** source-qualified `### Reconciliation Rn` events are the authoritative mutation history. They record semantic propagation; decision, work, thread, and candidate-prompt transitions; direct outcomes and authority; automatic narrowing or refinement; proposals; evidence-driven work completion; and later confirmation. Affected sources contain the resulting current state rather than a duplicate log.
- **One file per day per artefact:** If shaping resumes later the same day, `shaping.start` follows the source artefact's transcript backlink and appends a new `## ... session start` heading. This preserves identity across source renames and avoids reusing a same-title transcript owned by another artefact.
- **ID scope:** `Qn`, `Rn`, and review-finding `Cn` sequences are transcript-scoped and monotonic across session headings and same-day resumptions. A session heading marks an interaction boundary; no correctness rule depends on an implicit pass namespace.

### Compatibility and migration

This transcript format is additive and forward-only. Existing shapeable artefacts remain valid without shaping-decision, shaping-work, or reconciliation-log sections; the active workflow normalises current state lazily when they are next shaped. Every existing dialogue-only transcript format remains valid, including pre-heading `Q.` / `> A.` turns and `### Agent` / `### User` turns. Begin reconciliation at `R1` when a new material event occurs. Never infer or backfill historical events from old dialogue.

Artefact-definition sync updates the taxonomy contract, not existing user artefact bodies or transcripts. No version-bound user-data migration, check remediation, or repair rewrite is required unless a future release makes a new field, heading, frontmatter value, or link shape mandatory. The absence of reconciliation events is valid legacy state; repair must never fabricate append-only audit history.
