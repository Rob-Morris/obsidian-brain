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
```
