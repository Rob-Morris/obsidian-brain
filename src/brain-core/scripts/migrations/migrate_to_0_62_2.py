#!/usr/bin/env python3
"""Update the exact shipped shaping-transcript router condition."""

from __future__ import annotations

import os

from _common import safe_write


VERSION = "0.62.2"
TARGET_HANDLERS = {"pre_compile_patch": "patch_pre_compile"}
ROUTER_PATH = os.path.join("_Config", "router.md")
OLD_RULE = (
    "- After shaping each artefact through Q&A → "
    "[[_Config/Taxonomy/Temporal/shaping-transcripts]]"
)
NEW_RULE = (
    "- When a confirmed shaping plan selects a Brain transcript → "
    "[[_Config/Taxonomy/Temporal/shaping-transcripts]]"
)


def patch_pre_compile(
    vault_root: str,
    *,
    context: dict[str, object],
) -> dict[str, object]:
    """Replace only the previous shipped default, preserving custom rules."""

    del context
    router_path = os.path.join(vault_root, ROUTER_PATH)
    if not os.path.isfile(router_path):
        return {"status": "skipped", "updated": []}

    with open(router_path, "r", encoding="utf-8", newline="") as handle:
        content = handle.read()
    lines = content.splitlines(keepends=True)
    matched = False
    updated_lines = []
    for line in lines:
        body = line.rstrip("\r\n")
        ending = line[len(body) :]
        if body == OLD_RULE:
            updated_lines.append(NEW_RULE + ending)
            matched = True
        else:
            updated_lines.append(line)
    if not matched:
        return {"status": "skipped", "updated": []}

    safe_write(
        router_path,
        "".join(updated_lines),
        bounds=vault_root,
    )
    return {"status": "ok", "updated": [ROUTER_PATH]}
