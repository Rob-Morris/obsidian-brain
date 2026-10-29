"""One locked, atomic owner for the selected vault's private local settings."""

from __future__ import annotations

from contextlib import nullcontext
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import tempfile
from typing import Callable

from .file_lock import vault_mutation_lock
from .owned_paths import owned_directory, owned_output_paths
from .yaml_engine import YamlError, dump_yaml_text, load_yaml_text

RELATIVE = ".brain/local/config.yaml"
MISSING_REVISION = "missing"


class LocalConfigError(ValueError):
    """Local settings cannot safely be read or changed without losing data."""


@dataclass(frozen=True, slots=True)
class LocalConfig:
    document: dict
    revision: str
    path: Path


def read_local_config(vault_root: str | Path) -> LocalConfig:
    """Read without creating state or following redirected owned components."""
    try:
        root = Path(vault_root).resolve()
        try:
            root.stat()
        except FileNotFoundError:
            # Native installation may admit a new destination before creating
            # it. An absent root cannot contain an existing local policy.
            return LocalConfig({}, MISSING_REVISION, root / RELATIVE)
        path, = owned_output_paths(vault_root, (RELATIVE,))
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return LocalConfig({}, MISSING_REVISION, path)
        document = load_yaml_text(raw.decode("utf-8"), source=RELATIVE)
        if not isinstance(document, dict):
            raise LocalConfigError(f"{RELATIVE} must contain a YAML mapping")
        return LocalConfig(document, "sha256:" + hashlib.sha256(raw).hexdigest(), path)
    except (OSError, UnicodeError, YamlError) as exc:
        raise LocalConfigError(
            f"Cannot safely read {RELATIVE}; inspect and repair that owned file "
            "before retrying. No settings were replaced."
        ) from exc


def patch_local_config(
    vault_root: str | Path,
    update: Callable[[dict], object],
    *,
    expected_revision: str | None = None,
    lock_held: bool = False,
    before_write: Callable[[], None] | None = None,
) -> LocalConfig:
    """Patch a fresh mapping, preserving unrelated keys; return its new revision.

    The callback mutates its mapping. Owners distinguish authored settings from
    operational markers and perform their branch checkpoint before calling us.
    The lock-held form is for an owner already holding the shared mutation lock.
    """
    lock = nullcontext() if lock_held else vault_mutation_lock(vault_root)
    with lock:
        before = read_local_config(vault_root)
        if expected_revision is not None and expected_revision != before.revision:
            raise LocalConfigError(
                "Local configuration changed; read vault.read-config and retry "
                "with its current local configuration revision."
            )
        document = deepcopy(before.document)
        update(document)
        if document == before.document:
            return before
        if before_write is not None:
            before_write()
        raw = dump_yaml_text(document).encode("utf-8")
        owned_directory(vault_root, ".brain/local", create=True)
        # Re-admit immediately before replacement, including a concurrently
        # introduced redirected endpoint. This is not hostile-race confinement.
        path, = owned_output_paths(vault_root, (RELATIVE,))
        fd, temporary = tempfile.mkstemp(prefix=".config-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            owned_output_paths(vault_root, (RELATIVE,))
            if before_write is not None:
                before_write()
            os.replace(temporary, path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
        return LocalConfig(document, "sha256:" + hashlib.sha256(raw).hexdigest(), path)
