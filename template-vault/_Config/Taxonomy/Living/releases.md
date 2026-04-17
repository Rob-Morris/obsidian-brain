# Releases

Living artefact. Version-scoped shipment records for a project.

## Purpose

One file per planned or shipped version of a project. A release artefact keeps the goal, gate checklist, human-readable changelog, and source links for that version in one place so shipping work stays separate from the long-lived project hub.

## When To Use

When a project has a specific version, milestone, or named release worth planning, tracking, or recording. Create one release artefact per shipped or planned version.

## Lifecycle

| Status | Meaning |
|---|---|
| `planned` | Scoped and named, but work has not started yet. |
| `active` | In progress — gates are being worked through. |
| `shipped` | Released. Terminal — keep as the canonical record of what went out. |
| `cancelled` | Abandoned before release. Terminal — keep as the record of what was cut. |

## Terminal Status

When a release reaches `shipped` status, move it to `Releases/{Project}/+Shipped/`.

When a release reaches `cancelled` status, move it to `Releases/{Project}/+Cancelled/`.

Shipped and cancelled releases remain searchable and indexed in their terminal folders. No rename, no `archiveddate`.

## Naming

Primary folder: `Releases/{Project}/`.

Before ship a release is identified by its human title. Once `status` is `shipped` the filename leads with the shipped version so the canonical record on disk is version-led.

### Rules

| Match field | Match values | Pattern |
|---|---|---|
| `status` | `planned`, `active`, `cancelled` | `{Title}.md` |
| `status` | `shipped` | `{Version} - {Title}.md` |

### Placeholders

| Placeholder | Field | Required when field | Required values | Regex |
|---|---|---|---|---|
| `Version` | `version` | `status` | `shipped` | `^v?\d+\.\d+\.\d+$` |

Examples:

- Planned: `Releases/Brain/Experimental Cut.md`
- Shipped: `Releases/Brain/+Shipped/v0.28.6 - Release Artefact Type.md`

## Frontmatter

```yaml
---
type: living/release
tags:
  - release
  - project/PROJECT-SLUG
status: planned
version:
tag:
commit:
shipped:
---
```

## Template

[[_Config/Templates/Living/Releases]]

## Taxonomy guidance

- Keep the gate list short. Seven or fewer gates is the recommended default.
- Write the changelog for humans, not machines. Summaries should explain what changed and why, not mirror commit messages.
- Use gate status `deferred` when a scope cut is intentional and acknowledged, not forgotten.
