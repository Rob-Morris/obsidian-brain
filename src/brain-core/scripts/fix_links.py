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
from dataclasses import dataclass

from _common._wikilinks import WikilinkRewritePlan, plan_wikilink_rewrites, apply_wikilink_rewrites

from _portable.links import check_broken_wikilinks
from _lifecycle.derived_cache_state import (
    inspect_lexical_cache,
    load_fresh_compiled_router,
)
from _common import (
    build_vault_file_index,
    build_wikilink_pattern,
    check_wikilinks_in_file,
    discover_temporal_prefixes,
    file_index_from_documents,
    find_vault_root,
    make_wikilink_replacer,
    overlay_file_index_result,
    replace_wikilinks_in_text,
    replace_wikilinks_in_vault,
    resolve_broken_link,
    safe_write,
)


class WikilinkProcessingError(RuntimeError):
    """A requested post-write wikilink phase failed for a known document."""

    def __init__(self, path, cause):
        self.path = path
        super().__init__(f"wikilink processing failed for {path}: {cause}")


def file_index_for_mutation(vault_root):
    """Return a freshness-qualified wikilink index before a document write."""
    state = inspect_lexical_cache(vault_root)
    payload = state.payload if not state.stale else None
    documents = payload.get("documents") if isinstance(payload, dict) else None
    if isinstance(documents, list):
        try:
            return file_index_from_documents(documents, vault_root)
        except (KeyError, TypeError, ValueError):
            pass
    return build_vault_file_index(vault_root)


def _resolvable_fixes(findings):
    """Extract fix-ready entries from wikilink findings."""
    return [
        {
            "target": f["stem"],
            "resolved_to": f["resolved_to"],
            "strategy": f["strategy"],
        }
        for f in findings if f["status"] == "resolvable"
    ]


def attach_wikilink_warnings(vault_root, result, apply_fixes=False, file_index=None):
    """Check the written/edited file for broken wikilinks and attach findings.

    Adds a ``wikilink_warnings`` key to *result* when the file contains any
    broken, resolvable, or ambiguous links. Clean files leave the key absent
    so callers can treat its presence as the signal to emit a warning.

    When ``apply_fixes`` is True, resolvable findings are auto-applied and the
    file re-checked so ``wikilink_warnings`` reflect only findings that remain.
    Applied fixes are attached as ``wikilink_fixes``. Reuses a single vault
    file index across the pre- and post-fix scans to avoid double-walking.

    When ``file_index`` is provided, it may be an authoritative index or a
    zero-argument factory for one; factories are invoked only after the cheap
    link-presence check. The index is then overlaid with the just-written file.
    Pass ``None`` (the default) to build the index from the vault on demand.
    """
    path = result.get("path")
    if not path:
        return
    try:
        _attach_wikilink_warnings(
            vault_root,
            result,
            apply_fixes=apply_fixes,
            file_index=file_index,
        )
    except Exception as exc:
        if apply_fixes:
            raise WikilinkProcessingError(path, exc) from exc


