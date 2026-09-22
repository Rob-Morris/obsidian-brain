# Canary: Development Commit

Follow before a commit on `dev` or a feature branch. This receipt does not claim
that release metadata is final. Promotion has its own canary.

## Tasks

[1] **Scope.** The staged change is the work you mean to keep on `dev`, not an accidental mix of unrelated edits.

[2] **Documentation noticed so far.** Name the docs this change will eventually need, or state that none are apparent yet. Do not block a WIP commit on a full docs sweep.

[3] **Focused verification.** State the local check you ran for this change, or why no check was warranted.

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
[3] Focused verification: done, pytest tests/test_promotion.py
```
