# Observations

Temporal artefact. Timestamped facts, impressions, and things noticed.

## Purpose

An observation records something you noticed, learned, or were told at a particular moment. It can be as short as a single sentence. Observations are factual rather than speculative — they capture what is or was, not what might be. The bar for capture is low: if you learned something worth remembering, write it down.

Observations are generic — they can be about people, places, tools, processes, or anything else. Use tags to connect observations to relevant hubs (e.g. `person/alice-smith`, `project/my-app`).

When an observation changes the current picture (a preference shifted, a fact was corrected), update the relevant living artefact. The observation preserves when you learned it; the living artefact reflects what's true now.

## Naming

`yyyymmdd-observation~{Title}.md` in `_Temporal/Observations/yyyy-mm/`.

Example: `_Temporal/Observations/2026-03/20260326-observation~Alice Prefers Pepsi.md`

## Frontmatter

```yaml
---
type: temporal/observation
tags:
  - observation
---
```

Add hub tags as appropriate, e.g. `person/alice-smith`.

## Trigger

When you notice or learn a discrete fact worth recording.

## Template

[[_Config/Templates/Temporal/Observations]]
