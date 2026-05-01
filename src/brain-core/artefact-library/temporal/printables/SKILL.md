---
name: printables
---

# Printables

Create page-based PDF documents from vault content using Pandoc.

## Session Start

1. Read the printables taxonomy: `brain_read(resource="type", name="printable")`
2. Check Pandoc is installed: `pandoc --version`
3. The renderer auto-detects `xelatex`, `lualatex`, or `pdflatex` for PDF output
4. If the binaries are not on `PATH`, set explicit paths in `.brain/local/config.yaml` under `defaults.tool_paths` or via `BRAIN_PANDOC_PATH`, `BRAIN_XELATEX_PATH`, `BRAIN_LUALATEX_PATH`, `BRAIN_PDFLATEX_PATH`
5. Locate the support files: `_Config/Skills/printables/base.tex` and `_Config/Skills/printables/keep-headings.tex`

## Shaping Workflow

Use `brain_action("shape-printable", params={"source": ..., "slug": ...})` to create the printable artefact and render its PDF:

1. **Call the tool.** `source` is the vault artefact to convert (relative path). `slug` is the printable name.
2. **The tool creates** the printable markdown file from the template if it does not already exist.
3. **The tool renders** `_Assets/Generated/Printables/{stem}.pdf` using Pandoc.
4. **Iterate on the markdown.** Refine the printable file until the document reads cleanly on the page, then rerun the tool to regenerate the PDF.

Optional params:

- `render: false` — create or reopen the markdown artefact without rendering a PDF yet
- `keep_heading_with_next: false` — disable the heading/page-break helper for this render
- `pdf_engine: xelatex|lualatex|pdflatex` — force a specific PDF engine

## Manual Workflow

For environments without MCP, run the script directly:

```bash
python3 .brain-core/scripts/shape_printable.py --source "Wiki/topic.md" --slug "board-brief"
```

## Conventions

- **Markdown is the artefact.** The `.md` file in `_Temporal/Printables/` is what gets tracked, linked, and searched. The PDF is output.
- **Generated output lives under `_Assets/Generated/Printables/`.** Use `{stem}.pdf` for the normal single-file output. If one printable workflow produces multiple linked files, use the owning artefact's `scope` (the tokenised form of its canonical key) and nest those files under `_Assets/Generated/Printables/{scope}/`.
- **Tool resolution is explicit-path first.** `shape-printable` checks machine-local overrides from `.brain/local/config.yaml` and `BRAIN_*` env vars before falling back to the host `PATH`.
- **Link provenance.** Every printable should reference its source artefact(s) via `**Origin:**` links.
- **Write for pages.** Paragraphs and short sections are expected. This is not slide content.
- **Regenerate PDF after edits.** If you change the markdown, rerender the PDF. The markdown is the source of truth.
- **Use heading break control deliberately.** `keep_heading_with_next` helps prevent orphaned headings by reserving space before new sections. Turn it off when tighter pagination matters more than heading grouping.
