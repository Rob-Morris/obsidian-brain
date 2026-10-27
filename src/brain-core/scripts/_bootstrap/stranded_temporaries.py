"""Stranded atomic-write temporaries under ``.brain/local`` (DD-082, D18).

Both the check and ``runtime.remove-temporaries`` list through this function,
so what the check reports is exactly what the repair may remove. Most writers
of ``.brain/local`` state do not hold the vault mutation lock, so age is the
primary safety rule and the lock only a secondary guard.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path

from _bootstrap.owned_paths import (
    owned_directory, validate_owned_record,
)


LOCAL_STATE_REL = Path(".brain") / "local"
MINIMUM_AGE = timedelta(hours=24)


def find_stranded_temporaries(vault_root: str | Path, now: datetime) -> tuple[str, ...]:
    """Return vault-relative paths of temporaries old enough to be stranded.

    Only top-level regular files directly under ``.brain/local`` qualify: no
    descent, symlinks never followed, the name must match the shared
    atomic-write rule, and both ``mtime`` and ``ctime`` must be older than
    ``MINIMUM_AGE``. An unsafe atomic-shaped endpoint refuses the complete
    inventory, so checks cannot describe an excluded unsafe object as clean.
    """
    from _common._filesystem import is_atomic_write_temporary

    if now.tzinfo is None:
        raise ValueError("stranded-temporaries clock must be timezone-aware")
    try:
        directory = owned_directory(vault_root, LOCAL_STATE_REL)
        names = os.listdir(directory)
    except FileNotFoundError:
        return ()
    threshold = (now - MINIMUM_AGE).astimezone(timezone.utc).timestamp()
    stranded = []
    for name in sorted(names):
        if not is_atomic_write_temporary(name):
            continue
        try:
            info = os.lstat(directory / name)
        except FileNotFoundError:
            continue
        validate_owned_record(info, LOCAL_STATE_REL / name, single_link=True)
        if info.st_mtime >= threshold or info.st_ctime >= threshold:
            continue
        stranded.append((LOCAL_STATE_REL / name).as_posix())
    return tuple(stranded)
