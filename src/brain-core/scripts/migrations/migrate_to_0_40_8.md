# Migration v0.40.8 — Duplicate Artefact Frontmatter Repair

Repairs artefacts whose markdown body accidentally starts with a second frontmatter block.

## What it does

- Scans artefact markdown across normal content roots, temporal content, and archives.
- Detects documents that already have canonical frontmatter and then start the body with another frontmatter block.
- Merges the nested block over the outer document frontmatter.
- Removes the duplicate block from the body so the file returns to a single-frontmatter document shape.

## Why this exists

- Some malformed create flows supplied a full markdown document in `body` after Brain had already generated document frontmatter.
- The result was a valid outer frontmatter block followed by a second accidental frontmatter block at the start of the body.
- `check.py` and `repair.py frontmatter` now detect and fix that state directly; this migration backfills existing vault content during upgrade using the shipped conservative merge policy: outer frontmatter stays authoritative, nested tags are unioned additively, and `modified` is refreshed when the file is rewritten.

## Verification

- Repaired files have exactly one frontmatter block at the top of the document.
- Fields that only existed in the nested block now live in the canonical document frontmatter.
- The body begins with the intended markdown content, not a second `--- ... ---` block.
