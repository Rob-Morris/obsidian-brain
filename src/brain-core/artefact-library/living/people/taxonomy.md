# People

Living artefact. Person index files.

## Purpose

One file per person, serving as the living source of truth for what you know about them. The person file is the hub — observations, journal entries, transcripts, and other artefacts link back to it via the person tag. Updated as you learn new things; superseded facts are replaced, not accumulated.

## Naming

`{name}.md` in `People/`.

Example: `People/Alice Smith.md`

## Frontmatter

```yaml
---
type: living/person
tags:
  - person/{slug}
status: active
---
```

Every file related to a person should use the nested person tag, e.g. `person/alice-smith`.

## Lifecycle

| Status | Meaning |
|---|---|
| `active` | Default. Actively maintained. |
| `archived` | No longer in regular contact. Preserved for reference. |

## Observation Handshake

Observations tagged `person/{slug}` feed this card. When an observation changes the current picture — a preference shifted, a fact corrected, something new learned — distil it into the body. The observation preserves *when* you learned it; the person card reflects what's true *now*.

## Ingestion

When creating a person card from a narrative brief (a user dumping everything they know about someone in one go):

### 1. Decompose into artefacts

- **Durable facts** (identity, relationships, traits) → the person card body
- **Discrete things learned** (terminology, quotes, preferences) → observations, tagged `person/{slug}`
- **Actionable sparks** (ideas triggered by the interaction) → idea logs, tagged `person/{slug}`
- **Timeline** → log entry

### 2. Write the card as a summary report

The person card is an interpreted summary, not a raw dump. Each section should read as a concise brief on the current picture:

- **Who** — identity, what they do, defining traits. Interpreted, not quoted.
- **Relationship** — how you know each other, the nature of the connection. Capture the vibe, not just the facts.
- **Opportunities** — active threads, things you might do together, what's in play. Forward-looking.

### 3. Link contextually to temporal artefacts

Don't list temporal artefacts as changelog entries. Instead, weave links into the prose where they add depth. The link text should read naturally as part of the sentence — the reader clicks through for evidence, not to understand the summary.

Good: `[[observation|Interested in the brain system]]. Up for collaborating on a [[idea-log|podcast or screencast demo]].`

Bad: `- 2026-03-26 — See [[observation]] and [[idea-log]].`

### 4. Create temporals first, then write the card

Spin out observations, idea logs, and other temporals *before* writing the person card. This ensures you have links to weave in, and forces you to separate raw evidence from interpreted summary.

The person card is the hub. Temporal artefacts are the evidence trail.

## Template

[[_Config/Templates/Living/People]]
