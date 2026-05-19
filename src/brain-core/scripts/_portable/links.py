"""Portable broken-link checks shared by fix_links.py and check.py."""

import os

from _common import (
    INDEX_SKIP_DIRS,
    build_vault_file_index,
    check_wikilinks_in_file,
    discover_temporal_prefixes,
)


def check_broken_wikilinks(vault_root, router, file_index=None, *, ctx=None):
    """Check for wikilinks that target non-existent or ambiguous files.

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
            if d not in INDEX_SKIP_DIRS and d not in {"_Archive", "_Config"}
        ]
        for fname in filenames:
            if not fname.endswith(".md"):
                continue
            rel_path = os.path.relpath(os.path.join(dirpath, fname), vault_root)

            file_findings = check_wikilinks_in_file(
                vault_root, rel_path,
                file_index=file_index,
                temporal_prefixes=temporal_prefixes,
            )
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
