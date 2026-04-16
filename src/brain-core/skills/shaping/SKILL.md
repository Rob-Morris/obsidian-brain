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

The artefact is a discovery type (People, Ideas, Cookies, Journal Entries, Thoughts). Open-ended exploration, no decision table.

**File:** `discover/SKILL.md`

## Routing

All file paths below are relative to this skill's base directory. Use the Read tool to load them — do NOT use the Skill tool.

1. Read and follow `assess/SKILL.md` to set up the session (artefact, transcript, taxonomy).
2. Based on what assess found, select the mode:
   - Artefact is new, empty, or a stub without enough information to make specific decisions → **brainstorm**
   - Artefact has content and is a convergent type (Designs, Plans, Tasks, Reports, Research, Presentations, Printables, Mockups) with open decisions → **refine**
   - Artefact is a discovery type → **discover**
3. Read and follow the skill file for the selected mode.

## Routing Examples

- `shaping <design name>` → assess → **refine** (existing design with decisions)
- `shaping` + "I want to build X" → assess (creates artefact) → **brainstorm**
- `shaping <person name>` → assess → **discover**
- `shaping <stub design>` → assess → **brainstorm** (not enough content to refine yet)
