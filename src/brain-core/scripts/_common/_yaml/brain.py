"""Brain-owned helpers for standalone YAML mapping files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .engine import YamlError, dump_yaml_text, load_yaml_file, load_yaml_text


def load_mapping_text(text: str, *, source: str = "<string>") -> dict[str, Any]:
    """Parse Brain YAML text and require a mapping root."""
    data = load_yaml_text(text, source=source)
    if not isinstance(data, dict):
        raise YamlError(f"{source}: root document must be a mapping")
    return data


def load_mapping_file(path: str | Path) -> dict[str, Any]:
    """Parse a Brain YAML file and require a mapping root."""
    data = load_yaml_file(path)
    if not isinstance(data, dict):
        raise YamlError(f"{path}: root document must be a mapping")
    return data


def dump_mapping_text(data: dict[str, Any]) -> str:
    """Serialise a mapping into Brain's standalone YAML subset."""
    if not isinstance(data, dict):
        raise TypeError("Brain YAML mapping dump expects a dict root")
    return dump_yaml_text(data)
