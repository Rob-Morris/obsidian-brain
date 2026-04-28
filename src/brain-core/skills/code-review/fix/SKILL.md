---
name: code-review:fix
description: >
  Review changed code for reuse, quality, and efficiency, then fix any issues
  found.
---

# Code Review: Fix

Run a code review and fix the issues found.

## Phase 1: Investigate

Read and follow `code-review/investigate/SKILL.md` to get the raw findings list. Dispatch reviewers as parallel subagents; do not inline.

## Phase 2: Triage and Report

Aggregate the findings and decide which are worth applying. Briefly summarise the result for the user — what's worth applying and anything notable being skipped, with the reasoning.

## Phase 3: Fix and Overview

Apply each finding marked worth applying. Skipped findings are not fixed — do not argue with the triage. When done, briefly summarise what was fixed and what was skipped, with the reasoning. If the code was already clean, say so in one line and skip the fix.
