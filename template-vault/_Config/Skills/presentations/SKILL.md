---
name: presentations
---

# Presentations

Create slide decks from vault content using Marp CLI.

## Session Start

1. Read the presentations taxonomy: `brain_read(resource="type", name="presentation")`
2. Check Marp is installed: `marp --version`
   - Install if missing: `npm install -g @marp-team/marp-cli`
3. Locate the theme: `_Config/Skills/presentations/theme.css`

## Shaping Workflow

Use `brain_action("shape-presentation", params={"source": ..., "slug": ...})` to create the presentation artefact, render its PDF, and launch a live preview:

1. **Call the tool.** `source` is the vault artefact to present (relative path). `slug` is the deck name (lowercase-hyphenated).
2. **The tool creates** the presentation file from the template if it does not already exist.
3. **The tool renders** `_Assets/Generated/Presentations/{stem}.pdf` with Marp and the Brain theme.
4. **The tool launches** `marp --preview` with live reload unless `preview: false` is passed.
5. **Iterate on slides.** Read the source artefact, write slides to the presentation file. The browser updates automatically as you save. Ask the user for feedback — they can see changes in real time.
6. **Regenerate the PDF after edits.** Rerun the tool to refresh `_Assets/Generated/Presentations/{stem}.pdf` once the markdown changes.

Optional params:

- `render: false` — create or reopen the markdown artefact without regenerating the PDF yet
- `preview: false` — render the PDF without launching a fresh preview window

The shaping workflow is interactive: you write, the user watches, you refine together.

## Manual Workflow

For environments without live preview (remote, mobile, headless):

1. Create the presentation artefact manually using the template
2. Write slides in markdown with `---` separators
3. Generate PDF: `marp {path} --theme _Config/Skills/presentations/theme.css -o _Assets/Generated/Presentations/{stem}.pdf`

## Conventions

- **Markdown is the artefact.** The `.md` file in `_Temporal/Presentations/` is what gets tracked, linked, and searched. The PDF is output.
- **Generated output lives under `_Assets/Generated/Presentations/`.** Use `{stem}.pdf` for the normal single-file output. If one presentation workflow produces multiple linked files, use the owning artefact's `scope` (the tokenised form of its canonical key) and nest those files under `_Assets/Generated/Presentations/{scope}/`.
- **Link provenance.** Every presentation should reference its source artefact(s) via `**Origin:**` links.
- **Sparse slides.** One idea per slide. Headings, bullets, whitespace. Don't cram.
- **Regenerate PDF after edits.** If you change the markdown, regenerate the PDF. The markdown is the source of truth.
- **Slide separators.** Use `---` on its own line to separate slides.

## Theme Reference

The Brain theme (`theme.css`) provides these CSS classes:

| Class | Usage | Effect |
|---|---|---|
| `title` | `<!-- _class: title -->` | Dark navy background, white text, teal accent. Use for the first slide. |
| `warning` | `<div class="warning">...</div>` | Red left border, tinted background |
| `info` | `<div class="info">...</div>` | Blue left border, tinted background |
| `caution` | `<div class="caution">...</div>` | Amber left border, tinted background |
| `risk-critical` | `<span class="risk-critical">...</span>` | Red bold text |
| `risk-high` | `<span class="risk-high">...</span>` | Orange bold text |
| `risk-medium` | `<span class="risk-medium">...</span>` | Amber semibold text |
| `cards` | `<div class="cards">...</div>` | CSS grid card layout |

### Marp directives

- `<!-- _class: title -->` — apply title class to current slide
- `<!-- _paginate: false -->` — hide page number on current slide
- `<!-- _backgroundColor: #f0f0f0 -->` — per-slide background override
