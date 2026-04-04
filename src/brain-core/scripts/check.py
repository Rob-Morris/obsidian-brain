#!/usr/bin/env python3
"""
check.py — Router-driven vault compliance checker (DD-009)

Reads the compiled router JSON and validates vault files against 9 structural
rules. Never parses taxonomy markdown — all per-type rules come from the
compiled router. The compiler is the single adaptation point.

Usage:
    python3 check.py                     # human-readable output
    python3 check.py --json              # structured JSON output
    python3 check.py --actionable        # include fix suggestions
    python3 check.py --severity warning  # filter by severity
    python3 check.py --json --actionable # combined
    python3 check.py --vault /path/to/vault  # check a specific vault
"""

import json
import os
import re
import sys
from datetime import datetime, timezone

from _common import (
    find_vault_root,
    is_system_dir,
    parse_frontmatter,
    extract_wikilinks,
    build_vault_file_index,
    resolve_artefact_path,
    _fenced_ranges,
    _INDEX_SKIP_DIRS,
    strip_md_ext,
    _FM_RE,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

COMPILED_ROUTER_REL = os.path.join(".brain", "local", "compiled-router.json")

ROOT_ALLOW = {
    "Agents.md", "CLAUDE.md", "agents.local.md",
    ".gitignore", ".gitattributes", ".mcp.json",
}


# ---------------------------------------------------------------------------
# Router loading
# ---------------------------------------------------------------------------

def load_router(vault_root):
    """Load compiled router JSON. Returns dict or error dict (never sys.exit)."""
    router_path = os.path.join(vault_root, COMPILED_ROUTER_REL)
    if not os.path.isfile(router_path):
        return {"error": f"Compiled router not found at {COMPILED_ROUTER_REL}. Run compile_router.py first."}
    try:
        with open(router_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return {"error": f"Failed to read compiled router: {e}"}


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

def find_type_files(vault_root, artefact_path, skip_archive=True):
    """Find .md files in an artefact type folder. Returns list of relative paths."""
    type_dir = os.path.join(vault_root, artefact_path)
    if not os.path.isdir(type_dir):
        return []

    files = []
    for dirpath, dirnames, filenames in os.walk(type_dir, followlinks=True):
        if skip_archive:
            dirnames[:] = [d for d in dirnames if d != "_Archive" and not d.startswith(".")]
        else:
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fname in filenames:
            if fname.endswith(".md"):
                abs_path = os.path.join(dirpath, fname)
                rel_path = os.path.relpath(abs_path, vault_root)
                files.append(rel_path)
    return files


# ---------------------------------------------------------------------------
# Naming pattern → regex conversion
# ---------------------------------------------------------------------------

def naming_pattern_to_regex(pattern):
    """Convert a naming pattern string to a compiled regex, or None if pattern is None."""
    if pattern is None:
        return None

    # Split on known placeholders, escape literals, reassemble as regex.
    # Order matters: longer patterns first to avoid partial matches.
    # yyyymmdd before yyyy/mm/dd; yyyy-mm-dd before yyyy; ddd before dd.
    PLACEHOLDERS = [
        ("yyyymmdd", r"\d{8}"),
        ("yyyy-mm-dd", r"\d{4}-\d{2}-\d{2}"),
        ("yyyy", r"\d{4}"),
        ("ddd", r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun)"),
        ("mm", r"\d{2}"),
        ("dd", r"\d{2}"),
        ("{sourcedoctype}", r"[a-z]+(?:-[a-z]+)*"),
        ("{Title}", r".+"),
        ("{name}", r".+"),
        ("{slug}", r".+"),
    ]

    # Tokenise the pattern: split into placeholder tokens and literal tokens
    # We process left-to-right, greedily matching the longest placeholder at each position
    result = ""
    i = 0
    while i < len(pattern):
        matched = False
        for placeholder, regex in PLACEHOLDERS:
            if pattern[i:i + len(placeholder)] == placeholder:
                result += regex
                i += len(placeholder)
                matched = True
                break
        if not matched:
            # Literal character — escape for regex but preserve `--` as literal
            result += re.escape(pattern[i])
            i += 1

    try:
        return re.compile(r"\A" + result + r"\Z")
    except re.error:
        return None


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------

def validate_artefact_folder(vault_root, router, path):
    """Validate path belongs to a known, configured type folder.

    Returns artefact dict or raises ValueError.  Does **not** check the
    filename against the type's naming pattern — use this for edit/append/
    convert where the file already exists and its name may predate the
    current naming convention.
    """
    vault_root = str(vault_root)

    for art in router.get("artefacts", []):
        art_path = art["path"]
        if path.startswith(art_path + os.sep) or path.startswith(art_path + "/"):
            if not art.get("configured"):
                raise ValueError(
                    f"Path '{path}' belongs to unconfigured type '{art['key']}'. "
                    f"Create a taxonomy file first."
                )
            return art

    known_paths = [a["path"] for a in router.get("artefacts", [])]
    raise ValueError(
        f"Path '{path}' does not belong to any known artefact folder. "
        f"Known: {', '.join(known_paths)}"
    )


def resolve_and_validate_folder(vault_root, router, path):
    """Validate path belongs to a known artefact folder, falling back to basename resolution.

    Tries exact path first. If that fails, resolves by basename (like wikilinks)
    and re-validates. Returns ``(resolved_path, artefact_dict)``.  Artefacts are
    always ``.md`` files, so a missing extension is normalised automatically.

    Raises ValueError if neither the exact path nor basename resolution succeeds.
    """
    if not path.endswith(".md"):
        path += ".md"
    try:
        art = validate_artefact_folder(vault_root, router, path)
        return path, art
    except ValueError:
        resolved = resolve_artefact_path(path, vault_root)
        art = validate_artefact_folder(vault_root, router, resolved)
        return resolved, art


def validate_artefact_naming(artefact, path):
    """Validate filename matches the type's naming pattern. Raises ValueError if not."""
    naming = artefact.get("naming")
    if naming and naming.get("pattern"):
        regex = naming_pattern_to_regex(naming["pattern"])
        if regex:
            filename = os.path.basename(path)
            if not regex.match(filename):
                raise ValueError(
                    f"Filename '{filename}' does not match expected pattern "
                    f"'{naming['pattern']}' for type '{artefact['key']}'"
                )


def validate_artefact_path(vault_root, router, path):
    """Validate folder membership AND naming pattern (strict).

    Used by compliance checks — not by edit/append/convert.
    """
    art = validate_artefact_folder(vault_root, router, path)
    validate_artefact_naming(art, path)
    return art


# ---------------------------------------------------------------------------
# Check implementations
# ---------------------------------------------------------------------------

def check_root_files(vault_root, router):
    """Check for content files in vault root."""
    findings = []
    # Build set of known artefact folders from router
    known_folders = set()
    for art in router.get("artefacts", []):
        # Top-level path component
        top = art["path"].split(os.sep)[0] if os.sep in art["path"] else art["path"]
        known_folders.add(top)

    for entry in sorted(os.listdir(vault_root)):
        if entry in ROOT_ALLOW:
            continue
        if entry.startswith(".") or entry.startswith("_"):
            continue

        full = os.path.join(vault_root, entry)
        if os.path.isdir(full):
            if entry in known_folders:
                continue
            # Unknown root directory — not necessarily a violation,
            # it'll be caught by unconfigured_type if it's a folder
            continue

        # It's a file in root that isn't in ROOT_ALLOW and doesn't start with . or _
        findings.append({
            "check": "root_files",
            "severity": "error",
            "file": entry,
            "message": f"Content file in vault root: {entry}",
            "fix": f"Move to an appropriate artefact folder or add to ROOT_ALLOW",
        })

    return findings


def check_naming(vault_root, router):
    """Check file naming against patterns from taxonomy."""
    findings = []
    for art in router.get("artefacts", []):
        if not art.get("configured") or not art.get("naming"):
            continue
        pattern_str = art["naming"].get("pattern")
        regex = naming_pattern_to_regex(pattern_str)
        if regex is None:
            continue

        files = find_type_files(vault_root, art["path"], skip_archive=True)
        for rel_path in files:
            filename = os.path.basename(rel_path)
            if not regex.match(filename):
                findings.append({
                    "check": "naming",
                    "severity": "warning",
                    "file": rel_path,
                    "message": f"Does not match pattern {pattern_str}",
                    "fix": f"Rename to match pattern: {pattern_str}",
                })

    return findings


def check_frontmatter_type(vault_root, router):
    """Check that frontmatter type field matches expected type."""
    findings = []
    for art in router.get("artefacts", []):
        if not art.get("configured") or not art.get("frontmatter"):
            continue
        expected_type = art["frontmatter"].get("type")
        if not expected_type:
            continue

        files = find_type_files(vault_root, art["path"], skip_archive=True)
        for rel_path in files:
            abs_path = os.path.join(vault_root, rel_path)
            try:
                with open(abs_path, "r", encoding="utf-8") as f:
                    text = f.read()
            except (OSError, UnicodeDecodeError):
                continue

            fields, _ = parse_frontmatter(text)
            if not fields:
                continue  # no frontmatter — skip silently
            actual_type = fields.get("type")
            if actual_type and actual_type != expected_type:
                findings.append({
                    "check": "frontmatter_type",
                    "severity": "warning",
                    "file": rel_path,
                    "message": f"type is '{actual_type}', expected '{expected_type}'",
                    "fix": f"Change frontmatter type to '{expected_type}'",
                })

    return findings


def check_frontmatter_required(vault_root, router):
    """Check that required frontmatter fields are present."""
    findings = []
    for art in router.get("artefacts", []):
        if not art.get("configured") or not art.get("frontmatter"):
            continue
        required = art["frontmatter"].get("required", [])
        if not required:
            continue

        files = find_type_files(vault_root, art["path"], skip_archive=True)
        for rel_path in files:
            abs_path = os.path.join(vault_root, rel_path)
            try:
                with open(abs_path, "r", encoding="utf-8") as f:
                    text = f.read()
            except (OSError, UnicodeDecodeError):
                continue

            fields, _ = parse_frontmatter(text)
            if not fields:
                continue  # no frontmatter — skip silently

            for field in required:
                if field not in fields:
                    findings.append({
                        "check": "frontmatter_required",
                        "severity": "warning",
                        "file": rel_path,
                        "message": f"Missing required field: {field}",
                        "fix": f"Add '{field}' to frontmatter",
                    })

    return findings


def check_month_folders(vault_root, router):
    """Check temporal files are in yyyy-mm/ subfolders."""
    findings = []
    month_re = re.compile(r"\d{4}-\d{2}")

    for art in router.get("artefacts", []):
        if art.get("classification") != "temporal":
            continue

        type_dir = os.path.join(vault_root, art["path"])
        if not os.path.isdir(type_dir):
            continue

        # Check for .md files directly in the type folder (not in subfolders)
        for entry in os.listdir(type_dir):
            if entry.endswith(".md"):
                rel_path = os.path.join(art["path"], entry)
                findings.append({
                    "check": "month_folders",
                    "severity": "warning",
                    "file": rel_path,
                    "message": "Temporal file not in a yyyy-mm/ subfolder",
                    "fix": f"Move to {art['path']}/yyyy-mm/",
                })

    return findings


def _find_archive_dirs(vault_root, art_path):
    """Find all _Archive/ directories for a living type.

    Returns list of (abs_archive_dir, rel_prefix) tuples. Covers both the
    type-root archive ({Type}/_Archive/) and project-subfolder archives
    ({Type}/{Project}/_Archive/).
    """
    dirs = []
    type_dir = os.path.join(vault_root, art_path)
    if not os.path.isdir(type_dir):
        return dirs

    # Type-root archive
    root_archive = os.path.join(type_dir, "_Archive")
    if os.path.isdir(root_archive):
        dirs.append((root_archive, os.path.join(art_path, "_Archive")))

    # Project-subfolder archives
    for entry in os.listdir(type_dir):
        if is_system_dir(entry):
            continue
        subfolder = os.path.join(type_dir, entry)
        if not os.path.isdir(subfolder):
            continue
        sub_archive = os.path.join(subfolder, "_Archive")
        if os.path.isdir(sub_archive):
            dirs.append((sub_archive, os.path.join(art_path, entry, "_Archive")))

    return dirs


def check_archive_metadata(vault_root, router):
    """Check _Archive/ files have archiveddate, yyyymmdd- prefix, and terminal status.

    Checks both type-root archives ({Type}/_Archive/) and project-subfolder
    archives ({Type}/{Project}/_Archive/).
    """
    findings = []
    date_prefix_re = re.compile(r"\d{8}-")

    for art in router.get("artefacts", []):
        if art.get("classification") != "living":
            continue

        terminal = None
        if art.get("frontmatter") and art["frontmatter"].get("terminal_statuses"):
            terminal = art["frontmatter"]["terminal_statuses"]

        for archive_dir, rel_prefix in _find_archive_dirs(vault_root, art["path"]):
            for fname in os.listdir(archive_dir):
                if not fname.endswith(".md"):
                    continue
                rel_path = os.path.join(rel_prefix, fname)
                abs_path = os.path.join(archive_dir, fname)

                # Sub-check 1: filename prefix
                if not date_prefix_re.match(fname):
                    findings.append({
                        "check": "archive_metadata",
                        "severity": "warning",
                        "file": rel_path,
                        "message": "Archive filename missing yyyymmdd- prefix",
                        "fix": "Rename to yyyymmdd-{slug}.md",
                    })

                # Read frontmatter for remaining checks
                try:
                    with open(abs_path, "r", encoding="utf-8") as f:
                        text = f.read()
                except (OSError, UnicodeDecodeError):
                    continue

                fields, _ = parse_frontmatter(text)

                # Sub-check 2: archiveddate field
                if "archiveddate" not in fields:
                    findings.append({
                        "check": "archive_metadata",
                        "severity": "warning",
                        "file": rel_path,
                        "message": "Missing archiveddate field",
                        "fix": "Add 'archiveddate: YYYY-MM-DD' to frontmatter",
                    })

                # Sub-check 3: terminal status (only if type defines terminal_statuses)
                if terminal and fields:
                    status = fields.get("status")
                    if status and status not in terminal:
                        findings.append({
                            "check": "archive_metadata",
                            "severity": "warning",
                            "file": rel_path,
                            "message": f"Status '{status}' is not a terminal status ({', '.join(terminal)})",
                            "fix": f"Set status to one of: {', '.join(terminal)}",
                        })

    return findings


def check_status_values(vault_root, router):
    """Check status field values match status_enum from compiled router."""
    findings = []
    for art in router.get("artefacts", []):
        if not art.get("configured") or not art.get("frontmatter"):
            continue
        enum = art["frontmatter"].get("status_enum")
        if not enum:
            continue

        # Check non-archived files only
        files = find_type_files(vault_root, art["path"], skip_archive=True)
        for rel_path in files:
            abs_path = os.path.join(vault_root, rel_path)
            try:
                with open(abs_path, "r", encoding="utf-8") as f:
                    text = f.read()
            except (OSError, UnicodeDecodeError):
                continue

            fields, _ = parse_frontmatter(text)
            if not fields:
                continue
            status = fields.get("status")
            if not status:
                continue
            if status not in enum:
                findings.append({
                    "check": "status_values",
                    "severity": "warning",
                    "file": rel_path,
                    "message": f"Status '{status}' not in allowed values ({', '.join(enum)})",
                    "fix": f"Set status to one of: {', '.join(enum)}",
                })

    return findings


def check_unconfigured_type(vault_root, router):
    """Emit info for artefact types with no taxonomy file."""
    findings = []
    for art in router.get("artefacts", []):
        if not art.get("configured"):
            findings.append({
                "check": "unconfigured_type",
                "severity": "info",
                "file": None,
                "message": f"Folder '{art['folder']}' ({art['type']}) has no taxonomy file",
                "fix": f"Create taxonomy at _Config/Taxonomy/{'Living' if art.get('classification') == 'living' else 'Temporal'}/{art['key']}.md",
            })
    return findings


def check_taxonomy_type_consistency(vault_root, router):
    """Flag configured artefacts where frontmatter_type equals the folder-derived type.

    When taxonomy defines a singular type (living/idea vs living/ideas), frontmatter_type
    will differ from type. If they're identical and the key ends in 's', the taxonomy
    likely forgot to define a singular type: field — flag it as info.
    """
    findings = []
    for art in router.get("artefacts", []):
        if not art.get("configured"):
            continue
        fm_type = art.get("frontmatter_type")
        folder_type = art["type"]
        if fm_type and fm_type == folder_type and art["key"].endswith("s"):
            findings.append({
                "check": "taxonomy_type_consistency",
                "severity": "info",
                "file": art.get("taxonomy_file", art["key"]),
                "message": (
                    f"frontmatter type '{fm_type}' matches folder-derived type — "
                    f"expected singular form (e.g. "
                    f"'{folder_type.rsplit('/', 1)[0]}/{art['key'].rstrip('s')}')"
                ),
            })
    return findings


# ---------------------------------------------------------------------------
# Broken and ambiguous wikilinks
# ---------------------------------------------------------------------------

# File extensions that indicate a non-markdown link target (embeds, etc.)
_ASSET_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp",
    ".pdf", ".mp3", ".mp4", ".wav", ".webm", ".mov",
    ".csv", ".json", ".xml", ".html", ".css", ".js",
}


def _has_file_extension(stem):
    """Return True if the stem ends with a known file extension."""
    _, ext = os.path.splitext(stem)
    return ext.lower() in _ASSET_EXTENSIONS


def check_broken_wikilinks(vault_root, router, file_index=None):
    """Check for wikilinks that target non-existent or ambiguous files."""
    findings = []
    if file_index is None:
        file_index = build_vault_file_index(vault_root)
    md_basenames = file_index["md_basenames"]
    all_basenames = file_index["all_basenames"]
    md_relpaths = file_index["md_relpaths"]

    for dirpath, dirnames, filenames in os.walk(vault_root):
        dirnames[:] = [d for d in dirnames if d not in _INDEX_SKIP_DIRS]
        for fname in filenames:
            if not fname.endswith(".md"):
                continue
            fpath = os.path.join(dirpath, fname)
            rel_path = os.path.relpath(fpath, vault_root)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    text = f.read()
            except OSError:
                continue

            # Compute ranges to skip: frontmatter and fenced code blocks
            skip_ranges = []
            fm_match = _FM_RE.match(text)
            if fm_match:
                skip_ranges.append((0, fm_match.end()))
            skip_ranges.extend(_fenced_ranges(text))

            links = extract_wikilinks(text)
            for link in links:
                # Skip links inside frontmatter or code blocks
                pos = link["start"]
                if any(start <= pos < end for start, end in skip_ranges):
                    continue

                stem = link["stem"]
                is_embed = link["is_embed"]
                resolved = False
                ambiguous = False

                if is_embed or _has_file_extension(stem):
                    # Embed or asset link — check all files by basename
                    basename_key = os.path.basename(stem).lower()
                    if basename_key in all_basenames:
                        resolved = True
                elif "/" in stem:
                    # Path-qualified link — try exact relpath, then basename fallback
                    stem_lower = strip_md_ext(stem).lower()
                    if stem_lower in md_relpaths:
                        resolved = True
                    else:
                        # Basename fallback
                        basename_key = os.path.splitext(os.path.basename(stem))[0].lower()
                        if basename_key in md_basenames:
                            resolved = True
                else:
                    # Basename-only link
                    stem_lower = stem.lower()
                    matches = md_basenames.get(stem_lower, [])
                    if matches:
                        resolved = True
                        if len(matches) > 1:
                            ambiguous = True

                if not resolved:
                    findings.append({
                        "check": "broken_wikilinks",
                        "severity": "warning",
                        "file": rel_path,
                        "stem": stem,
                        "message": f"Broken wikilink: [[{stem}]]",
                    })
                elif ambiguous:
                    file_list = ', '.join(matches[:5])
                    if len(matches) > 5:
                        file_list += f", ... and {len(matches) - 5} more"
                    findings.append({
                        "check": "ambiguous_wikilinks",
                        "severity": "info",
                        "file": rel_path,
                        "stem": stem,
                        "message": f"Ambiguous wikilink: [[{stem}]] matches {len(matches)} files: {file_list}",
                    })

    return findings


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

ALL_CHECKS = [
    check_root_files,
    check_naming,
    check_frontmatter_type,
    check_frontmatter_required,
    check_month_folders,
    check_archive_metadata,
    check_status_values,
    check_broken_wikilinks,
    check_unconfigured_type,
    check_taxonomy_type_consistency,
]


def run_checks(vault_root, router=None):
    """Run all compliance checks. Returns structured result dict.

    Safe for import — never calls sys.exit().
    """
    if router is None:
        router = load_router(vault_root)
    if "error" in router:
        return {
            "vault_root": vault_root,
            "brain_core_version": None,
            "checked_at": datetime.now(timezone.utc).astimezone().isoformat(),
            "summary": {"errors": 1, "warnings": 0, "info": 0},
            "findings": [{
                "check": "router",
                "severity": "error",
                "file": None,
                "message": router["error"],
            }],
        }

    version = router.get("meta", {}).get("brain_core_version")
    findings = []
    for check_fn in ALL_CHECKS:
        findings.extend(check_fn(vault_root, router))

    summary = {
        "errors": sum(1 for f in findings if f["severity"] == "error"),
        "warnings": sum(1 for f in findings if f["severity"] == "warning"),
        "info": sum(1 for f in findings if f["severity"] == "info"),
    }

    return {
        "vault_root": vault_root,
        "brain_core_version": version,
        "checked_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "summary": summary,
        "findings": findings,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv):
    """Parse CLI arguments. Returns (json_mode, actionable, severity_filter, vault_path)."""
    json_mode = "--json" in argv
    actionable = "--actionable" in argv
    severity = None
    vault_path = None
    if "--severity" in argv:
        idx = argv.index("--severity")
        if idx + 1 < len(argv):
            severity = argv[idx + 1]
    if "--vault" in argv:
        idx = argv.index("--vault")
        if idx + 1 < len(argv):
            vault_path = argv[idx + 1]
    return json_mode, actionable, severity, vault_path


def main():
    json_mode, actionable, severity_filter, vault_path = parse_args(sys.argv)
    vault_root = vault_path if vault_path else str(find_vault_root())
    result = run_checks(vault_root)

    # Apply severity filter
    if severity_filter:
        result["findings"] = [f for f in result["findings"] if f["severity"] == severity_filter]
        result["summary"] = {
            "errors": sum(1 for f in result["findings"] if f["severity"] == "error"),
            "warnings": sum(1 for f in result["findings"] if f["severity"] == "warning"),
            "info": sum(1 for f in result["findings"] if f["severity"] == "info"),
        }

    # Strip fix keys unless --actionable
    if not actionable:
        for f in result["findings"]:
            f.pop("fix", None)

    if json_mode:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        # Human-readable output
        severity_prefix = {"error": "ERROR", "warning": "WARN ", "info": "INFO "}
        for f in result["findings"]:
            prefix = severity_prefix.get(f["severity"], "     ")
            file_str = f["file"] or "(folder-level)"
            line = f"  {prefix}  {file_str}: {f['message']}"
            if actionable and "fix" in f:
                line += f" → {f['fix']}"
            print(line)

        s = result["summary"]
        total = s["errors"] + s["warnings"] + s["info"]
        if total == 0:
            print("\nAll checks passed.")
        else:
            print(f"\n{total} finding(s): {s['errors']} error(s), {s['warnings']} warning(s), {s['info']} info")

    # Exit codes: 0 = clean, 1 = warnings only, 2 = errors
    if result["summary"]["errors"] > 0:
        sys.exit(2)
    elif result["summary"]["warnings"] > 0:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
