"""Lifecycle helpers for duplicate frontmatter detection and repair."""

from __future__ import annotations

import os
from pathlib import Path

from _common import (
    inspect_duplicate_frontmatter_document,
    now_iso,
    safe_write_active_or_archived_artefact,
    serialize_frontmatter,
)


_ARTEFACT_TOP_LEVEL_SYSTEM_ROOTS = {"_Temporal", "_Archive"}


def iter_candidate_artefact_markdown_files(vault_root: str | Path):
    """Yield vault-relative markdown paths that may be artefacts.

    This walk is intentionally filesystem-driven rather than router-driven so
    repair and migration can still operate when the compiled router is stale or
    missing. Candidate roots are every non-hidden top-level content folder,
    plus the canonical temporal/archive system roots.
    """
    vault_root = Path(vault_root)
    candidate_roots = []
    for entry in sorted(os.listdir(vault_root)):
        if entry.startswith(".") or entry == "_Config":
            continue
        if entry.startswith("_") and entry not in _ARTEFACT_TOP_LEVEL_SYSTEM_ROOTS:
            continue
        full = vault_root / entry
        if full.is_dir():
            candidate_roots.append(full)

    for root in candidate_roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for filename in filenames:
                if not filename.endswith(".md"):
                    continue
                yield os.path.relpath(os.path.join(dirpath, filename), vault_root)


def detect_duplicate_frontmatter_documents(vault_root: str | Path, *, unreadable=None) -> list[dict]:
    """Return duplicate-frontmatter artefacts without mutating the vault."""
    vault_root = Path(vault_root)
    findings = []
    for rel_path in iter_candidate_artefact_markdown_files(vault_root):
        abs_path = vault_root / rel_path
        try:
            content = abs_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            if unreadable is not None:
                unreadable.append(rel_path)
            continue
        duplicate = inspect_duplicate_frontmatter_document(content)
        if duplicate is None:
            continue
        findings.append(
            {
                "file": rel_path,
                "outer_fields": duplicate["outer_fields"],
                "nested_fields": duplicate["nested_fields"],
                "merged_fields": duplicate["merged_fields"],
                "body": duplicate["body"],
            }
        )
    return findings


def plan_duplicate_frontmatter_documents(vault_root, *, effective_at=None, unreadable=None):
    """Render duplicate-frontmatter repairs without persisting them."""
    effective_at = effective_at or now_iso()
    findings = detect_duplicate_frontmatter_documents(vault_root, unreadable=unreadable)
    return [{**item, "merged_fields": {**item["merged_fields"], "modified": effective_at}}
            for item in findings]


def normalize_duplicate_frontmatter_documents(
    vault_root: str | Path,
    *,
    dry_run: bool = False,
    prepared_findings=None,
    effective_at=None,
) -> dict:
    """Merge duplicate frontmatter blocks across vault artefacts."""
    vault_root = Path(vault_root)
    findings = (prepared_findings if prepared_findings is not None
                else plan_duplicate_frontmatter_documents(vault_root, effective_at=effective_at))
    if not findings:
        return {
            "status": "skipped",
            "dry_run": dry_run,
            "updated": 0,
            "files": [],
            "actions": [],
        }

    files = [item["file"] for item in findings]
    actions = [f"normalised duplicate frontmatter in {rel_path}" for rel_path in files]
    if not dry_run:
        for item in findings:
            fields = dict(item["merged_fields"])
            safe_write_active_or_archived_artefact(
                str(vault_root / item["file"]),
                serialize_frontmatter(fields, body=item["body"]),
                bounds=str(vault_root),
            )

    return {
        "status": "ok",
        "dry_run": dry_run,
        "updated": len(findings),
        "files": files,
        "actions": actions,
    }
