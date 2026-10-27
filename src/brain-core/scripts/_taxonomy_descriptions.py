"""Taxonomy description extraction shared by retrieval and processing."""

from __future__ import annotations

from functools import lru_cache
import os
import re

from _common._source_access import resolve_public_source


_section_cache: dict[str, re.Pattern] = {}


def extract_type_description(vault_root, artefact):
    """Read taxonomy file and extract one-liner + Purpose + When To Use/Trigger."""
    taxonomy_file = artefact.get("taxonomy_file")
    if not taxonomy_file:
        return ""

    vault_root_str = str(vault_root)
    try:
        abs_path = resolve_public_source(vault_root_str, taxonomy_file)
        modified = os.stat(abs_path).st_mtime_ns
    except (OSError, ValueError):
        return ""

    return _read_description(vault_root_str, str(abs_path), modified)


@lru_cache(maxsize=256)
def _read_description(vault_root_str, taxonomy_file, modified):
    """Read one taxonomy description, bounded by vault, file, and modification time."""
    try:
        abs_path = resolve_public_source(vault_root_str, taxonomy_file)
        with open(abs_path, "r", encoding="utf-8") as handle:
            content = handle.read()
    except (OSError, UnicodeDecodeError, ValueError):
        return ""

    parts = []

    h1_match = re.search(r"^# .+\n\n(.+)", content, re.MULTILINE)
    if h1_match:
        parts.append(h1_match.group(1).strip())

    for section_name in ("Purpose", "When To Use", "Trigger"):
        body = _extract_section(content, section_name)
        if body:
            parts.append(body)

    description = "\n\n".join(parts)
    return description


def _extract_section(content, heading):
    """Extract the body of a ## heading section, stopping at the next ## or EOF."""
    pattern = _section_cache.get(heading)
    if pattern is None:
        pattern = re.compile(
            rf"^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)",
            re.MULTILINE | re.DOTALL,
        )
        _section_cache[heading] = pattern
    match = pattern.search(content)
    if match:
        return match.group(1).strip()
    return ""
