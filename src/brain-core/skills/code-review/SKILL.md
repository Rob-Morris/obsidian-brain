---
name: code-review
description: >
  Review changed code for reuse, quality, and efficiency. Produce a triaged
  list of findings; no edits.
---

# Code Review

Run a code review and produce a triaged list of findings. No edits — for fixes, use `code-review:fix`.

## Phase 1: Investigate

Read and follow `investigate/SKILL.md` to get the raw findings list. Dispatch reviewers as parallel subagents; do not inline.

## Phase 2: Triage and Report

Aggregate the findings and decide which are worth applying. Briefly summarise the result for the user — what's worth applying and anything notable being skipped, with the reasoning. If the code was already clean, say so in one line.
