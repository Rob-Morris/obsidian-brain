# Keys

Every living artefact has a `key` — a short, stable identifier that participates in the artefact's canonical key `{type}/{key}`. The key is the foundation of hub lookup, parent ownership, cross-type scope projection, and rename behaviour.

Only living artefacts carry keys because only living artefacts act as referenceable targets — they can *be* a hub. Temporal artefacts never act as hubs themselves; they can still *be owned* by a living hub via `parent:`, but they don't need a key of their own to do so.

## Terminology

- `key` — the frontmatter identifier stored on a living artefact (for example `brain`)
- `canonical key` — the typed identity `{type}/{key}` (for example `project/brain`)
- `parent` — a frontmatter field whose value is an owning artefact's canonical key
- `scope` — the tokenised cross-type folder form of a canonical key (for example `project~brain`)

Same-type child folders still use the raw `key` directly; `scope` exists for cross-type folder projection only.

## Frontmatter contract

Every living artefact has a `key:` field in frontmatter:

```yaml
---
type: living/project
key: pistols-at-dawn
tags:
  - project/pistols-at-dawn
---
```

Temporal artefacts do not carry a `key:` field — there is nothing to key them by, because nothing references them as a hub. A temporal child records its owner through `parent:` alone:

```yaml
---
type: temporal/mockup
parent: project/pistols-at-dawn
tags:
  - project/pistols-at-dawn
---
```

The tag mirrors the `parent:` value for relationship-scan tooling; ownership is carried by the `parent:` field, not the tag (see [[hub-pattern]]).

## Format

- Lowercase ASCII letters and digits, separated by single hyphens. At least one letter is required.
- Regex: `^[a-z0-9]+(-[a-z0-9]+)*$`, with the additional constraint that the key must contain at least one `[a-z]` character.
- Length: 1–64 characters.

Uppercase letters, spaces, underscores, consecutive hyphens, leading or trailing hyphens, and purely numeric values are invalid. Violations raise `INVALID_KEY` at the API boundary.

## Selection

The tooling generates keys automatically as `{keyword}-{suffix}`:

- **Keyword** — the longest non-stopword token from the title, lowercased, stripped of non-ASCII, and truncated to 12 characters. When every token is a stopword the sentinel keyword `husk` is used.
- **Suffix** — a random 3-character string drawn from a confusable-free alphabet (`SLUG_ALPHABET` in `_common/_slugs.py`).

Example: title `Pistols at Dawn` → keyword `pistols` → key `pistols-a1b`.

**Explicit keys** are accepted when a caller wants a memorable key (for example `pistols-at-dawn` on creation, or when converting). Explicit keys are validated against the format contract and rejected on collision within their type.

## Role

The key drives several derived structures:

- The canonical artefact key `{type}/{key}` used in `parent:` pointers and hub relationship tags.
- Same-type child folder names under an owner: `{Type}/{key}/`.
- Cross-type scope tokens such as `project~brain`, used in folder paths as `{Type}/{scope}/`.

Tags and wikilinks remain relationship signals; ownership lives in the `key` and `parent` fields and the canonical folder layout.

## Relationship to filenames

Filenames are the human-readable `{Title}` — that is how Obsidian surfaces artefacts in the file explorer, in wikilinks, and in backlinks. The key is a separate machine identifier stored in frontmatter, never the filename. See [[naming-conventions]] for the filename rendering contract.

## Absence

If a living artefact lacks a valid `key:` field, the router compile excludes it from `artefact_index`. The artefact still exists and is indexed by the search layer, but it cannot be resolved as a hub or owner, and children cannot point to it via `parent:` until a valid key is added.

## Collisions

Key uniqueness is enforced per artefact type. Two distinct `living/project` artefacts cannot both have `key: widget`; the router compile fails when duplicates are found.

- **Generated keys** — the platform resamples the suffix until a unique candidate is produced.
- **Explicit keys** — the caller receives `KEY_TAKEN` and must choose another value.

## Conversion

Converting an artefact's type can cross the living/temporal boundary:

- **Temporal → living** — the target gains hub capability, so tooling must generate a valid `key:` during conversion. A key-less living artefact breaks the canonical-key contract.
- **Living → temporal** — the key becomes vestigial (a temporal artefact carrying a key isn't broken, but it has no function). Any children that referenced the converted artefact via `parent:` are left pointing at a non-hub; those references should be healed or removed by the conversion flow.

## See also

- [[hub-pattern]] — how keys combine into canonical `{type}/{key}` keys and `parent` pointers.
- [[subfolders]] — how keys project into folder structure.
- [[naming-conventions]] — filename rendering contract (filenames are titles, not keys).
