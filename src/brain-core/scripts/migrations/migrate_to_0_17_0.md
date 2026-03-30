# Migration: preferences.json → config.yaml (v0.17.0)

## What changed

Brain vault configuration moves from `.brain/preferences.json` (JSON, flat key-value) to `.brain/config.yaml` (YAML, two-zone model with vault authority and local defaults). This supports the new operator profile system and consolidates all vault configuration into one file.

The config system uses a three-layer merge: shipped template defaults → vault config → local config. If no config.yaml exists, template defaults apply and the vault behaves identically to before.

## Automated (script)

The migration script (`migrate_to_0_17_0.py`) runs on MCP server startup or CLI upgrade:

1. If `.brain/preferences.json` doesn't exist → skip
2. If `.brain/config.yaml` already exists → delete preferences.json only
3. If preferences.json has non-default values → write them into a new `.brain/config.yaml` under the `defaults` zone, then delete preferences.json
4. If preferences.json is empty/default → delete it (no config.yaml created)

**Verification checks:**
- [ ] `.brain/preferences.json` no longer exists
- [ ] If preferences had `artefact_sync_exclude` values → `.brain/config.yaml` exists with those values under `defaults.exclude.artefact_sync`
- [ ] If preferences were empty → no `.brain/config.yaml` created (template defaults apply)

## Manual (naive agent)

If you encounter `.brain/preferences.json` in a vault:

1. Read its contents. If empty (`{}`), delete the file. You're done.
2. If it contains values, create `.brain/config.yaml` with:
   ```yaml
   defaults:
     exclude:
       artefact_sync:
         - value1  # from artefact_sync_exclude list
         - value2
   ```
3. If it contained an `artefact_sync` key with a non-"auto" value, add:
   ```yaml
   defaults:
     artefact_sync: skip  # or whatever the value was
   ```
4. Delete `.brain/preferences.json`
