# Canary: Promotion Recovery

Follow after `promotion.py recover plan` has produced its immutable plan and
preview, and before `recover stage <plan-sha>` publishes replacement candidates.
Read the full content diffs displayed by the preview; a diff
stat alone is not a content review. Review the whole queue, not only the last
version. This receipt covers the
promotion judgements for that mapping; deterministic ref, replay and CI checks
remain in the tool.

## Tasks

[1] **Published boundary.** Confirm the displayed common anchor is the last shared published point and every release being replaced is unpublished. Current Git refs cannot prove that main was never rewritten. Stop if the publication history is unknown or contradicts the plan.

[2] **Release boundaries.** Each old-to-new entry still represents its intended coherent version; an entry with no remaining content effect has been noticed rather than silently removed.

[3] **Narrative.** Review the preserved Summary, authored notes, release date and commit body for every entry, including any explicitly requested version substitutions.

[4] **Versions.** The retained or explicitly mapped Core, CLI and proxy versions have the intended semantic meaning. The tool has not chosen new version numbers on your behalf.

[5] **Impact and work preservation.** Review the combined effect of the direct-main change, rebuilt releases and uncut dev tail, including documentation and architecture impacts. Private work remains outside the shared plan and will be adopted separately.

[6] **Scope.** Rebuilding this queue is the intended recovery. It neither publishes main nor resolves content conflicts automatically; identify any milestone or other follow-up separately.

## Log

Write `.canary--pre-recovery` at the repo root. Include exactly one `Plan:` line
with the full plan SHA, followed by one receipt line for every task. Stage checks
the binding and consumes the receipt only once staging is observed committed.
An unchanged staged plan can be inspected, applied or aborted without creating
another receipt. A new plan needs a new review.

Log format: `[id] Short name: status, comment`, where status is `done` or
`skip, reason`. The boundary and scope must be confirmed; do not proceed when
their facts are unknown.

```text
Plan: <full-plan-sha>
[1] Published boundary: done, checked the published point and the unpublished queue
[2] Release boundaries: done, both queued versions retain their intended scope
[3] Narrative: done, reviewed both notes and the replacement mapping
[4] Versions: done, existing versions remain suitable
[5] Impact and work preservation: done, reviewed the direct-main fix and uncut tail
[6] Scope: done, recovery only; publication remains a separate decision
```
