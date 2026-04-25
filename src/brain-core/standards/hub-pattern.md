# Hub Pattern

Some living artefact types become hubs because other artefacts gather around them. The hub is not a separate type. It is an ordinary living artefact with a stable artefact key that other artefacts can own or relate to.

The pattern:

1. The hub file is a living artefact with a canonical key: `{type}/{key}` (e.g. `project/my-app`, `person/alex`, `workspace/client-data`). See [[keys]] for the key contract.
2. Child artefacts persist ownership via `parent: {type}/{key}`. Living children also reflect it in owner-derived folders; temporal children stay in their date folders
3. Tags remain relationship signals only; they can connect temporal or living artefacts to the hub, but tooling must never infer ownership from tags alone

This is useful when a stream of related work or content needs a single living touchpoint. The hub file describes the stream and links to key artefacts. Ownership keeps living children structurally grouped; relationship tags and links keep the wider network findable.

**Current examples:**
- **People** — `person/{key}` can own living or temporal child artefacts and relate observations or other artefacts to a person
- **Projects** — `project/{key}` can own releases, designs, wiki pages, research, and other child artefacts; temporal children keep their normal date filing
- **Journals** — `journal/{key}` remains the named anchor for a journal stream; journal entries still use the `journal/{key}` relationship tag and may also persist canonical `parent` without changing their date-based filing
- **Workspaces** — `workspace/{key}` can own living or temporal child artefacts and connect brain content to a bounded working container (`_Workspaces/`)

**When to use:** Some types are hubs by design — `project`, `person`, `workspace`, `journal` exist specifically to anchor other artefacts. For those, the hub role is the point; create the hub first, let children accrue.

But hub behaviour isn't restricted to the designed ones. Any living artefact becomes a hub the moment another artefact names it as `parent`. Content-heavy types (designs, wiki pages, releases) can anchor children just as readily as organisational ones — the hub body's shape differs by type (a project hub reads like an index, a design hub reads like a document), but the ownership contract is identical. You don't need to redeclare a type as "hub-capable" to let this happen; it's available by default.

Tags are not a substitute. Tags signal relationship (this touches that); `parent` signals ownership (this belongs to that). Use both, for different reasons.

## Granularity

The hub is an interpreted summary — the current picture, distilled. Temporal artefacts are the evidence trail — what happened, when, in detail. Don't collapse both into one file.

When content arrives, spin it out into the right artefacts. A conversation about a project produces a transcript, observations, maybe a decision log — each in its own temporal file, typically tagged to relate it to the hub. The hub then reflects the current state, linking contextually to the evidence.

## Temporal Handshake

Related temporal artefacts feed their hub. When a temporal artefact changes the current picture — a new decision, a shifted preference, a corrected fact — distil the change into the hub body. The temporal preserves *when* you learned it; the hub reflects what's true *now*.

Not every temporal triggers an update. A routine log entry doesn't change the project summary. But a decision that alters scope does. Use judgement.

## Contextual Linking

Weave links into prose where they add depth. The reader clicks through for evidence, not to understand the summary.

Good:
```
Scope narrowed after the [[decision-log|March architecture review]]. Currently focused on [[plan|the API-first milestone]].
```

Bad:
```
- 2026-03-20 — See [[20260320-decision--architecture-review]]
- 2026-03-22 — See [[20260322-plan--api-first-milestone]]
```

Link text should read naturally as part of the sentence. Changelog-style lists lose the interpretive layer that makes hubs valuable.

## Ingestion

Match the effort to the input. A name and a sentence creates a minimal hub — don't interrogate. A rich dump decomposes into artefacts.

### Minimal input → minimal hub, no fuss

If the user gives you basic info, create the hub immediately with what you have. Leave empty sections empty. Don't ask clarifying questions unless something is genuinely ambiguous. Look for a natural opportunity to expand later — ask a single question at most, or just do it.

### Rich input → decompose into artefacts

When receiving a dump of information about a hub's subject (a project brief, everything someone knows about a person, a journal-worthy reflection), decompose before writing:

1. **Identify artefact types** — what's a durable fact (hub body), what's a discrete observation, what's an idea, what's a timeline event?
2. **Create temporals first** — spin out observations, idea logs, decision logs, entries. This gives you links to weave in.
3. **Write or update the hub** — interpreted summary referencing the evidence. Not a raw dump.

Each hub type's taxonomy specifies its own decomposition rules. The principle is universal: temporals first, hub second.

## Elicitation

Hubs are natural elicitation points. Creating or updating a hub is a moment to notice what's missing and ask. This follows the core principles in [session-core.md](../session-core.md), especially “Actively seek signal”:

- When creating a new hub, ask what the user knows that isn't in the initial input. Small, focused rounds — not an interrogation.
- When revisiting a sparse hub, notice the gaps and ask naturally.
- Capture answers as the appropriate temporal artefact (observations, shaping transcripts, idea logs), then update the hub.

The goal is a hub that gets richer over time through active curiosity, not one that stays frozen at the quality of its first draft.
