# Hub Pattern

Some living artefact types act as hubs — containers that group related artefacts via nested tags. The pattern:

1. The hub file is a living artefact with a nested tag: `{type}/{slug}` (e.g. `project/my-app`, `journal/personal`)
2. All related artefacts (temporal or living) carry the same nested tag
3. The hub is the index; the tag is the query mechanism

This is useful when a stream of related work or content needs a single living touchpoint. The hub file describes the stream and links to key artefacts; the tag makes everything findable.

**Current examples:**
- **People** — `person/{slug}` groups observations and other artefacts related to a person
- **Projects** — `project/{slug}` groups plans, research, designs, logs, and other artefacts related to a project
- **Journals** — `journal/{slug}` groups journal entries belonging to a named journal stream
- **Workspaces** — `workspace/{slug}` groups brain artefacts related to a bounded working container (`_Workspaces/`). The workspace hub connects brain content to a freeform data folder of non-artefact files

**When to use:** When you need a living artefact that organises a collection of other artefacts (especially temporal ones) rather than containing content itself. If the living artefact is primarily content (like a wiki page or design doc), tags alone suffice — the hub pattern adds an explicit index file.

## Granularity

The hub is an interpreted summary — the current picture, distilled. Temporal artefacts are the evidence trail — what happened, when, in detail. Don't collapse both into one file.

When content arrives, spin it out into the right artefacts. A conversation about a project produces a transcript, observations, maybe a decision log — each in its own temporal file, each tagged with the hub's nested tag. The hub then reflects the current state, linking contextually to the evidence.

## Temporal Handshake

Tagged temporal artefacts feed their hub. When a temporal artefact changes the current picture — a new decision, a shifted preference, a corrected fact — distil the change into the hub body. The temporal preserves *when* you learned it; the hub reflects what's true *now*.

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

Hubs are natural elicitation points. Creating or updating a hub is a moment to notice what's missing and ask. This follows the core principle ([[.brain-core/index|Be curious, then capture]]):

- When creating a new hub, ask what the user knows that isn't in the initial input. Small, focused rounds — not an interrogation.
- When revisiting a sparse hub, notice the gaps and ask naturally.
- Capture answers as the appropriate temporal artefact (observations, shaping transcripts, idea logs), then update the hub.

The goal is a hub that gets richer over time through active curiosity, not one that stays frozen at the quality of its first draft.
