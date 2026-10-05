"""Released migration identity: a shipped migration keeps its version and targets.

A migration at or below the released ``VERSION`` has already been recorded in
vaults' ledgers under its version and target keys. Renaming, removing or
re-targeting it would make those records meaningless, so its identity is
permanent; its body may still be corrected for vaults that have not yet
upgraded, and new behaviour ships as a new migration above ``VERSION``.

The target rule itself lives in ``upgrade.py`` (``declared_migration_targets``),
so this policy can never disagree with the runner that writes the keys.
"""

from __future__ import annotations

import posixpath

from upgrade import MigrationDefinitionError, declared_migration_targets, migration_file_version

from _repository_contracts.view import RepositoryView


MIGRATIONS_ROOT = "src/brain-core/scripts/migrations"


def migration_version(path: str) -> tuple[int, ...] | None:
    """Return the version a migration path declares, or None for any other path."""
    version = migration_file_version(posixpath.basename(path))
    if version is None or not path.startswith(f"{MIGRATIONS_ROOT}/"):
        return None
    return tuple(int(part) for part in version.split("."))


def released_migration_paths(paths, boundary: tuple[int, ...]) -> set[str]:
    """Select the migration paths whose version is at or below the released boundary."""
    selected = set()
    for path in paths:
        version = migration_version(path)
        if version is not None and version <= boundary:
            selected.add(path)
    return selected


def migration_targets(text: str, path: str) -> frozenset[str]:
    return declared_migration_targets(text, filename=path)


def validate_migration_identity(
    released_at_head: dict[str, str],
    view: RepositoryView,
) -> list[str]:
    """Require each released migration (path → HEAD text) to keep its path and target set."""
    errors: list[str] = []
    for path, head_text in sorted(released_at_head.items()):
        label = ".".join(str(part) for part in migration_version(path))
        if not view.exists(path):
            errors.append(
                f"{path}: released migration {label} cannot be removed or renamed; "
                "ship a correction as a new migration"
            )
            continue
        try:
            head_targets = migration_targets(head_text, path)
            staged_targets = migration_targets(view.read_text(path), path)
        except MigrationDefinitionError as exc:
            errors.append(f"{path}: {exc}")
            continue
        if head_targets != staged_targets:
            errors.append(
                f"{path}: released migration {label} targets changed from "
                f"{sorted(head_targets)} to {sorted(staged_targets)}; "
                "ship a correction as a new migration"
            )
    return errors
