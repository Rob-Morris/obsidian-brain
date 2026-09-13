# Writing Migrations

Migration scripts run automatically during CLI upgrade (`upgrade.py` or `install.sh`). They handle vault-level data transformations that can't be done by simply copying new files.

Each successful or skipped migration is recorded in `.brain/local/migrations.json`. The runner checks that ledger before executing a migration again, so reinstalling `.brain-core/` into the same vault does not replay historical migrations. Passing `--force` to `upgrade.py` bypasses that ledger and re-runs migrations up to the target version. `install.sh` stays non-destructive here and does not forward installer prompt suppression into upgrade override semantics.

Ledger keys are target-aware:

- `0.27.6` means the standard `post_compile` migration for `v0.27.6`
- `0.29.0@pre_compile_patch` means the `pre_compile_patch` handler for `v0.29.0`

## File naming

`migrate_to_{VERSION}.py` where VERSION uses underscores: `migrate_to_0_19_0.py` for v0.19.0.

The upgrade runner discovers scripts by filename, parses the version, and runs those in the range `(old_version, new_version]` in sorted order.

## Standard targets

Migration scripts can expose one or more target handlers:

- `post_compile` — default. Runs after copy and compile validation succeed.
- `pre_compile_patch` — optional patch stage. Runs after copy but before the compile gate, and is intended for narrow compatibility fixes that unblock the new compiler without skipping rollback safety.

## Required interface

```python
def migrate(vault_root: str) -> dict:
    """Return {"status": "ok", ...} or {"status": "skipped", ...}."""
```

Optional non-default targets are declared with `TARGET_HANDLERS`:

```python
TARGET_HANDLERS = {
    "pre_compile_patch": "patch_pre_compile",
}

def patch_pre_compile(vault_root: str, *, context: dict | None = None) -> dict:
    """Return {"status": "ok", ...} or {"status": "skipped", ...}."""
```

Use a plain string function name in `TARGET_HANDLERS` so the runner can discover the handler without importing the module.
Other shapes are rejected during upgrade-time discovery: no computed dicts, no variable indirection for handler names, and no missing functions.

Migration result status is intentionally strict: `ok` and `skipped` are the
only non-fatal statuses. Any other status, including `blocked` or `warnings`,
halts the upgrade and is not recorded in the migration ledger. If a migration
has non-fatal warnings, return `status: "ok"` or `status: "skipped"` and put
details in a `warnings` field.

## Definition files are not a migration's to edit

Migrations move and rewrite *artefacts*. They never edit definition files
under `_Config/Taxonomy/` or `_Config/Templates/`, managed or unmanaged:

- **Library-managed definitions** propagate through post-upgrade definition
  sync (`sync_definitions.py`), which runs after migrations, overwrites
  `sync_ready` files from the library and records their source hash. A
  migration that hand-rewrote a managed file would flip it into a
  both-sides-changed conflict unless it also patched `.brain/tracking.json`.
- **Unmanaged custom definitions** (created through `type.create`, with no
  manifest or tracking entry) are brought forward by sync's convention pass:
  add a rule to `compile_router.CONVENTION_RULES` (beside the taxonomy
  parser, which also owns the `## Naming` rewriter) when a convention changes.
  Exact matches are rewritten and the previous value recorded; anything else
  is preserved and warned.
- **Checks surface drift** (`check_taxonomy_conventions`) through the same
  rule table, so the check's matcher cannot drift from what sync rewrites —
  the only surface for vaults with `artefact_sync: skip`.

`upgrade.py --dry-run` previews both the migration and the sync pass. See
DD-072 for the decision and its alternatives.

## Import constraints

Migration scripts run inside the upgrade process. When the upgrade copies new files to disk, old script modules may still be cached in `sys.modules`. The runner now executes each migration inside a fresh import context rooted at the upgraded `.brain-core/scripts/` tree, so local imports resolve against the just-copied files rather than stale module cache entries.

**Safe:**
```python
from _common import parse_frontmatter, safe_write, serialize_frontmatter
from rename import rename_and_update_links
```

## Guidelines

- Migrations must be **idempotent** — running twice produces the same result.
- Return `{"status": "skipped"}` with no side effects when there's nothing to do.
- `pre_compile_patch` handlers should be minimal compatibility repairs only. If they mutate vault files, rely on the upgrade runner's snapshot/rollback context rather than rolling their own partial rollback scheme.

The v0.53 pre-compile patch adds lifecycle rows to legacy taxonomies that
already declared a complete `## Shaping` contract. This lets the strict v0.53
compiler remain authoritative without making an older customised vault
unbootable during upgrade.
- Use `rename_and_update_links()` when renaming files — it handles vault-wide wikilink updates.
- Include a companion `.md` file documenting what the migration does, verification checks, and manual steps for agents without MCP tools.
