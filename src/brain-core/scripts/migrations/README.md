# Writing Migrations

Migration scripts run automatically during CLI upgrade (`upgrade.py` or `install.sh`). They handle vault-level data transformations that can't be done by simply copying new files.

## File naming

`migrate_to_{VERSION}.py` where VERSION uses underscores: `migrate_to_0_19_0.py` for v0.19.0.

The upgrade runner discovers scripts by filename, parses the version, and runs those in the range `(old_version, new_version]` in sorted order.

## Required interface

```python
def migrate(vault_root: str) -> dict:
    """Return {"status": "ok", ...} or {"status": "skipped", ...}."""
```

## Import constraints

Migration scripts run inside the upgrade process. When the upgrade copies new files to disk, **the old module versions may still be cached in `sys.modules`**. The upgrade runner reloads `_common` and `rename` before executing migrations, so imports from those modules are safe.

If your migration imports from a module **not** in the reload list (`_common`, `rename`), add it to `_MIGRATION_DEPS` in `upgrade.py`. Otherwise the import will silently pick up the old cached version, which may be missing new symbols or have stale behaviour.

**Safe:**
```python
from _common import parse_frontmatter, safe_write, serialize_frontmatter
from rename import rename_and_update_links
```

**Needs `_MIGRATION_DEPS` update first:**
```python
from create import create_artefact  # not in reload list — will get stale module
```

## Guidelines

- Migrations must be **idempotent** — running twice produces the same result.
- Return `{"status": "skipped"}` with no side effects when there's nothing to do.
- Use `rename_and_update_links()` when renaming files — it handles vault-wide wikilink updates.
- Include a companion `.md` file documenting what the migration does, verification checks, and manual steps for agents without MCP tools.
