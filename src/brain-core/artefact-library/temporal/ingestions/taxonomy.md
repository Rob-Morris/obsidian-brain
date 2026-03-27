# Ingestions

Temporal artefact. A record of an ingestion event — how content was analysed, decomposed, and absorbed into the vault.

## Purpose

An ingestion is the processing receipt for content entering the brain. It links to a [[_Config/Taxonomy/Temporal/captures|capture]] that holds the raw source material, then records agent enrichment and analysis, documents the thread-by-thread breakdown, and links to every artefact created from that source. Once complete, it becomes the authoritative record of how a piece of content was decomposed and integrated into the vault.

## Relationship to captures

Every ingestion references a capture. The capture holds the immutable raw content; the ingestion holds the processing. This is a clean separation:

- **Capture** = the content itself (a noun). Frozen, immutable, always available as the source of truth for what was received.
- **Ingestion** = the processing record (a verb). What the agent understood, how it decomposed the content, what it created.

The pipeline always creates the capture first, then the ingestion record. This means:
- Simple content that doesn't need decomposition just gets a capture — no ingestion record needed. The capture IS the artefact.
- Complex content gets a capture (raw material) AND an ingestion record (processing). The ingestion links to the capture rather than duplicating the content.
- The capture persists as a permanent reference regardless of what processing happens on top of it.

## Structure

An ingestion has three sections, populated in order as processing progresses:

1. **Enrichment** — agent analysis of the captured content. Context, interpretation, background research, connections to existing vault content. This is where the agent adds value — identifying what's significant, noting relevant existing artefacts, surfacing implicit context. Written before decomposition begins — understand the content holistically before breaking it apart.

2. **Thread inventory** — the systematic breakdown of distinct threads found in the content. Each thread is identified, classified (maps to existing artefact type, new artefact, deferred), and given a disposition. This section implements Step 6c of the Brain Inbox processing pipeline.

3. **Artefacts created** — links to everything spun out from the ingestion, populated as artefacts are created. Includes deferred threads with reasoning for why they weren't actioned. When processing is complete, this section is the map from source material to vault artefacts.

## How to Write Ingestions

- **Create the capture first.** The raw content goes into a capture artefact before the ingestion record is created. Follow the "save before acting" principle.
- **Link to the capture.** The ingestion's body opens with a wikilink to the source capture. This is the provenance chain.
- **Enrich before decomposing.** The enrichment section should be written before the thread inventory — understanding the content holistically before breaking it apart.
- **Account for every thread.** The thread inventory must cover all distinct ideas, questions, observations, and action items in the source — not just the obvious ones. Deferred threads are listed with reasoning.
- **Link bidirectionally.** Every artefact created from the ingestion gets an Origin link back to the ingestion. The ingestion links forward to all created artefacts. The capture gets a spin-out callout pointing to the ingestion.

## Naming

`yyyymmdd-ingestion~{Title}.md` in `_Temporal/Ingestions/yyyy-mm/`.

Example: `_Temporal/Ingestions/2026-03/20260327-ingestion~ Brain Project Directions Voice Memo.md`

## Frontmatter

```yaml
---
type: temporal/ingestion
tags:
  - ingestion
---
```

## Trigger

When ingesting content into the vault that contains enough substance to warrant decomposition — multi-topic brain dumps, voice memos, documents with multiple threads. Simple, single-topic captures don't need an ingestion record; the capture alone (or direct artefact creation) suffices.

## Template

[[_Config/Templates/Temporal/Ingestions]]
