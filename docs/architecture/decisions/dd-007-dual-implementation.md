# DD-007: Dual Implementation with Shared Test Fixtures

**Status:** Accepted

## Context

Brain-core has two runtime paths: Python scripts (desktop/MCP) and TypeScript plugin (Obsidian). When logic is shared — frontmatter parsing, naming conventions, timestamp handling — the two implementations can silently diverge. Unit tests for each implementation in isolation would not catch cross-language disagreements.

## Decision

Shared test fixtures define the expected behaviour for any logic that must be consistent across Python and TypeScript. Both implementations are tested against the same fixture data. A fixture that passes in Python but fails in TypeScript (or vice versa) is a bug.

## Consequences

- Cross-language consistency is verified automatically, not assumed.
- Adding a new shared behaviour requires a fixture update alongside both implementations.
- Fixtures serve as the canonical specification for shared logic — they are more authoritative than prose documentation.
- The fixture format must be readable by both Python and TypeScript test harnesses (JSON is the natural choice).