def _attach_wikilink_warnings(vault_root, result, apply_fixes=False, file_index=None):
    path = result["path"]
    vault_root = str(vault_root)
    try:
        with open(os.path.join(vault_root, path), "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        raise
    if "[[" not in text:
        return
    if callable(file_index):
        file_index = file_index()
    if file_index is None:
        file_index = build_vault_file_index(vault_root)
    else:
        file_index = overlay_file_index_result(file_index, result)
    temporal_prefixes = discover_temporal_prefixes(file_index["md_basenames"])
    findings = check_wikilinks_in_file(
        vault_root, path,
        file_index=file_index, temporal_prefixes=temporal_prefixes, text=text,
    )
    if apply_fixes:
        resolvable = _resolvable_fixes(findings)
        if resolvable:
            applied = apply_fixes_to_file(vault_root, path, resolvable)
            result["wikilink_fixes"] = {
                "applied": applied,
                "fixes": resolvable,
            }
            # Re-scan the mutated file; basenames are unchanged so reuse index.
            findings = check_wikilinks_in_file(
                vault_root, path,
                file_index=file_index, temporal_prefixes=temporal_prefixes,
            )
    if findings:
        result["wikilink_warnings"] = findings


def scan_and_resolve_file(vault_root, rel_path, file_index=None, temporal_prefixes=None):
    """Scan a single file for broken wikilinks and return fix-ready entries.

    Calls ``check_wikilinks_in_file`` and filters to resolvable findings.

    Returns a list of dicts: ``{target, resolved_to, strategy}``.
    """
    findings = check_wikilinks_in_file(
        vault_root, rel_path,
        file_index=file_index, temporal_prefixes=temporal_prefixes,
    )
    return _resolvable_fixes(findings)


def apply_fixes_to_file(vault_root, rel_path, fixes, links_filter=None):
    """Apply resolved wikilink fixes to a single file.

    Args:
        vault_root: absolute path to vault root
        rel_path: vault-relative path of the file to rewrite
        fixes: list of ``{target, resolved_to}`` dicts
        links_filter: optional iterable of target stems — only those targets
            are applied. Omit to apply all fixes.

    Returns:
        Number of wikilink substitutions made in the file.
    """
    if not fixes:
        return 0

    if links_filter is not None:
        wanted = set(links_filter)
        fixes = [f for f in fixes if f["target"] in wanted]
        if not fixes:
            return 0

    stem_map = {item["target"]: item["resolved_to"] for item in fixes}
    pattern = build_wikilink_pattern(*stem_map.keys())
    replacer = make_wikilink_replacer(stem_map)

    abs_path = os.path.join(vault_root, rel_path)
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return 0
    new_content, count = replace_wikilinks_in_text(content, pattern, replacer)
    if count > 0:
        safe_write(abs_path, new_content, bounds=vault_root)
    return count


def scan_and_resolve(vault_root, router=None):
    """Scan for broken wikilinks vault-wide and attempt resolution.

    Returns a dict with:
        fixed     — list of {target, resolved_to, strategy, ref_count, file_count}
        ambiguous — list of {target, candidates, strategy}
        unresolvable — list of {target, ref_count, files}
        summary   — {total_broken, fixed, ambiguous, unresolvable}
    """
    if router is None:
        router = load_fresh_compiled_router(vault_root)
    if isinstance(router, dict) and "error" in router:
        raise ValueError(router["error"])

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


def scan_file(vault_root, rel_path, router=None):
    """Scan a single file and return a result dict mirroring scan_and_resolve.

    Uses the vault-wide file index so resolution strategies work identically.
    ``router`` is accepted for API symmetry with ``scan_and_resolve`` but is
    not needed — single-file resolution relies on the file index only.
    """
    file_index = build_vault_file_index(vault_root)
    temporal_prefixes = discover_temporal_prefixes(file_index["md_basenames"])

    findings = check_wikilinks_in_file(
        vault_root, rel_path,
        file_index=file_index, temporal_prefixes=temporal_prefixes,
    )

    fixed = []
    ambiguous = []
    unresolvable = []
    for f in findings:
        if f["status"] == "resolvable":
            fixed.append({
                "target": f["stem"],
                "resolved_to": f["resolved_to"],
                "strategy": f["strategy"],
                "ref_count": 1,
                "file_count": 1,
            })
        elif f["status"] == "ambiguous":
            ambiguous.append({
                "target": f["stem"],
                "candidates": f["candidates"],
                "strategy": f["strategy"],
            })
        else:
            unresolvable.append({
                "target": f["stem"],
                "ref_count": 1,
                "files": [rel_path],
            })

    total = len(fixed) + len(ambiguous) + len(unresolvable)
    return {
        "fixed": fixed,
        "ambiguous": ambiguous,
        "unresolvable": unresolvable,
        "summary": {
            "total_broken": total,
            "fixed": len(fixed),
            "ambiguous": len(ambiguous),
            "unresolvable": len(unresolvable),
        },
        "path": rel_path,
    }


@dataclass(frozen=True, slots=True)
class LinkFixPlan:
    """A resolved link query and its concrete matching text replacements."""

    result: dict
    rewrites: WikilinkRewritePlan
    path: str | None


def plan_link_fixes(vault_root, *, path=None, links_filter=(), router=None):
    """Resolve the selected fix scope and render all replacements before effects."""
    result = scan_file(vault_root, path, router) if path else scan_and_resolve(vault_root, router)
    fixes = [item for item in result["fixed"]
             if not links_filter or item["target"] in links_filter]
    stems = {item["target"]: item["resolved_to"] for item in fixes}
    rewrites = (plan_wikilink_rewrites(
        vault_root, build_wikilink_pattern(*stems), make_wikilink_replacer(stems),
        paths=(path,) if path else None) if stems else WikilinkRewritePlan(()))
    return LinkFixPlan(result, rewrites, path)


def apply_link_fix_plan(vault_root, plan, *, dry_run=False):
    """Return the planned observation or persist exactly its matching writes."""
    substitutions = 0 if dry_run else apply_wikilink_rewrites(vault_root, plan.rewrites)
    return {**plan.result, "substitutions": substitutions}


def apply_fixes(vault_root, fix_list):
    """Apply resolved fixes vault-wide.

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

    try:
        result = scan_and_resolve(vault_root)
    except ValueError as exc:
        if json_mode:
            print(json.dumps({"error": str(exc)}))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
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
