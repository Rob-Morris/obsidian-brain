# Canary: Development Commit

Follow before a commit on `dev` or a feature branch. This receipt does not claim
that release metadata is final. Promotion has its own canary.

## Tasks

[1] **Scope.** The staged change is the work you mean to keep on `dev`, not an accidental mix of unrelated edits.

[2] **Documentation noticed so far.** Name the docs this change will eventually need, or state that none are apparent yet. Do not block a WIP commit on a full docs sweep.

[3] **Verification decision and evidence.** Follow
`docs/CONTRIBUTING.md#selecting-development-verification`. Record changed inputs,
affected owners/consumers, applicable earlier evidence, the selected serial
commands and outcomes, and omitted coverage. Use full serial `make test` for
broad or uncertain impact; retain relevant slow integration tests. Explain any
checks invalidated by later edits. Do not present selected or interrupted
coverage as a full-suite pass. This also applies to WIP.

Candidate preparation and hosted certification retain their full gates.
`make test-fast` and parallel runs are iteration feedback, not substitutes for
the selected serial gate.

## Log

After the tasks above, write `.canary--pre-commit` at the repo root.
Every task needs one log line.

Log format: `[id] Short name: status, comment`

### done

`done` means you performed the action.

### skip

`skip, {reason}` means you did not perform the action. The reason must be your own assessment.

### Example

```
[1] Scope: done, only the promotion command and its tests
[2] Documentation noticed so far: done, contributor workflow docs still need the new command
[3] Verification decision and evidence: done, changed promotion orchestration only; traced prepare/recovery consumers; final diff against HEAD tested with serial pytest tests/test_promotion*.py and tests/repo/test_promotion_canary.py after required setup, all passed with no unexpected skips; no earlier evidence reused; omitted runtime/installer coverage because their inputs and shared execution contracts are unchanged
```
