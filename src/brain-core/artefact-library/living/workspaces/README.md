# Workspaces

Workspace hub files. One file per workspace, linking brain artefacts to a bounded container of working files that fall outside the vault taxonomy.

Membership is explicit `workspace: workspace/{key}` metadata, not a tag-derived convention. Hubs own shared `default_parent` / `default_tags`; caller-local manifests provide applicable overrides. Use `workspace.setup` for registration and binding, `workspace.update-policy` for shared defaults, and `artefact.set-workspace` for deliberate recursive adoption. Terminal hubs preserve historical identity while disallowing new scoped mutation.

## Install

```
_Config/Taxonomy/Living/workspaces.md      <- taxonomy.md
_Config/Templates/Living/Workspaces.md     <- template.md
Workspaces/                                <- create folder
_Workspaces/                               <- create folder (data bucket, infrastructure)
```
