# DD-041: Canonical Living-Artefact Key Convention

**Status:** Implemented (v0.31.0)

## Context

Before v0.31.0 the brain treated tag-derived hubs as the source of truth: a "project" was wherever an artefact carried `project/{slug}` in its `tags:`, with the slug recovered by parsing the tag. This worked while every artefact lived inside a single, agreed type folder, but it stopped scaling when:

- The same hub identifier was wanted across multiple types (a `wiki/foo` page is not the same as a `project/foo`), and tag-derived inference had no clean way to express that.
- Ownership needed to survive folder reorganisation. A child artefact filed under `Projects/widget/` could lose its parent linkage simply by being moved, because tags carry relationships but not ownership.
- Search and rename tooling needed to resolve "the canonical artefact named `widget`" without scanning the whole vault for tag occurrences.

The repeated pattern was: a derived value (the tag) treated as a primary identifier (the hub key) for lookup purposes. That conflation led to brittle resolution code and ambiguous semantics whenever a tag and a hub disagreed.

## Decision

Every living artefact carries an explicit `key:` field in frontmatter. The composite `{type}/{key}` is the canonical artefact key — used by `parent:` for ownership, by hub-relationship tags for discovery, and by subfolder layouts for filesystem ownership.

- The `key:` value is slug-shaped (lowercase ASCII, hyphens, regex `^[a-z0-9]+(-[a-z0-9]+)*$`, length 1–64) so it is always URL-safe and filesystem-safe, but it is *curated*, not derived from a title at lookup time.
- Living artefacts only. Temporal artefacts have no `key:` because nothing references them as a hub; they record their owner via `parent:` alone.
- Filenames remain the human-readable title. The key is a separate machine identifier stored in frontmatter, never the filename.
- Tags persist for relationship discovery (`project/{key}` etc.) and mirror the `parent:` value for relationship-scan tooling, but ownership is carried by `key` and `parent`, not by tags.
- The router compile validates uniqueness per type and excludes any living artefact lacking a valid `key:` from `artefact_index` until the gap is repaired.

The migration `migrations/migrate_to_0_31_0.py` backfills `key:` for existing vaults, with a fixed priority: existing valid key → legacy `hub-slug` / `hub_slug` → self-referencing type tag → title-derived key → generated `{keyword}-{suffix}` fallback.

## Alternatives Considered

**Tag-derived hub identity (status quo).** Rejected. The implicit-from-tags model conflated relationship signals with ownership and made multi-type identity impossible to express without overloading the tag namespace.

**Use the filename as the key.** Rejected. Filenames are titles for Obsidian's UX (file explorer, wikilinks, backlinks) — making them double as machine identifiers forced either ugly filenames or fragile title-to-slug derivation at every lookup. Decoupling lets each side optimise for its consumer.

**Hub-only identity (no `parent:` field).** Rejected. Without an explicit ownership pointer, moves and renames had no reliable way to keep child artefacts aligned with their owner; relationship was always recovered from path-and-tag inference. The `parent:` field makes ownership a first-class fact that survives any filesystem operation.

**Reuse "slug" as the field name.** Rejected. The codebase already had `title_to_slug()`, `slugify()`, and `slug_to_title()` helpers describing URL-slug *derivation* — string transforms that produce slug-shaped output from arbitrary input. Calling the curated identifier field "slug" too would conflate two distinct concepts that happen to share a character class. The field's job is lookup, not derivation; it is named `key` to make that role clear. The slug-shape contract for the value is preserved; only the field's name changed.

## Consequences

- Hub resolution becomes a `(type, key)` dict lookup rather than a tag scan. `_artefacts.py` exposes `resolve_artefact_key_entry`, `make_artefact_key`, `parse_artefact_key`, `normalize_artefact_key`, and `scan_artefact_key_references`/`replace_artefact_key_references` to work with the canonical form everywhere.
- Ownership is robust under filesystem moves. `parent: project/widget` continues to resolve regardless of where the child file lives, as long as a living `project/widget` exists.
- Subfolder layout becomes a function of canonical keys, not folder discovery. Same-type children live under `{Type}/{key}/`; cross-type children live under `{Type}/{parent-type}~{key}/`. See [[subfolders]] for the full layout rules.
- The compiler validates uniqueness at startup. Duplicate keys within a type are a hard error, not a silent collision; `artefact_index` is the authoritative resolver and it refuses ambiguity.
- The slug helpers (`title_to_slug`, `slug_to_title`, `slugify`, `generate_contextual_slug`, `extract_slug_keyword`, the `SLUG_*` shape constants, and `_common/_slugs.py`) keep their names. They genuinely produce slug-shaped strings from input; they are unrelated to the field-naming choice.
- Field-level validators are renamed to `is_valid_key` / `validate_key` and the error code is `KEY_TAKEN` / `INVALID_KEY`. The old `is_valid_slug` / `validate_slug` / `SLUG_TAKEN` / `INVALID_SLUG` names are gone — there is no backwards-compat shim because nothing has shipped using them.
- Generated keys collide stochastically. Selection retries until the candidate is free; explicit keys raise `KEY_TAKEN` so callers can react.
- Migration is one-way and idempotent. Running it twice is a no-op once every artefact has a valid `key:` field.

## Implementation Notes

- The contract is documented at `src/brain-core/standards/keys.md` (see also `hub-pattern.md` for the relationship to tags and `subfolders.md` for the folder layout it implies).
- `_common/_slugs.py` houses the slug-shape regex and helpers. The validators live there because the *value* shape is slug-shaped, even though the *field* is named `key`. The `_slugs.py` module name is preserved.
- `migrations/migrate_to_0_31_0.py` is the entry point for vault upgrades. It runs `plan_key_backfill` then writes; tests in `tests/test_migrate_to_0_31_0.py` cover the priority cascade end-to-end.
- The MCP `brain_create` tool accepts an explicit `key=...` kwarg; absent that, the platform generates a contextual key from the title.
- Workspace registry (`scripts/workspace_registry.py`) reads `fields["key"]` from each hub on registry build; pre-0.31 hubs without a key fall back to filename stem so registry resolution stays available during migration windows.
