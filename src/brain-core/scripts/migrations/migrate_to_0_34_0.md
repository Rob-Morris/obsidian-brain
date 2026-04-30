# Migration v0.34.0 — Release Artefact Alignment

Normalises legacy release artefacts to the settled Phase 1 release structure.

## What it does

- Removes the legacy literal `**Project:** [[PROJECT]]` placeholder line.
- Renames `## Gates` to `## Acceptance Criteria` and converts the old gate table to a two-column milestone-criteria table.
- Pulls each unique design out of the old `Implicated Designs` column into `## Designs In Scope` as one entry per design (instead of a per-row dump). Each entry carries a `_todo: release role_` placeholder and preserves the originally-listed criteria as transition context (e.g. `- [[Brain Search]] — _todo: release role_ (legacy criterion: "Search is stable")`); the author replaces the placeholder with a release-specific role note describing what the design contributes.
- Renames `## Changelog` to `## Release Notes`.
- Rehomes or renames the file so it matches the status-based naming contract, anchored on the canonical `parent:` (or the file's current ownership context when the parent does not resolve).
- Refreshes the matching parent relationship tag when a resolvable canonical `parent:` is set but the corresponding tag is missing.

## What it does not guess

- It never infers a new parent from tags or folders.
- If a release has no `parent:` set, the migration **halts before making any changes** and lists the offending paths. Set `parent: <type>/<key>` on each (any owning living artefact type is valid — projects are the canonical case), then re-run the migration.
- If a stored `parent:` does not resolve, the migration leaves the file in its current ownership context and records a warning.
- It preserves existing release-note prose; it does not rewrite the content into a new editorial style.

## Verification

- Release files now have sections in this order: `Goal`, `Acceptance Criteria`, `Designs In Scope`, `Release Notes`, `Sources`.
- Every release carries a canonical `parent:` and the matching relationship tag, and lives under the corresponding owner-derived folder.
- Terminal releases now live under `+Shipped/` or `+Cancelled/` within their current ownership context and use the current naming contract.
