"""Read-only source identity shared by maintenance planning and detached workers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def file_identity(path: Path) -> str | None:
    if path.is_symlink():
        raise ValueError(f"maintenance input is a symbolic link: {path}")
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError(f"maintenance input is not a file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def source_manifest(vault_root: str | Path) -> dict:
    """Bind current artefact/configuration inputs, excluding generated cache files."""
    from _common import iter_artefact_paths, scan_living_types, scan_temporal_types
    import compile_router

    root = Path(vault_root)
    paths = {".brain-core/VERSION", ".brain-core/session-core.md", ".brain/config.yaml",
             ".brain-core/brain_mcp/requirements.txt", ".brain/local/config.yaml",
             ".brain/local/semantic-model-manifest.json"}
    for info in scan_living_types(root) + scan_temporal_types(root):
        paths.update(iter_artefact_paths(root, info))
    trees = ["_Config", ".brain-core/defaults", ".brain-core/artefact-library"]
    trees.extend((compile_router.STYLES_DIR, compile_router.MEMORIES_DIR,
                  compile_router.SKILLS_DIR, compile_router.CORE_SKILLS_DIR,
                  compile_router.PLUGINS_DIR))
    for relative in set(trees):
        base = root / relative
        if base.is_symlink():
            raise ValueError(f"maintenance source directory is a symbolic link: {relative}")
        if base.is_dir():
            for path in base.rglob("*"):
                if path.is_symlink():
                    raise ValueError(f"maintenance source is a symbolic link: {path}")
                if path.is_file():
                    paths.add(path.relative_to(root).as_posix())
    entries = [(relative, file_identity(root / relative)) for relative in sorted(paths)]
    encoded = json.dumps(entries, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return {"sha256": "sha256:" + hashlib.sha256(encoded).hexdigest(),
            "file_count": sum(revision is not None for _path, revision in entries)}
