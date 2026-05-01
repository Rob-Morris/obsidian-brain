# Presentations

Temporal artefact. Slide decks generated from markdown content using Marp CLI.

## Purpose

A presentation turns vault knowledge into a visual deck — status updates, proposals, walkthroughs, retrospectives. The markdown source is the artefact; the PDF is output. Marp CLI renders markdown to slides with live preview, so the agent can iteratively shape the deck while the user watches.

Presentations bridge the gap between working knowledge and communicable form. They draw from existing artefacts (designs, research, reports) and distil them into a structured narrative with visual hierarchy.

## How to Write Presentations

- **Link to the source.** Every presentation should reference the artefact(s) it presents. Follow [[.brain-core/standards/provenance]].
- **Sparse slides.** One idea per slide. Use headings, bullet points, and whitespace. Don't cram.
- **Generated output lives under `_Assets/Generated/Presentations/`.** The normal output is `_Assets/Generated/Presentations/{stem}.pdf`. If one presentation workflow produces multiple linked files, use the owning artefact's `scope` (the tokenised form of its canonical key) and use `_Assets/Generated/Presentations/{scope}/`.
- **Use the theme.** The Brain theme provides title slides, callout classes, risk colours, and card layouts. See the skill reference for available CSS classes.
- **Regenerate PDF after edits.** The markdown is the source of truth. Always regenerate the PDF when slides change.

## Naming

`yyyymmdd-presentation~{Title}.md` in `_Temporal/Presentations/yyyy-mm/`.

Example: `_Temporal/Presentations/2026-03/20260325-presentation~Q1 Security Review.md`

## Frontmatter

```yaml
---
type: temporal/presentation
tags:
  - presentation
---
```

No lifecycle. Optional `status: shaping` or `status: ready` when shaping is active or complete.

## Shaping

**Flavour:** Convergent
**Bar:** Content and structure are clear.
**Completion status:** `ready`

See [[.brain-core/standards/shaping]] for the shaping process.

## Trigger

When creating a slide deck or presentation from vault content — status updates, proposals, walkthroughs, retrospectives, or any content that needs to be communicated visually.

## Template

[[_Config/Templates/Temporal/Presentations]]
