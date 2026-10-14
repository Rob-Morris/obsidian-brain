# Workspaces

Workspace hub files. One file per workspace, linking brain artefacts to a bounded container of working files that fall outside the vault taxonomy.

Membership is explicit `workspace: workspace/{key}` metadata, not a tag-derived convention. Hubs own shared `default_parent` / `default_tags`; caller-local manifests provide applicable overrides. Create the canonical workspace explicitly with `workspace.create`, then use `workspace.register` to register its folder, `workspace.update-policy` for shared defaults, and `artefact.set-workspace` for deliberate recursive adoption. One Brain/workspace key can have several folder registrations; paths identify
those registrations. Use explicit `workspace.rebind` for a changed binding and
local CLI `workspace.upgrade` / read-only `workspace.discover` for compatibility
and missing-registration recovery. A project and filing defaults are optional.
Terminal hubs preserve historical identity while disallowing new scoped mutation.

## Install

```
_Config/Taxonomy/Living/workspaces.md      <- taxonomy.md
_Config/Templates/Living/Workspaces.md     <- template.md
Workspaces/                                <- create folder
_Workspaces/                               <- create folder (data bucket, infrastructure)
```
