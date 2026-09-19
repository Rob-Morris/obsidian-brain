# Agent Workflow

Contributor-only workflow standard for agents changing `obsidian-brain`.

This does not ship in `.brain-core` and is not part of the runtime bootstrap for normal Brain agents.

## Tiered Workflow

When making or planning changes, choose the smallest workflow tier that safely fits the change. Escalate one tier when the blast radius is unclear, the change crosses bounded contexts, or it touches bootstrap, migrations, routing, or security-sensitive behaviour.

| Tier | Use when | Required flow | Verification bar |
|---|---|---|---|
| `trivial` | Typos, doc clarifications, local config/template tweaks, dead-code removal with no behavioural effect | Implement -> verify | Concrete local proof: the changed text/config renders correctly or the targeted command/output matches the intent |
| `small` | Single-file or single-context behavioural changes with a clear contract | Implement -> verify -> review | Focused tests or equivalent reproducible check, then self-review the diff for drift |
| `medium` | Multi-file changes within one or two contexts, or work with meaningful edge-case risk | Research -> plan -> implement -> verify -> review | Explicit execution + verification plan, relevant tests, and a docs/canary sweep before hand-off |
| `large` | Architectural changes, new context boundaries, migrations, bootstrap/security changes, or staged rollouts | Research -> design -> approval -> plan -> implement -> verify -> multi-review -> final review | Full-suite proof, explicit rollout/rollback thinking, and separate review passes for design and implementation |

## Tier Notes

- `trivial` should stay local. If the change starts affecting behaviour, tests, or multiple files, it is no longer trivial.
- `small` is the default tier for straightforward brain-core implementation work. If a core domain behaviour changes, update an existing `.feature` file or add one when that contract is not already covered.
- `medium` requires an explicit plan before editing. Name the bounded contexts touched, the files you expect to change, and how you will verify the result.
- `large` work should be phased. Do not treat a cross-cutting design change as an auto-implement task; shape it, get approval where required, and then execute in verifiable slices.

## Post-push CI check

After every authorised push, check CI for the exact pushed commit before
reporting the pushed change complete. Local tests remain the pre-commit gate;
they do not replace native CI. Keep the existing branch and workflow triggers.
This is an explicit contributor check, not automated branch protection.

1. Record the pushed commit SHA and branch. Identify its runs for
   [Linux tests](../../.github/workflows/linux-test.yml),
   [Windows smoke](../../.github/workflows/windows-smoke.yml) and
   [dependency certification](../../.github/workflows/dependency-certification.yml).
   All three must finish successfully, including the native dependency matrix.
2. Monitor queued/running checks until terminal. A missing run, pending result,
   cancellation, skip or unavailable GitHub access is not a pass. Allow for run
   creation delay, then inspect triggers if a workflow never appears. A non-main branch
   without a PR does not automatically run these checks on push; use an
   authorised manual dispatch or report verification blocked. Do not silently
   change triggers, open a PR or publish more code to obtain a green result.
3. For failures, read the failed job logs and diagnose the cause. Fix relevant
   code/test defects, rerun local verification, then push the correction within
   the task's authority and monitor all checks for the new SHA. Rerun unchanged
   code only when evidence supports a transient infrastructure failure; do not
   retry until green, weaken assertions or skip failing coverage. Report unrelated
   failures or missing authority as explicit blockers with the failing run links.
4. Before completion or deployment, recheck the final candidate's latest run
   attempts: each workflow must have `status: completed` and `conclusion: success`.
   An older green commit or attempt does not certify its replacement. For PR
   checks, use the PR's associated checks and record the tested merge/head SHA;
   after merging, check the resulting pushed commit too.
5. Include the verified SHA and workflow links/results in the handoff. Distinguish
   local verification, CI verification, merge/push and deployment. If blocked,
   name the unresolved check and next action; do not claim completion or proceed
   to release/vault propagation. A local-only commit may be handed off as
   unpushed and CI-unverified, without implying permission to push or deploy.

For a push run, capture the SHA at push time and inspect it with GitHub CLI:

```bash
ci_sha="$(git rev-parse HEAD)"
gh run list --commit "$ci_sha" --limit 100 \
  --json databaseId,workflowName,headSha,headBranch,event,status,conclusion,attempt,url
gh run view RUN_ID --json headSha,status,conclusion,attempt,jobs,url
gh run view RUN_ID --log-failed
```

Replace `RUN_ID` with each applicable run ID. Check the expected workflow set,
not just whichever runs the listing returns. Inspect more results if the listing
is truncated; use `gh pr checks PR_NUMBER` for PR-associated checks. Poll pending
runs at a reasonable interval while keeping the user informed. A zero exit from
`gh run view --exit-status` alone is insufficient: a pending run can also return
zero. Inspect both status and conclusion explicitly.

Direct pushes to `main` can make `main` temporarily red. This workflow requires
following those failures through to resolution; it does not prevent them from
landing or grant authority to force-push, bypass protections or deploy.
