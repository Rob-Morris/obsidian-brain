# Journal Entries

Temporal artefact. Personal journal entries.

## Purpose

A journal entry captures personal reflections, recollections, and life updates — things happening in the user's life, not work activity. Each entry belongs to a journal stream via the `journal/{slug}` nested tag.

Journal entries are distinct from other temporal types:

| Type | What it captures | Scope |
|---|---|---|
| **Logs** | Work activity timeline | Work |
| **Daily Notes** | Work summary digest | Work |
| **Thoughts** | Fleeting fragments | Any (raw, brief) |
| **Journal Entries** | Personal reflections, life updates | Life (developed, user's voice) |
| **Transcripts** | Conversation records | Any (raw exchange) |

Logs = what you did. Journal = who you are and what's happening.
Thoughts = a sentence or question. Journal entries = developed reflection.
Transcripts = the conversation that may have produced a journal entry.

## Workflows

Four ways to create journal entries:

### 1. Casual sharing ("talking to a friend")

User chats with agent about their life. Agent records what the user says as a journal entry — capturing the user's own words, not paraphrasing. Agent responds naturally throughout (that conversation is a separate transcript if worth preserving). The journal entry contains what the user shared; the transcript contains the full exchange.

### 2. Directed creation

User asks agent to create an entry and tells them what to write. Agent writes exactly what was said.

### 3. Shaping/drafting

Agent interviews user, helps draft the entry. User approves edits through ongoing shaping conversation. This is a drafting process — the journal entry evolves through conversation.

### 4. Manual

User writes directly, no agent involvement.

## Voice

Journal entries are always in the user's own words unless the user explicitly asks the agent to write on their behalf. When capturing from conversation, preserve the user's language and phrasing — do not paraphrase, summarise, or polish.

## Naming

`yyyymmdd-journal~{Title}.md` in `_Temporal/Journal Entries/yyyy-mm/`.

The title is the topic or subject of the entry, in the same sense as `{Title}` for research or report artefacts. The journal stream is carried by the `journal/{slug}` tag, not the filename — scope by tag when browsing a stream.

If an entry has no specific topic, title it for the broad theme of the reflection (e.g. `Evening Reflection`, `Quiet Thoughts`) rather than the journal stream.

Examples:

- `20260322-journal~Moving House.md` (tagged `journal/personal`)
- `20260322-journal~Knee Rehab.md` (tagged `journal/health`)
- `20260322-journal~Evening Reflection.md` (general entry, tagged `journal/personal`)

> Folder-scoping temporal entries by their parent hub (e.g. `_Temporal/Journal Entries/{journal-slug}/yyyy-mm/`) is a deliberate deferred question. See [[Hub Slug Folder Scoping For Temporal Artefacts]].

## Frontmatter

```yaml
---
type: temporal/journal-entry
tags:
  - journal-entry
  - journal/{journal-slug}
---
```

The `journal/{journal-slug}` tag identifies which journal stream the entry belongs to. Substitute the stream slug at create time (e.g. `journal/personal`, `journal/health`).

No lifecycle. Optional `status: shaping` or `status: ready` when shaping is active or complete.

## Shaping

**Flavour:** Discovery
**Bar:** User has nothing more to add.
**Completion status:** `ready`

See [[.brain-core/standards/shaping]] for the shaping process.

## Distillation

Journal entries are source material. Over time, patterns across entries might feed into living artefacts (wiki pages about life topics, writing projects, etc.) following [[.brain-core/standards/provenance]]. But there is no prescribed graduation path — journal entries are valuable as-is.

## Trigger

When the user wants to journal, reflect on their life, or share something personal — create a journal entry.

## Template

[[_Config/Templates/Temporal/Journal Entries]]
