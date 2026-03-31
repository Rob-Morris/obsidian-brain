# People

Living artefact. Person index files.

## Purpose

One file per person, serving as the living source of truth for what you know about them. The person file is the hub — observations, journal entries, transcripts, and other artefacts link back to it via the person tag. Updated as you learn new things; superseded facts are replaced, not accumulated.

## Naming

`{Title}.md` in `People/`.

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
| `parked` | No longer in regular contact. Preserved for reference. |

## Observation Handshake

Observations tagged `person/{slug}` feed this card. When an observation changes the current picture — a preference shifted, a fact corrected, something new learned — distil it into the body. The observation preserves *when* you learned it; the person card reflects what's true *now*.

## Ingestion

Match the effort to the input. Don't ask unnecessary questions — just create what you can and grow it later.

### Minimal input → minimal card, no fuss

If the user gives you basic info about a person, create the card immediately with what you have. Leave empty sections empty. Don't ask clarifying questions unless something is genuinely ambiguous (e.g. you can't tell which name to use for the filename). Look for a natural opportunity to expand — ask a single question at most, or just do it.

### Rich input → decompose into artefacts

If the user dumps a narrative brief with lots of detail, decompose:

- **Durable facts** (identity, relationships, traits) → the person card body
- **Discrete things learned** (terminology, quotes, preferences) → observations, tagged `person/{slug}`
- **Actionable sparks** (ideas triggered by the interaction) → idea logs, tagged `person/{slug}`
- **Timeline** → log entry

### Writing the card

The person card is an interpreted summary, not a raw dump. Each section should read as a concise brief on the current picture:

- **Who** — identity, what they do, defining traits. Interpreted, not quoted.
- **Relationship** — how you know each other, the nature of the connection. Capture the vibe, not just the facts.
- **Opportunities** — active threads, things you might do together, what's in play. Forward-looking.

### Contextual linking

Don't list temporal artefacts as changelog entries. Weave links into the prose where they add depth. The link text should read naturally as part of the sentence — the reader clicks through for evidence, not to understand the summary.

Good: `[[observation|Interested in the brain system]]. Up for collaborating on a [[idea-log|podcast or screencast demo]].`

Bad: `- 2026-03-26 — See [[observation]] and [[idea-log]].`

### Create temporals first when decomposing

When there's rich input to decompose, spin out observations, idea logs, and other temporals *before* writing the person card. This ensures you have links to weave in, and forces you to separate raw evidence from interpreted summary.

The person card is the hub. Temporal artefacts are the evidence trail.

## Template

[[_Config/Templates/Living/People]]
