---
type: living/workspace
status: active
workspace_mode: embedded
---

{{agent: If you are authoring this artefact without `artefact.create` tooling, also set `key:` (canonical format: lowercase alphanumeric with hyphens, must include a letter; see .brain-core/standards/keys) and add the hub tag `workspace/{key}` under `tags:`. Tooling injects these automatically. Delete this line once applied.}}

## Purpose

## Data Folder

Embedded data lives under `_Workspaces/{key}/`. Linked data may live in several
registered local folders sharing this Brain/workspace key. Folder paths identify
registrations; they do not assign artefact membership.

## Related Artefacts

Membership uses `workspace: workspace/{key}` on member artefacts. Relationship tags do not adopt content. Set shared parent/tag defaults through `workspace.update-policy`; this hub is self-scoped.

For an existing project, explicitly adopt it before selecting it as `default_parent`.
Use tags or links for association without adoption; do not give the hub a cross-workspace parent.
