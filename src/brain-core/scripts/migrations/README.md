# Writing Migrations

Migration scripts run automatically during CLI upgrade (`upgrade.py` or `install.sh`). They handle vault-level data transformations that can't be done by simply copying new files.

Each successful or skipped migration is recorded in `.brain/local/migrations.json` straight after it runs. The runner selects only migrations above the installed `.brain-core/VERSION`, up to the target, whose ledger key is not yet recorded, so reinstalling `.brain-core/` into the same vault does not replay historical migrations. Nothing re-runs a recorded migration: `upgrade.py --force` re-applies the same version's core and selects no migration, and a correction ships as a new migration. `.brain-core/VERSION` is written last, after every migration. Before a migration may change a path, the runner journals that path's original bytes write-ahead, outside the vault, so a failure or a kill is rolled back (by the run itself, or by the next upgrade from the journal) and the migration reruns from the pre-run state. The runner holds the vault mutation lock from before the migrations until `VERSION` is written, and the lock is not re-entrant: a migration must not take it, in process or through a subprocess, or it deadlocks against the run it belongs to.

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
- Migrations should be **restartable** — they converge from any partial application of themselves. On the machine that ran the upgrade, the rollback journal restores a killed run before the migration reruns, so this is defence in depth: it matters when the journal is unavailable (the vault moved to another machine, or the journal was deleted), where the next upgrade applies the migration again to content it may have half-changed. Write the file that can grant the least last (0.68.0 writes the local layer before the shared file for this reason).
- Declare **`prospective_effects(vault_root)`** when you can, and declare it exhaustively: every file the migration may create or change, and every existing directory whose entries it adds or removes (declared as the directory, which the runner journals as a tree so rollback can prune what the migration creates under it). The journal holds exactly those paths plus what every run journals anyway (`.brain/` and the migration ledger); a migration that declares nothing makes the runner journal `_Config/` and, after compile, every artefact folder before it runs, which reads the whole vault. A migration above the released `VERSION` must declare its effects (a test enforces it), so the broad scope stays a legacy path.
- A released migration keeps its **identity**: once a migration's version is at or below the repository `VERSION`, its file name and the set of targets it declares (`migrate` and the `TARGET_HANDLERS` keys) are permanent, because vault ledgers already record it under those keys. A repository contract rejects a staged rename, removal or re-targeting. Its body may still be corrected for vaults that have not yet upgraded; new behaviour is a new migration above `VERSION`.
- On `dev` and in the lab, an unreleased migration (above the repository `VERSION`) is never selected by an upgrade, because selection is capped at the source `VERSION`. Apply the module directly (`migrate_to_X.migrate(vault_root)`) to exercise it; that writes no ledger record, so dev propagation from `src/brain-core` still passes the content guard that refuses a source older than the recorded content.
- Return `{"status": "skipped"}` with no side effects when there's nothing to do.
- `pre_compile_patch` handlers should be minimal compatibility repairs only. If they mutate vault files, rely on the upgrade runner's snapshot/rollback context rather than rolling their own partial rollback scheme.

The v0.53 pre-compile patch adds lifecycle rows to legacy taxonomies that
already declared a complete `## Shaping` contract. This lets the strict v0.53
compiler remain authoritative without making an older customised vault
unbootable during upgrade.
- Use `rename_and_update_links()` when renaming files — it handles vault-wide wikilink updates.
- Include a companion `.md` file documenting what the migration does, verification checks, and manual steps for agents without MCP tools.
