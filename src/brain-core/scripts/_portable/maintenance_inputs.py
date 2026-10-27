"""Read-only source identity shared by maintenance planning and detached workers."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from _bootstrap.owned_paths import UnsafeFilesystemLayoutError, owned_directory, owned_file
from _common._source_access import SourceAccessError, resolve_content_source


# Installation/configuration records are not public-document exceptions.
_FIXED_INPUTS = frozenset({
    ".brain-core/VERSION", ".brain-core/session-core.md", ".brain/config.yaml",
    ".brain-core/brain_mcp/requirements.txt", ".brain-core/brain_mcp/requirements-semantic.txt",
    ".brain/local/config.yaml", ".brain/local/semantic-model-manifest.json",
})


def file_identity(path: Path) -> str | None:
    """Hash a caller-admitted source or legacy output/tool observation.

    This byte helper grants no vault authority. Corpus and managed-record owners
    below admit their selected target before calling it; other legacy callers
    retain their own source/output selection contracts.
    """
    try:
        if path.is_symlink():
            raise SourceAccessError("Maintenance input is a symbolic link")
        if not path.exists():
            return None
        if not path.is_file():
            raise SourceAccessError("Maintenance input is not a regular file")
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except FileNotFoundError:
        return None
    except UnsafeFilesystemLayoutError:
        raise
    except OSError:
        raise SourceAccessError("Maintenance input could not be read") from None
    return "sha256:" + digest.hexdigest()


def public_file_identity(vault_root: str | Path, relative: str | Path) -> str | None:
    """Admit public content or an explicitly selected archive before hashing."""
    try:
        source = resolve_content_source(vault_root, relative)
    except FileNotFoundError:
        return None
    return file_identity(source)


def owned_file_identity(vault_root: str | Path, relative: str | Path) -> str | None:
    """Observe a record selected by its installation/configuration/output owner."""
    try:
        source = owned_file(vault_root, relative)
    except FileNotFoundError:
        return None
    except UnsafeFilesystemLayoutError:
        raise
    except OSError:
        raise SourceAccessError("Cannot observe the selected maintenance record") from None
    return file_identity(source)


def source_manifest(vault_root: str | Path) -> dict:
    """Bind admitted corpus and fixed configuration inputs, excluding caches."""
    from _common import iter_artefact_paths, scan_living_types, scan_temporal_types
    import compile_router

    root = Path(vault_root)
    paths = set()
    try:
        for info in scan_living_types(root) + scan_temporal_types(root):
            paths.update(relative for relative in iter_artefact_paths(root, info)
                         if not any(part.startswith(".") for part in Path(relative).parts))
        trees = {"_Config", ".brain-core/artefact-library",
                 compile_router.STYLES_DIR, compile_router.MEMORIES_DIR,
                 compile_router.SKILLS_DIR, compile_router.CORE_SKILLS_DIR,
                 compile_router.PLUGINS_DIR}
        for relative in trees:
            base = root / relative
            if base.is_dir():
                for directory, directories, files in os.walk(base):
                    directories[:] = [name for name in directories if not name.startswith(".")]
                    for name in files:
                        if name.startswith("."):
                            continue
                        path = Path(directory) / name
                        if path.is_file():
                            paths.add(path.relative_to(root).as_posix())
    except OSError:
        raise SourceAccessError("Cannot inspect the selected maintenance corpus") from None

    owned_inputs = set(_FIXED_INPUTS)
    try:
        try:
            defaults = owned_directory(root, ".brain-core/defaults")
        except FileNotFoundError:
            defaults = None
        if defaults is not None:
            for directory, directories, files in os.walk(defaults):
                for name in directories:
                    relative = (Path(directory) / name).relative_to(root.resolve())
                    owned_directory(root, relative)
                for name in files:
                    relative = (Path(directory) / name).relative_to(root.resolve()).as_posix()
                    # Validate each endpoint before a later hash can open it.
                    owned_file(root, relative)
                    owned_inputs.add(relative)
    except UnsafeFilesystemLayoutError:
        raise
    except OSError:
        raise SourceAccessError("Cannot inspect maintenance configuration inputs") from None

    entries = [(relative, owned_file_identity(root, relative)
                if relative in owned_inputs else public_file_identity(root, relative))
               for relative in sorted(paths | owned_inputs)]
    encoded = json.dumps(entries, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return {"sha256": "sha256:" + hashlib.sha256(encoded).hexdigest(),
            "file_count": sum(revision is not None for _path, revision in entries)}
