# DD-017: Shorthand Trigger Index with Gotos

**Status:** Implemented (v0.4.0)

## Context

The lean router needs to list conditional triggers without reproducing the full trigger instructions inline. Each trigger condition should point to where the instructions live. Two options: embed the instructions directly (expensive, duplicated) or use a pointer (goto) to the canonical source.

## Decision

Triggers in the lean router use a goto pattern: a one-line condition followed by a wikilink to the taxonomy or skill file that contains the `## Trigger` section with full instructions. Example: `- Working on a research topic → [[_Config/Taxonomy/living/research.md]]`. The target file is the single source of truth for what the trigger does and how to respond.

## Consequences

- The router stays compact — one line per trigger, no duplication of instructions.
- Trigger instructions are maintained in taxonomy files only; router references never go stale in content (only the wikilink target could change).
- Agents must follow the wikilink to get full trigger instructions — one extra file read.
- The compiled router expands triggers into structured data at compile time, so tools get full trigger detail without following links.
