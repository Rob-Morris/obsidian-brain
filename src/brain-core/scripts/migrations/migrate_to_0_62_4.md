# v0.62.4 terminal-status living-key compatibility migration

Brain Core v0.59.1 began enforcing canonical living keys inside `+Status`
folders. Vaults that had already recorded the v0.31 key migration could
therefore gain new validation errors after upgrade for older keyless terminal
records.

`migrate_to_0_62_4.py` runs through the normal post-compile migration stage. It
scans every living artefact type so generated keys cannot collide with an
existing key, then backfills only records inside `+Status` folders whose key is
missing or invalid. Existing keys and keyless records outside status folders
are preserved. The upgrade runner snapshots living artefact roots before this
stage and restores them if migration or later cutover work fails.

The migration is idempotent. A second run reports `skipped`.
