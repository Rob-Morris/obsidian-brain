# DD-001: Drop Version from Wikilink Paths

**Status:** Implemented (v0.3.0)

## Context

Early brain-core wikilinks included a version segment in the path (e.g. `[[.brain-core/v0.2/index]]`). This made every link stale on upgrade — agents following links would land on 404s, and any tooling that parsed wikilinks had to understand version segments.

## Decision

Drop the version component from all wikilink paths. Links use stable, version-agnostic paths such as `[[.brain-core/index]]`. The current version is readable from `.brain-core/VERSION`, not encoded in paths.

## Consequences

- Wikilinks remain valid across upgrades without modification.
- Agents and tooling can follow links without understanding versioning.
- The VERSION file becomes the single source of truth for the installed version.
- Any vault with version-encoded wikilinks requires a one-time migration to the stable path format.
