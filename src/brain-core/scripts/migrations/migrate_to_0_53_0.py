#!/usr/bin/env python3
"""Make legacy shaping taxonomies satisfy the v0.53 lifecycle contract."""

from __future__ import annotations

import os
import re
import sys
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import compile_router
from _common import safe_write


VERSION = "0.53.0"
TARGET_HANDLERS = {"pre_compile_patch": "patch_pre_compile"}


def _snapshot_file(context: dict[str, Any], path: str) -> None:
    snapshot = context.get("snapshot_file")
    if callable(snapshot):
        snapshot(path)


def _append_inline_statuses(content: str, missing: list[str]) -> str | None:
    match = re.search(r"^(status:\s*\S+\s*#\s*)(.+)$", content, re.MULTILINE)
    if not match:
        return None
    suffix = match.group(2).rstrip()
    addition = " | ".join(missing)
    return content[: match.start(2)] + f"{suffix} | {addition}" + content[match.end(2) :]


def _unique_statuses(statuses: list[str]) -> list[str]:
    return list(dict.fromkeys(statuses))


def _lifecycle_rows(statuses: list[str], added: set[str]) -> str:
    return "".join(
        f"| `{status}` | "
        + (
            "Added for shaping compatibility."
            if status in added
            else "Existing lifecycle status."
        )
        + " |\n"
        for status in statuses
    )


def _append_lifecycle_rows(
    content: str,
    statuses: list[str],
    added: list[str],
) -> str:
    lifecycle = re.search(
        r"^## Lifecycle\s*\n(.*?)(?=^## |\Z)",
        content,
        re.MULTILINE | re.DOTALL,
    )
    added_set = set(added)
    if lifecycle:
        body = lifecycle.group(1).rstrip()
        table_values = re.findall(
            r"^\|\s*`([^`]+)`\s*\|", body, re.MULTILINE
        )
        has_separator = bool(
            re.search(r"^\|\s*:?-{3,}", body, re.MULTILINE)
        )
        if table_values:
            if not has_separator:
                first_row = re.search(r"^\|\s*`", body, re.MULTILINE)
                body = (
                    body[: first_row.start()]
                    + "| Status | Meaning |\n|---|---|\n"
                    + body[first_row.start() :]
                )
            rows = _lifecycle_rows(
                [status for status in statuses if status not in table_values],
                added_set,
            )
            row_matches = list(
                re.finditer(
                    r"^\|\s*`[^`]+`\s*\|[^\n]*(?:\n|$)",
                    body,
                    re.MULTILINE,
                )
            )
            insert_at = row_matches[-1].end()
            separator = "" if body[:insert_at].endswith("\n") else "\n"
            replacement = (
                body[:insert_at]
                + separator
                + rows
                + body[insert_at:]
                + "\n"
            )
        elif has_separator:
            replacement = (
                body + "\n" + _lifecycle_rows(statuses, added_set) + "\n"
            )
        else:
            table = (
                "| Status | Meaning |\n"
                "|---|---|\n"
                + _lifecycle_rows(statuses, added_set)
            )
            replacement = (body + "\n\n" if body else "") + table + "\n"
        return content[: lifecycle.start(1)] + replacement + content[lifecycle.end(1) :]

    shaping_heading = re.search(r"^## Shaping\s*$", content, re.MULTILINE)
    section = (
        "## Lifecycle\n\n"
        "| Status | Meaning |\n"
        "|---|---|\n"
        + _lifecycle_rows(statuses, added_set)
        + "\n"
    )
    if shaping_heading:
        return content[: shaping_heading.start()] + section + content[shaping_heading.start() :]
    return content.rstrip() + "\n\n" + section


def _patch_taxonomy(content: str) -> tuple[str, list[str]]:
    shaping = compile_router._parse_shaping_section(content)
    if not shaping:
        return content, []
    statuses = compile_router.parse_status_enum(content) or []
    required = _unique_statuses(["shaping", shaping["completion_status"]])
    missing = [
        status
        for status in required
        if status not in statuses
    ]
    if not missing:
        return content, []

    patched = _append_inline_statuses(content, missing)
    if patched is None:
        patched = _append_lifecycle_rows(
            content,
            _unique_statuses([*statuses, *missing]),
            missing,
        )
    patched_statuses = compile_router.parse_status_enum(patched) or []
    lost = [
        status
        for status in _unique_statuses([*statuses, *required])
        if status not in patched_statuses
    ]
    if lost:
        raise ValueError(
            "v0.53 shaping lifecycle repair failed its post-condition; "
            "statuses still undeclared: " + ", ".join(lost)
        )
    return patched, missing


def patch_pre_compile(
    vault_root: str, *, context: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Patch only the legacy shaping validation error blocking compilation."""
    context = context if context is not None else {}
    compile_error = context.get("compile_error")
    result: dict[str, Any] = {"status": "skipped", "patched": [], "warnings": []}
    if (
        not compile_error
        or compile_router.SHAPING_LIFECYCLE_ERROR_CODE not in compile_error
    ):
        return result

    taxonomy_root = os.path.join(vault_root, "_Config", "Taxonomy")
    for classification in ("Living", "Temporal"):
        folder = os.path.join(taxonomy_root, classification)
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if not name.endswith(".md"):
                continue
            path = os.path.join(folder, name)
            with open(path, "r", encoding="utf-8") as handle:
                content = handle.read()
            try:
                patched, added = _patch_taxonomy(content)
            except ValueError as exc:
                result["warnings"].append(
                    {
                        "target": os.path.relpath(path, vault_root),
                        "message": f"taxonomy was not auto-repaired: {exc}",
                    }
                )
                continue
            if not added:
                continue
            _snapshot_file(context, path)
            safe_write(path, patched, bounds=vault_root)
            result["patched"].append(
                {
                    "target": os.path.relpath(path, vault_root),
                    "added_statuses": added,
                }
            )

    if result["patched"]:
        result["status"] = "ok"
    return result
