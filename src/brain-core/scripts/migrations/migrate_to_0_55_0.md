# v0.55 command-profile migration

The 0.55 breaking cutover replaces legacy aggregate and superseded granular
MCP grants in `.brain/config.yaml` with exact commands from the installed
catalogue. Exact historical built-ins become the five cumulative shipped
profiles; custom profiles expand only authority they already expressed.

The migration validates every profile and target before writing. Unknown or
malformed authority aborts the upgrade, allowing the checked upgrade
transaction to restore the previous Brain Core and shared configuration.
There is no runtime alias or compatibility mapping after this one-time step.

Verify after upgrade with `brain vault read-config` and confirm that every
profile contains dotted command names. Reader, contributor, maintainer,
operator and administrator contain 37, 63, 76, 85 and 86 application commands
respectively when the historical built-in set was migrated; MCP projects the
eligible 37, 63, 74, 77 and 78-command subsets.
