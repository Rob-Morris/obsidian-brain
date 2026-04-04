# Shaping

Shaping is the iterative process of refining an artefact through structured Q&A until it meets its type's bar for clarity and completeness. Shaping produces two things: an artefact that is ready to act on, and a transcript that records how it got there.

## Shapeable Artefacts

A type is shapeable if:
1. Its schema includes `shaping` as a status value
2. Its taxonomy defines a `## Shaping` section specifying:
   - What "fully shaped" means for this type (the bar)
   - What status to transition to when shaping completes (e.g. `ready`)

Each type owns its definition of "fully shaped." The bar should be concrete enough that an agent can judge whether the artefact meets it. Examples:

- **Ideas:** Clear *what* the idea is and *why* it matters. Open questions about *how* are fine — that's design territory.
- **Designs:** All decisions resolved, core goal clear, approach concrete enough to plan, no internal inconsistencies.
- **People:** Nothing more the user wants to record right now — shaping is a discovery process teasing out useful information.

Types opt in by adding `shaping` to their status enum and a `## Shaping` section to their taxonomy.

## Shaping Flavours

Not all shaping works the same way. The process adapts to the artefact's nature:

- **Convergent shaping** drives toward specific decisions. The decision list is the primary tracking mechanism, and shaping completes when all decisions are resolved and the artefact is internally consistent. Examples: Designs, Plans, Tasks.

- **Discovery shaping** teases out information the user wants to capture. There may not be a fixed decision list — the agent explores the topic, asking questions to surface what's worth recording. Shaping completes when the user has nothing more to add. Examples: People, Journal Entries, Cookies.

Most artefacts lean one way, but a session can blend both — a design might start with discovery (what are we even designing?) before converging on decisions. The type's `## Shaping` section should indicate which flavour is primary.

**Convergent types** must include a decisions table in their template (e.g. Open Decisions) so shaping state is trackable in the artefact. **Discovery types** don't need one — the artefact grows with each answer and shaping completes when there's nothing more to capture.

## Starting a Shaping Session

Use `brain_action("start_shaping", {target, ...})` to commence shaping. This handles the mechanical setup:

1. **Identifies or creates the artefact** — finds an existing artefact by name/path, or creates a new one in `shaping` status
2. **Creates the transcript** — linked to the artefact, with provenance in both directions
3. **Sets status** — moves the artefact to `shaping` if not already there

Sometimes shaping begins before the user knows what they're shaping. In this case, the first questions are exploratory — identifying the artefact type and creating it is part of the process. The agent can call `start_shaping` once the target is clear.

### Source linking

The transcript's first body line identifies all source artefacts:
```
Shaping transcript for [[Artefact1|Title1]].
```

As shaping expands to touch additional artefacts, append them:
```
Shaping transcript for [[Artefact1|Title1]], [[Artefact2|Title2]].
```

Each source artefact links back via `**Transcripts:** [[transcript|Session]]` — see [[.brain-core/standards/provenance]].

### Shaping state lives in the artefact

The artefact is the source of truth for where shaping stands — its content, open questions, and decision table (for convergent types). Previous transcripts are reference material the agent *can* consult for context (e.g. "why was this decided?") but the artefact should be self-sufficient. An agent picking up shaping in a new session reads the artefact, not the transcript history.

### Setting the agenda

Review the artefact's current state and identify what needs to be decided. If the artefact is new and blank, the first question establishes what this is about.

## During Shaping

### One question per turn

Each turn asks one numbered question (Q1, Q2, …). The next question is chosen after each answer based on what has highest impact and flows naturally from the conversation — not by walking a pre-made list in order.

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
- Continue shaping — the transcript can serve multiple related artefacts

### Convergent shaping process

Each question references the decision(s) it relates to (e.g. "Q3 [D2, D4]"). Questions and decisions are separate tracking concerns — one question may resolve multiple decisions, or several questions may be needed for one decision.

After the user answers:

1. Update the artefact to reflect what was decided
2. Update the decision list (resolve decided items, add newly discovered ones) — **only the user closes decisions**. If research or reasoning leads to a conclusion, present it and confirm before marking resolved.
3. Record the Q&A in the transcript
4. Choose the next question (see [[#One question per turn]])

**Decision visibility.** After each turn, show the user the decision list with resolved/open status and count (e.g. "3 of 5 decisions resolved"). When all known decisions are resolved, signal that review is next — do not assume shaping is done (see [[#Completing Shaping]]).

### Discovery shaping process

There is no fixed decision list. The agent asks questions to surface what the user wants to capture, exploring the topic to tease out useful information.

After the user answers:

1. Incorporate the answer into the artefact's content
2. Record the Q&A in the transcript
3. Choose the next question (see [[#One question per turn]])

**Progress signalling.** After each turn, signal where things stand:
- "Still exploring — more to capture?" when the topic feels open
- "Anything else?" when the conversation seems to be winding down

Shaping completes when the user signals they have nothing more to add. The agent should then review for completeness (see [[#Completing Shaping]]) before declaring the artefact fully shaped.

## Completing Shaping

### Don't assume done

When all known questions are resolved, do not declare the artefact fully shaped. Instead, enter a review phase.

### Completion review

Review the artefact against its type's bar:
- **Internal consistency:** Do all parts agree with each other?
- **Completeness:** Does the artefact meet its type's fully-shaped bar?
- **Clarity:** Would someone reading this for the first time understand it?
- **Missing links:** Are provenance and transcript links in place?

Present a summary of potential gaps to the user:

> **Review found X potential gaps:**
> 1. [gap summary]
> 2. [gap summary]
>
> **Do any of these need more shaping?**

Only the gaps the user flags become new shaping questions. The rest are dismissed. Do not resume shaping or update the artefact without confirmation. If no gaps are found, proceed to declaring fully shaped.

### Declaring fully shaped

When the review passes:
1. Set the artefact's status to its type's completion status (e.g. `ready` for designs)
2. Close the transcript (no more Q&A appended)
3. Signal to the user: "Fully shaped — [artefact] is ready"

The artefact's lifecycle continues beyond shaping. The type's taxonomy defines subsequent statuses (e.g. `adopted` for ideas, `implemented` for designs).

## Transcript Conventions

Shaping transcripts follow the shaping-transcript taxonomy with these additions:
- **Naming:** `yyyymmdd-shaping-transcript~{Title}.md` in `_Temporal/Shaping Transcripts/yyyy-mm/`
- **Multi-source:** First line lists all source artefacts, growing as scope expands
- **One transcript per session:** If shaping resumes in a new session, create a new transcript (append to the artefact's `**Transcripts:**` list)
