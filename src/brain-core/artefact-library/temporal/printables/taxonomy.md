# Printables

Temporal artefact. Paginated PDF documents generated from markdown content using Pandoc.

## Purpose

A printable turns vault knowledge into a document-shaped PDF — brief, memo, handout, one-pager, leave-behind, or report-style summary. The markdown source is the artefact; the PDF is output. Use a printable when the audience needs a page-based document rather than a slide deck.

Printables bridge the gap between working knowledge and shareable documents. They draw from existing artefacts (designs, research, reports, wiki pages) and reshape that material into a format that reads well on paper or as a conventional PDF.

## How to Write Printables

- **Link to the source.** Every printable should reference the artefact(s) it comes from. Follow [[.brain-core/standards/provenance]].
- **Write for pages, not slides.** Use paragraphs, headings, lists, and short sections. The reader can sustain more detail than in a presentation.
- **Generated output lives under `_Assets/Generated/Printables/`.** The normal output is `_Assets/Generated/Printables/{stem}.pdf`. If one printable workflow produces multiple linked files, use the owning artefact's canonical key and use `_Assets/Generated/Printables/{OwnerType}~{owner-key}/`.
- **Regenerate PDF after edits.** The markdown is the source of truth. Always rerender the PDF when content changes.
- **Use heading-break control when needed.** `keep_heading_with_next: true` reduces orphaned headings at page breaks by reserving space before new sections. Disable it for dense layouts that need tighter pagination.

## Naming

`yyyymmdd-printable~{Title}.md` in `_Temporal/Printables/yyyy-mm/`.

Example: `_Temporal/Printables/2026-04/20260416-printable~Q2 Board Brief.md`

## Frontmatter

```yaml
---
type: temporal/printable
tags:
  - printable
keep_heading_with_next: true
---
```

No lifecycle. Optional `status: shaping` or `status: ready` when shaping is active or complete.

## Shaping

**Flavour:** Convergent
**Bar:** The narrative is coherent, the structure suits the audience, and the document is ready to render without major layout surprises.
**Completion status:** `ready`

See [[.brain-core/standards/shaping]] for the shaping process.

## Trigger

When converting a vault artefact into a page-based PDF document — brief, memo, handout, one-pager, leave-behind, or printable summary.

## Template

[[_Config/Templates/Temporal/Printables]]
