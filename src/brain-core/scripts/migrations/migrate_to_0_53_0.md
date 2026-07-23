# Migration to v0.53.0

Before the strict v0.53 router compile gate, this migration repairs configured
taxonomies whose legacy `## Shaping` contract either expresses its completion
status as prose containing one backticked example, or does not declare the
`shaping` or completion status in its lifecycle.

The compiler prefixes these compatibility failures with the stable
`SHAPING_METADATA_INVALID` and `SHAPING_LIFECYCLE_STATUS_UNDECLARED` codes. The
migration keys off those codes, not the surrounding diagnostic prose, and
leaves unrelated compile failures untouched.

The repair converts the unambiguous legacy completion prose to the strict
backticked field, preserves every status already recognised by the old taxonomy,
and adds only the missing shaping values. Inline frontmatter status comments are
extended in place. Table and prose forms are normalised into a complete Markdown
lifecycle table when needed, including headers and neutral descriptions for
carried-over values. The migration reparses its result and refuses to write
unless every existing and required status remains authoritative. The upgrade
runner snapshots each changed taxonomy, so a later upgrade failure restores the
original file.

A malformed sibling taxonomy is reported as a per-file warning rather than
abandoning repairs that can be applied safely. The upgrade runner performs the
single authoritative compiler validation after the full migration pass.

Verification:

- the pre-compile migration result lists each patched taxonomy and added status;
- `compile_router.py` succeeds with the repaired lifecycle contract;
- `.brain/local/migrations.json` records `0.53.0@pre_compile_patch` as `ok`.

No manual action is required. If compilation still fails, run Brain repair and
address the separate compiler error it reports.
