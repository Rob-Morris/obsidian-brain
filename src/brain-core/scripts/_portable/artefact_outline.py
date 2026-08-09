"""Portable structural-outline semantics without parser dependencies."""

from __future__ import annotations

from _common import (
    MissingFileResult,
    load_compiled_router,
    outline_structural_nodes,
    parse_frontmatter,
)
from _portable.artefact_read import read_artefact


def outline_artefact(vault_root, router, path):
    content = read_artefact(router, vault_root, path)
    if isinstance(content, MissingFileResult):
        raise FileNotFoundError(content.message)
    if isinstance(content, dict) and "error" in content:
        raise ValueError(content["error"])
    _fields, body = parse_frontmatter(content)
    return {
        "path": path,
        "targets": outline_structural_nodes(body),
    }


def outline_from_vault(vault_root, path):
    router = load_compiled_router(vault_root)
    if "error" in router:
        raise ValueError(router["error"])
    return outline_artefact(str(vault_root), router, path)
