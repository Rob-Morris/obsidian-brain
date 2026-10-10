# Canary: Promotion

Follow before preparing a candidate. CI and ref checks remain deterministic;
this receipt is only the judgement they cannot make.

## Tasks

[1] **Coherent cut.** The commits up to the cut, and not the excluded tail, form one version.

[2] **Semantic bump.** The requested patch, minor, or major bump fits that cut.

[3] **Narrative.** The Summary and changelog describe the promoted tree, not the commits left on `dev`.

[4] **Impact review.** Documentation, architecture, and security impacts of the cut were reviewed.

[5] **Boundary.** No intended work was omitted from the cut, and no unrelated work was included.

[6] **Milestone.** Any milestone follow-up is identified and is not being treated as part of this promotion.

[7] **Migration versions.** Every `migrate_to_*.py` added since the last release declares a `VERSION` equal to the release `VERSION`, and its file name spells the same version. The upgrade runner selects migrations by version, so one written ahead of its release on `dev` silently never runs if the cut picks another number.

[8] **Lab at the release version.** `make test-brain-lab-current-docker` passes when run from a scratch worktree of the cut whose `src/brain-core/VERSION` is set, uncommitted, to the release `VERSION`; the lab captures tracked modifications with the worktree. The lab selects the compatibility adapter that owns that version, so `dev` cannot run this check while its `VERSION` still names the last release. A launcher command whose request or grammar changed in the cut needs that adapter's gates changed to match.

## Log

Write `.canary--pre-promotion` at the repo root before `promotion.py prepare`.
The prepare command consumes the receipt.

Log format: `[id] Short name: status, comment`

### Example

```
[1] Coherent cut: done, notes one and two; three and four stay on dev
[2] Semantic bump: done, minor
[3] Narrative: done, Summary names the promoted notes only
[4] Impact review: done, no security or architecture change in the cut
[5] Boundary: done
[6] Milestone: skip, no milestone closes with this version
[7] Migration versions: done, every new migration declares the release version
[8] Lab at the release version: done, current-template passes from a scratch worktree of the cut at the release version
```
