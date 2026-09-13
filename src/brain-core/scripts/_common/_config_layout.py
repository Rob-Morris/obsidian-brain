"""Single source of truth for authored Brain configuration paths."""

from __future__ import annotations

import os


ROUTER_REL_PATH = os.path.join("_Config", "router.md")
PLUGINS_DIR = "_Plugins"


def classification_subdir(classification: str) -> str:
    if classification == "living":
        return "Living"
    if classification == "temporal":
        return "Temporal"
    raise ValueError("classification must be 'living' or 'temporal'")


def classification_from_subdir(subdir: str) -> str:
    """Inverse of ``classification_subdir``; raises for an unknown folder name."""
    if subdir == "Living":
        return "living"
    if subdir == "Temporal":
        return "temporal"
    raise ValueError(f"unknown taxonomy classification folder: {subdir!r}")


TAXONOMY_DIR = os.path.join("_Config", "Taxonomy")


def taxonomy_rel_path(classification: str, name: str) -> str:
    return os.path.join(
        "_Config", "Taxonomy", classification_subdir(classification), f"{name}.md"
    )


def template_dir(classification: str) -> str:
    return os.path.join("_Config", "Templates", classification_subdir(classification))


def template_rel_path(classification: str, name: str) -> str:
    return os.path.join(template_dir(classification), f"{name}.md")


def plugin_skill_rel_path(name: str) -> str:
    return os.path.join(PLUGINS_DIR, name, "SKILL.md")


def markdown_rel_path(path: str) -> str:
    return path if path.endswith(".md") else path + ".md"
