# Releases

Living artefact. Release milestone record.

## Purpose

One file per release milestone. Before shipment, a release artefact tracks the milestone goal, acceptance criteria, in-scope designs, and supporting sources. After shipment, the same artefact becomes the canonical historical record for the shipped release range and its internal release notes.

## When To Use

When a project, workstream, or product area has a named milestone, version target, or release cut worth planning, tracking, or recording separately from a broader hub. A release must set a canonical `parent` to its owning living artefact; projects are the common case, but any living artefact can own a release.

## Lifecycle

| Status | Meaning |
|---|---|
| `planned` | Aspirational milestone record. Scoped and named, but work has not started yet. Owns no shipped versions. |
| `active` | Current milestone record. Work is in progress, but the release still owns no shipped versions. |
| `shipped` | Historical release record. Terminal — keeps the canonical shipped range, metadata, and release notes. |
| `deprecated` | Abandoned, cancelled, or superseded milestone. Reason captured in a callout. Terminal — keeps the record of what was cut. Owns no shipped versions. |

## Terminal Status

When a release reaches a terminal status (`shipped` or `deprecated`):

- **Shipped:** set `status: shipped`; the lifecycle handler moves the release to the `+Shipped/` folder within its ownership location. The filename leads with the shipped version (see Naming).
- **Deprecated:** set `status: deprecated`, add a reason callout, and let the lifecycle handler move the release to the `+Deprecated/` folder within its ownership location:
  ```markdown
  > [!info] Deprecated — cancelled: scope absorbed into next milestone
  > [!info] Deprecated — superseded by [[link|new release]]
  > [!info] Deprecated — retired: milestone no longer pursued
  ```

Shipped and deprecated releases remain searchable and indexed in their terminal folders. No rename for deprecated; no `archiveddate`.

## Naming

Primary folder: `Releases/`. Every release follows the shared living-artefact ownership convention rooted at its required canonical `parent`.

Before ship a release is identified by its human title. Once `status` is `shipped` the filename leads with the shipped terminal version so the canonical record on disk is version-led.

### Rules

| Match field | Match values | Pattern |
|---|---|---|
| `status` | `planned`, `active`, `deprecated` | `{Title}.md` |
| `status` | `shipped` | `{Version} - {Title}.md` |

### Placeholders

| Placeholder | Field | Required when field | Required values | Regex |
|---|---|---|---|---|
| `Version` | `version` | `status` | `shipped` | `^v?\d+\.\d+\.\d+$` |

Examples:

- Planned: `Releases/project~brain/Operational Maturity.md`
- Nested ownership: `Releases/project~brain/design~command-interface/Interface Cutover.md`
- Shipped: `Releases/project~brain/+Shipped/v0.28.6 - Release Artefact Type.md`

## Frontmatter

```yaml
---
type: living/release
key: {key}
parent: project/{owner-key}
tags:
  - release
status: planned
version:
tag:
commit:
shipped:
---
```

**Optional:** `version`, `tag`, `commit`, `shipped`

`status` is required because the naming rules select a filename pattern from it.

`parent` is required and uses a canonical artefact key such as `project/brain`. Tooling keeps the owner-derived path and matching relationship tag aligned. `version`, `tag`, `commit`, and `shipped` become load-bearing at ship time; until then they may stay blank.

## Template

[[_Config/Templates/Living/Releases]]

## Taxonomy guidance

- Use `## Acceptance Criteria` for milestone criteria only. Recommended status values are `pending`, `partially met`, `met`, and `deferred`.
- Use `## Designs In Scope` for designs or plans intended to help meet the milestone goal. Each entry pairs the design with a brief release-specific role note describing what the design contributes to this release (e.g. `- [[Documentation Audit Skills]] — provides the audit and recommendation machinery`). This keeps the design portfolio legible without re-coupling individual designs to specific criteria. Keep this section separate from the acceptance-criteria table.
- Use `## Release Notes` for the internal shipped summary. It should explain what the milestone amounted to, not mirror the repo changelog line-by-line.
- Use `## Sources` for evidence supporting acceptance-criteria status claims and ship-time decisions.
- Only `shipped` releases own versions. The terminal `version` recorded at ship time marks the end of the contiguous shipped range that the release claims.

## Hub Indexing

Releases are owner-indexed. Any artefact that owns one or more releases — a project, a book, a design, a wiki page, or any other living artefact — should index its releases in its own body using the standard four-section pattern:

- `## Release Policy` — cadence, versioning scheme, and any branch or tag rules
- `## Active Releases` — wikilinks to `active` release artefacts
- `## Shipped Releases` — wikilinks to `shipped` release artefacts, newest first
- `## Backlog` — planned future versions or named release ideas not yet active

Projects are the canonical case and the project template ships with these sections by default. Other owner types adopt the same headings when they hold releases. The pattern is a body convention: the ownership relationship is structural (`parent:` on each release), but the index in the owner's body remains authored content rather than auto-generated state.
