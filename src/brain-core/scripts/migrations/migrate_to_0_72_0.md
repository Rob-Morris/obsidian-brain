# Migration to v0.72.0

Workspace command permissions are converted by the coordinated upgrade's
pre-compile patch. The captured installed source version determines eligibility;
current-version loads and forced reapplications retain the new register grants.

Historical permission migrations run first through the existing runner, journal
and ledger, so their retirement of old register cannot strip the replacement.
Released migrations and their retired-command set remain unchanged.

In shared profile allow-lists and both layers' explicit initial selections and
overrides, drop historical `workspace.register` and `workspace.bind` first, then
map `workspace.ensure-registration` to `workspace.create` and `workspace.setup`
to the narrower `workspace.register`. Preserve ordering, deduplication, Boolean
override values and empty local mappings. Conflicting target overrides refuse
for explicit resolution. Local profile definitions remain ignored and untouched.
Exact recognised shipped profiles use canonical built-in permission generation;
custom restrictions receive only the narrowed mapping.

No custom rebind/upgrade/discover grant or live consent is inferred. Both files
are planned before writing; failures roll back under the normal upgrade journal.
Direct invocation without captured source evidence refuses.

## Verification

Inspect `brain vault read-config --json` and `brain command list --json` after
upgrade. Confirm replacement permissions retain authored restrictions and an
already-current forced upgrade does not remove `workspace.register`.

## Manual steps without MCP tools

Use the coordinated local upgrade. For a reported conflicting target override,
choose its intended value explicitly in the named configuration layer and rerun.
