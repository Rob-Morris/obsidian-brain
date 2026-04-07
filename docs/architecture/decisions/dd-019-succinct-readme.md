# DD-019: Succinct Readme Pattern for Lean Discovery Guides

**Status:** Implemented (v0.4.0)

## Context

Agents discovering the vault's classification system need an entry point that explains the structure and points to the right locations. A verbose readme would be expensive to load on every session; it might also drift out of sync with the actual filesystem layout.

## Decision

`.brain-core/taxonomy/readme.md` is a lean discovery guide of approximately 50 tokens. It explains the classification system in minimal terms and points agents to `_Config/Taxonomy/`. It does not enumerate types — the filesystem is the index (DD-018). The pattern is: orient, not enumerate.

## Consequences

- The readme stays cheap to load regardless of how many types the vault has.
- It never becomes stale in the "enumeration" sense — since it doesn't enumerate types, adding a type doesn't require updating it.
- Agents get enough orientation to navigate the taxonomy without the readme needing to be comprehensive.
- The same pattern applies to other lean discovery guides in brain-core — they orient, they don't duplicate content that lives elsewhere.
