# Migration to v0.71.0

Removes the grants for the retired `workspace.bind` and `workspace.register`
commands. Each wrote only one end of a workspace link; `workspace.setup` makes a
link and `workspace.unregister` removes one, so neither retired command has a
replacement and the grants are dropped rather than mapped.

The migration edits `.brain/config.yaml` and `.brain/local/config.yaml` where
present:

- every `vault.profiles.<name>.allow` list in the shared file (the local file's
  `vault` zone is never merged, so it is left alone);
- `defaults.access.initial.commands` when initial access is `explicit`;
- `defaults.access.overrides`. An override mapping emptied by the change is
  kept as `{}`, because removing a local mapping would let shared overrides
  take effect.

A file is rewritten only when it held one of the two grants. Nothing is added
to any list, so no grant widens. The historical profile migrations drop the same
two IDs, so an upgrade from any earlier version arrives at the same result.

## Verification

- `brain command list --json` and `brain vault read-config --json` succeed
  against the vault.
- No profile, initial-command list or override in either file names
  `workspace.bind` or `workspace.register`.

## Manual steps without MCP tools

Remove the two IDs by hand from the lists and mappings above, then run the
verification commands.
