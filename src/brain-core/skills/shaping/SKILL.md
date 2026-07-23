---
name: shaping
description: >
  Shape an artefact through structured Q&A. Routes to the right sub-skill:
  brainstorm (new/unclear artefacts), refine (open decisions), or discover
  (exploration-driven artefacts like People and Ideas).
---

# Shaping

Shape an artefact through structured Q&A until it meets its type's bar.

## Modes

### brainstorm

The artefact is new or a stub. What needs to be shaped isn't clear yet. Explores the idea, writes initial content, then hands off to refine.

**File:** `brainstorm/SKILL.md`

### refine

The artefact is clear but has open decisions to work through. Decision-driven, with progress tracking.

**File:** `refine/SKILL.md`

### discover

The taxonomy declares discovery shaping. Open-ended exploration, no decision table.

**File:** `discover/SKILL.md`

## Routing

All file paths below are relative to this skill's base directory. Use the Read tool to load them — do NOT use the Skill tool.

1. Read and follow `assess/SKILL.md`. It resolves the artefact, reads the taxonomy's `## Shaping` metadata, selects the mode, and opens the session.
2. Read and follow the skill file for the mode returned by assess.

## Routing Examples

- `shaping <design name>` → assess → **refine** (existing design with decisions)
- `shaping` + "I want to build X" → assess (creates artefact) → **brainstorm**
- `shaping <discovery artefact>` → assess → **discover**
- `shaping <stub design>` → assess → **brainstorm** (not enough content to refine yet)
