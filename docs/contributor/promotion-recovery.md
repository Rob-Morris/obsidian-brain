# Recover an Unpublished Promotion Queue

Contributor-only recovery for an exceptional direct push to `main` that leaves
it outside the `unreleased` ledger. Ordinary changes still use
`prepare` → `finish` → `publish`; see [Contributing](../CONTRIBUTING.md#dev-branch).
Recovery does not push `main`, publish a version, or add branch protection.

## Plan and review

Start from clean `dev` matching shared `origin/dev`, with no worktree checking
out `main`. If you have private work, preserve it and use a separate clean clone
for the shared recovery, then adopt the result in your original clone.

```bash
.venv/bin/python src/scripts/promotion.py recover plan
# Optional, deliberately chosen version mappings:
.venv/bin/python src/scripts/promotion.py recover plan --input recovery-request.json
```

Planning is local. It fetches the shared snapshot, rebuilds every unpublished
release on the new main, tests each exact replacement candidate, rehearses the
uncut dev tail, and pins an immutable Git plan. The output includes the full plan
SHA and the old/new mapping. No shared ref or primary dev checkout moves.
Use the preview's full content diffs to inspect the direct-main change,
each replacement candidate and both old/new uncut tails.

The common published anchor needs human confirmation: current refs cannot prove
that main was never rewritten. Review the entire queue and stop if any entry was
already published or its history is uncertain. Unknown provenance, incoherent
release facts and content conflicts are refusals, not invitations to force refs.

Recovery preserves release boundaries, authored notes, Summary, release date and
commit body. It reconstructs source/tip provenance and renders new release
metadata using the rebuilt source's release API. It retains original dev history
through provenance and replays the uncut shared tail. Private local commits are
not part of the shared plan.

Version numbers are retained when compatible. An optional request is an object
of explicit old-to-new semantic-version maps, for example:

```json
{
  "core": {"1.1.0": "1.3.0", "1.2.0": "1.4.0"},
  "cli": {},
  "proxy": {}
}
```

These are illustrations, not recommended project versions. An unchanged helper
version follows the new base; an explicitly bumped helper must remain compatible
or receive an explicit mapping. Review every displayed substitution in authored
notes. Destination candidate names belonging to another release are refused;
the tool does not overwrite that owner or silently renumber the queue. Content
resolution inputs and interactive conflict resolution are not supported. A
conflict identifies the affected content and stops without changing shared refs.

After reviewing the actual plan, follow the
[recovery canary](../../.canaries/pre-recovery.md) and write
`.canary--pre-recovery`, binding it to that full plan SHA. Keep request files
outside the tracked checkout or in an ignored local directory so they do not
make dev dirty.

## Stage, verify and apply

Use the printed full SHA wherever `<plan-sha>` appears:

```bash
.venv/bin/python src/scripts/promotion.py recover stage <plan-sha>
.venv/bin/python src/scripts/promotion.py recover status <plan-sha> --json
.venv/bin/python src/scripts/promotion.py recover apply <plan-sha>
```

Stage atomically publishes the plan and installs replacement candidates under
their canonical `promotion/vVERSION` names, with exact old-SHA ownership checks.
Old objects remain reachable through the plan. Stage consumes the review receipt
only after the transaction is observed committed. `main`, `unreleased` and `dev`
stay unchanged while the replacements run CI.

Every rebuilt SHA must pass the same Linux, Windows and dependency certification
workflows as any other candidate. Old green runs cannot certify a replacement.
Use the [post-push CI check](../standards/agent-workflow.md#post-push-ci-check) for
each replacement's exact SHA and branch. `apply` observes evidence once; it does
not wait, start workflows, or rerun them until green.

Apply rechecks the sealed snapshot and every candidate, then atomically replaces
`unreleased`, fast-forwards `dev` through retained provenance, and creates the
immutable applied result. Nothing is published to main. Empty-queue alignment
creates no candidate and adds no new CI gate; it does not certify already-pushed
main content or waive the independent duty to resolve failed main CI.

The ordinary immutability and one-version finish rules still apply outside these
exact recovery transactions. A server without atomic push support is refused.
There is no sequential fallback or general force-push option.

## Interruptions, stale plans and abort

A failed push response does not establish rollback. Inspect the same plan; do
not rebuild or force shared branches merely because a connection failed.

| Observed outcome | Next action |
|---|---|
| Not staged, old refs unchanged | Retry stage with its review receipt. |
| Staged and snapshot unchanged | Wait for exact candidate CI, then apply. |
| Staged but shared snapshot changed | Abort owned staging and plan again; do not absorb changes into the old plan. |
| Applied | Do not push the old targets again. Align local work with current shared dev. |
| Aborted | Terminal; create a new plan if still needed. |
| Foreign/mixed state or unavailable observation | Preserve work and diagnose the reported exact refs; do not guess. |

Before application, an explicit abort restores only candidate refs still owned
by this plan:

```bash
.venv/bin/python src/scripts/promotion.py recover abort <plan-sha>
```

Restoration and the aborted result are atomic. Any foreign candidate prevents
the entire abort; other people's replacements are never deleted. Apply and
abort compete on the same absent result ref, so both cannot win. Applied recovery
cannot be rolled back with abort. Later repair is new work.

Main and candidate refs are re-observed, not locked against arbitrary bypassed
writes. If the applied result exists but another writer has since changed refs,
application still committed. Report and repair the new incompatibility; never
claim the original transaction rolled back.

## Adopt from another clone

After recovery, run ordinary `adopt` from clean dev. It validates the recovery
record when accepting a replaced ledger, including in narrow-fetch clones, then
replays only your private suffix onto current shared dev. Rehearsal happens
before changing the checkout. Conflicts preserve your private commits; already
aligned suffixes are not rewritten again.

```bash
.venv/bin/python src/scripts/promotion.py adopt
# Optional: explicitly discard a stale local candidate after alignment.
.venv/bin/python src/scripts/promotion.py adopt promotion/vX.Y.Z
```

Same-name remote replacements are preserved by SHA ownership. A rebuilt candidate
already on the ledger belongs to normal publication cleanup, not stale-candidate
adoption. A local settlement failure does not undo shared application.

Recovery state is two immutable Git refs:
`recovery/<plan-sha>/plan` and `recovery/<plan-sha>/result`. They retain old/new
history for diagnosis and other clones. There is no mutable journal, external
database or automatic pruning. Superseded canonical names left by explicit
version mappings are reported and can be deliberately discarded by exact owner;
never delete all promotion refs by prefix.

Make wrappers mirror the CLI: `promotion-recover-plan [INPUT=...]` and
`promotion-recover-stage`, `-status`, `-apply`, `-abort` with `PLAN=<full-sha>`.
