---
name: code-review
description: >
  Review changed code for reuse, quality, and efficiency. Use after writing code
  to produce a triaged findings list without edits, or when asked to review and
  fix the worthwhile findings.
---

# Code Review

Review changed code and either report a triaged findings list or fix the worthwhile findings, according to the user's request.

## Workflows

- **Review (default):** Read [references/investigate.md](references/investigate.md) completely, then triage and report without editing.
- **Fix:** When the user asks to fix review findings, read and follow [references/fix.md](references/fix.md) completely.

All paths are relative to this skill's root. These referenced Markdown files are workflow instructions, not independently discoverable skills.

## Review-only route

Read and follow [references/investigate.md](references/investigate.md) completely to get the raw findings list. Dispatch reviewers as parallel subagents; do not inline.

Then aggregate the findings and decide which are worth applying. Briefly summarise the result for the user — what's worth applying and anything notable being skipped, with the reasoning. Do not edit files. If the code was already clean, say so in one line.
