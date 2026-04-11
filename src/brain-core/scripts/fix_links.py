#!/usr/bin/env python3
"""
fix_links.py — Broken wikilink auto-repair.

Scans the vault for broken wikilinks and attempts to resolve them using
naming-convention heuristics (slug→title, double-dash→tilde, temporal
prefix matching, etc.). Dry-run by default; --fix applies unambiguous fixes.

Usage:
    python3 fix_links.py                      # dry run — report only
    python3 fix_links.py --fix                # apply unambiguous fixes
    python3 fix_links.py --json               # structured JSON output
    python3 fix_links.py --vault /path        # explicit vault path
"""

import json
import os
import sys
from datetime import datetime, timezone

from _common import (
    find_vault_root,
    build_vault_file_index,
    build_wikilink_pattern,
    discover_temporal_prefixes,
    load_compiled_router,
    make_wikilink_replacer,
    replace_wikilinks_in_vault,
    resolve_broken_link,
    strip_md_ext,
)
from check import check_broken_wikilinks


def scan_and_resolve(vault_root, router=None):
    """Scan for broken wikilinks and attempt resolution.

    Returns a dict with:
        fixed     — list of {target, resolved_to, strategy, ref_count, file_count}
        ambiguous — list of {target, candidates, strategy}
        unresolvable — list of {target, ref_count, files}
        summary   — {total_broken, fixed, ambiguous, unresolvable}
    """
    if router is None:
        router = load_compiled_router(vault_root)

    file_index = build_vault_file_index(vault_root)
    findings = check_broken_wikilinks(vault_root, router, file_index=file_index)
    broken = [f for f in findings if f["check"] == "broken_wikilinks"]

    # Group broken findings by target stem
    target_refs = {}  # stem → list of source files
    for finding in broken:
        target_refs.setdefault(finding["stem"], set()).add(finding["file"])

    fixed = []
    ambiguous = []
    unresolvable = []

    # Cache temporal prefixes once for the whole scan
    temporal_prefixes = discover_temporal_prefixes(file_index["md_basenames"])

    for target, source_files in sorted(target_refs.items()):
        resolution = resolve_broken_link(target, file_index, temporal_prefixes)

        if resolution.status == "resolved":
            fixed.append({
                "target": target,
                "resolved_to": resolution.resolved_to,
                "strategy": resolution.strategy,
                "ref_count": len(source_files),
                "file_count": len(source_files),
            })
        elif resolution.status == "ambiguous":
            ambiguous.append({
                "target": target,
                "candidates": resolution.candidates,
                "strategy": resolution.strategy,
            })
        else:
            unresolvable.append({
                "target": target,
                "ref_count": len(source_files),
                "files": sorted(source_files),
            })

    return {
        "fixed": fixed,
        "ambiguous": ambiguous,
        "unresolvable": unresolvable,
        "summary": {
            "total_broken": len(target_refs),
            "fixed": len(fixed),
            "ambiguous": len(ambiguous),
            "unresolvable": len(unresolvable),
        },
    }


def apply_fixes(vault_root, fix_list):
    """Apply resolved fixes to the vault.

    Args:
        vault_root: absolute path to vault root
        fix_list: list of dicts with 'target' and 'resolved_to' keys

    Returns:
        total number of wikilink substitutions made
    """
    if not fix_list:
        return 0

    stem_map = {item["target"]: item["resolved_to"] for item in fix_list}
    pattern = build_wikilink_pattern(*stem_map.keys())
    replacer = make_wikilink_replacer(stem_map)
    return replace_wikilinks_in_vault(vault_root, pattern, replacer)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv):
    """Parse CLI arguments."""
    do_fix = "--fix" in argv
    json_mode = "--json" in argv
    vault_path = None
    if "--vault" in argv:
        idx = argv.index("--vault")
        if idx + 1 < len(argv):
            vault_path = argv[idx + 1]
    return do_fix, json_mode, vault_path


def main():
    do_fix, json_mode, vault_path = parse_args(sys.argv)
    vault_root = vault_path if vault_path else str(find_vault_root())

    result = scan_and_resolve(vault_root)
    summary = result["summary"]

    if do_fix and result["fixed"]:
        total_subs = apply_fixes(vault_root, result["fixed"])
        result["substitutions"] = total_subs

    if json_mode:
        result["vault_root"] = vault_root
        result["checked_at"] = datetime.now(timezone.utc).astimezone().isoformat()
        result["mode"] = "fix" if do_fix else "dry_run"
        print(json.dumps(result, indent=2))
        return

    # Human-readable output
    print(f"Broken links: {summary['total_broken']} targets\n")

    if result["fixed"]:
        label = "Fixed" if do_fix else "Fixable"
        print(f"{label} ({summary['fixed']}):")
        for item in result["fixed"]:
            print(f"  [[{item['target']}]] → [[{item['resolved_to']}]]"
                  f"  ({item['strategy']}, {item['ref_count']} refs)")
        if do_fix:
            print(f"\n  {result.get('substitutions', 0)} total substitutions applied")
        print()

    if result["ambiguous"]:
        print(f"Ambiguous ({summary['ambiguous']}):")
        for item in result["ambiguous"]:
            print(f"  [[{item['target']}]] → {len(item['candidates'])} candidates:")
            for c in item["candidates"][:5]:
                print(f"    {c}")
            if len(item["candidates"]) > 5:
                print(f"    ... and {len(item['candidates']) - 5} more")
        print()

    if result["unresolvable"]:
        print(f"Unresolvable ({summary['unresolvable']}):")
        for item in result["unresolvable"]:
            print(f"  [[{item['target']}]] — {item['ref_count']} refs")
        print()

    if not do_fix and summary["fixed"] > 0:
        print(f"Run with --fix to apply {summary['fixed']} unambiguous fixes.")


if __name__ == "__main__":
    main()
