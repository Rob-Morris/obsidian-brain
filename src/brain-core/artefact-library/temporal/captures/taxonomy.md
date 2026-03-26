# Captures

Temporal artefact. External material ingested into the vault verbatim.

## Purpose

A capture preserves external material exactly as received — emails, meeting notes, Slack threads, data extracts, documents. Captures are frozen on ingest: never summarised, restructured, or edited after creation. They serve as source material that downstream artefacts (wiki pages, designs, plans) can link back to.

## How to Write Captures

- **Preserve the original.** Don't summarise, restructure, or clean up. The value is in having the raw material.
- **Note the source.** Use the `source` frontmatter field to record where the material came from (e.g. "Email from James Ward", "Slack #ops channel", "Q3 revenue extract").
- **Link for context.** Include at least one wikilink in the body to connect the capture to a relevant vault artefact.
- **Never edit after creation.** Captures are immutable. If you need to annotate or respond to the material, create a separate artefact and link back. Follow [[.brain-core/standards/provenance]].

## Naming

`yyyymmdd-capture~{Title}.md` in `_Temporal/Captures/yyyy-mm/`.

Example: `_Temporal/Captures/2026-03/20260325-capture~James Ward API Feedback.md`

## Frontmatter

```yaml
---
type: temporal/capture
source:
tags:
  - capture
---
```

No status field. Captures have no lifecycle — they are immutable once created.

## Trigger

When ingesting external material into the vault — emails, meeting transcripts, Slack threads, data extracts, or any content not originally written in the vault.

## Template

[[_Config/Templates/Temporal/Captures]]
