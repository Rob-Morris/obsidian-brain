"""Portable broken-link checks shared by fix_links.py and check.py."""

import os

from _common import (
    INDEX_SKIP_DIRS,
    build_vault_file_index,
    check_wikilinks_in_file,
    discover_temporal_prefixes,
    table_breaking_wikilink_findings,
)
from .vault_files import resolve_public_vault_file


def check_broken_wikilinks(vault_root, router, file_index=None, *, ctx=None):
    """Check for wikilinks that target non-existent or ambiguous files.

    Private dotfiles and dotfolders are outside the source corpus.
    Infrastructure folders (``_Config``) are excluded from the walk because
    template and taxonomy files contain intentional placeholder wikilinks
    that generate false positives. Those files remain in the file index so
    they stay valid link targets — they are just not themselves checked.
    """
    findings = []
    if file_index is None:
        file_index = ctx.file_index if ctx is not None else build_vault_file_index(vault_root)
    temporal_prefixes = discover_temporal_prefixes(file_index["md_basenames"])

    for dirpath, dirnames, filenames in os.walk(vault_root):
        dirnames[:] = [
            d for d in dirnames
            if not d.startswith(".") and d not in INDEX_SKIP_DIRS and d not in {"_Archive", "_Config"}
        ]
        for fname in filenames:
            if fname.startswith(".") or not fname.endswith(".md"):
                continue
            rel_path = os.path.relpath(os.path.join(dirpath, fname), vault_root)

            try:
                source = resolve_public_vault_file(vault_root, rel_path)
            except (ValueError, OSError):
                findings.append({
                    "check": "source_admission", "severity": "warning", "file": rel_path,
                    "message": "Source excluded; link-check coverage is incomplete.",
                })
                continue
            try:
                if ctx is not None:
                    text = ctx.read_text(source)
                else:
                    with open(source, encoding="utf-8") as handle:
                        text = handle.read()
            except (OSError, UnicodeDecodeError):
                continue
            file_findings = check_wikilinks_in_file(
                vault_root, rel_path,
                file_index=file_index,
                temporal_prefixes=temporal_prefixes,
                text=text,
            )
            findings.extend(describe_link_findings(rel_path, text, file_findings))

    return findings


def check_from_vault(vault_root, file_index=None):
    """Run the portable check without requiring compiled-router availability."""
    return check_broken_wikilinks(str(vault_root), {}, file_index=file_index)


def describe_link_findings(rel_path, text, file_findings):
    """Render source diagnostics from an existing wikilink analysis."""
    findings = []
    for table_finding in table_breaking_wikilink_findings(text, rel_path):
        findings.append({
            "check": "table_wikilink_alias",
            "severity": "warning",
            "file": rel_path,
            "line": table_finding["line"],
            "message": table_finding["message"],
            "fix": "Remove the alias or rewrite the table cell with a plain wikilink.",
        })

    for finding in file_findings:
        stem = finding["stem"]
        if finding["status"] == "ambiguous" and finding["strategy"] == "ambiguous":
            matches = finding["candidates"]
            file_list = ", ".join(matches[:5])
            if len(matches) > 5:
                file_list += f", ... and {len(matches) - 5} more"
            findings.append({
                "check": "ambiguous_wikilinks",
                "severity": "info",
                "file": rel_path,
                "stem": stem,
                "message": (
                    f"Ambiguous wikilink: [[{stem}]] matches "
                    f"{len(matches)} files: {file_list}"
                ),
            })
        else:
            findings.append({
                "check": "broken_wikilinks",
                "severity": "warning",
                "file": rel_path,
                "stem": stem,
                "message": f"Broken wikilink: [[{stem}]]",
            })
    return findings
