"""Vault root discovery, version reading, and filesystem scanning."""

import os
import sys
from pathlib import Path

# _Temporal contains temporal artefacts — scanned separately from living types
TEMPORAL_DIR = "_Temporal"


def is_vault_root(path):
    """Check whether *path* is a Brain vault root.

    A vault is identified by the brain-core VERSION file (modern vaults), the
    canonical top-level ``AGENTS.md`` bootstrap, or the legacy ``Agents.md``
    marker (pre-0.x vaults). Accepts either a ``pathlib.Path`` or a string.
    """
    p = path if isinstance(path, Path) else Path(path)
    return (
        (p / ".brain-core" / "VERSION").is_file()
        or (p / "AGENTS.md").is_file()
        or (p / "Agents.md").is_file()
    )


def find_vault_root(vault_arg=None):
    """Find a Brain vault root.

    If vault_arg is given, validates it directly. Otherwise checks cwd first,
    then walks up from script location.
    """
    if vault_arg:
        p = Path(vault_arg).resolve()
        if is_vault_root(p):
            return p
        print(f"Error: {vault_arg} is not a vault root.", file=sys.stderr)
        sys.exit(1)

    # Check cwd first (allows running from dev repo: cd vault && python3 /path/to/script)
    cwd = Path(os.getcwd()).resolve()
    if is_vault_root(cwd):
        return cwd

    # Walk up from script location (works when installed inside .brain-core/scripts/)
    current = Path(__file__).resolve().parent
    for _ in range(10):
        current = current.parent
        if is_vault_root(current):
            return current
    print("Error: could not find vault root.", file=sys.stderr)
    sys.exit(1)


def read_version(vault_root):
    """Read brain-core version from the canonical VERSION file."""
    version_file = os.path.join(str(vault_root), ".brain-core", "VERSION")
    with open(version_file, "r", encoding="utf-8") as f:
        return f.read().strip()


def is_system_dir(name):
    """Check if a directory name is infrastructure (not a living artefact).

    Convention: any folder starting with _ or . is excluded from living type
    discovery. _Temporal contains artefacts but is scanned separately — it is
    still excluded here because its children are temporal, not living.
    """
    return name.startswith("_") or name.startswith(".")


def is_archived_path(path):
    """Return True if *path* sits inside an ``_Archive/`` directory."""
    return "/_Archive/" in path or path.startswith("_Archive/")


def scan_living_types(vault_root):
    """Discover living artefact types from root-level non-system directories."""
    types = []
    for entry in sorted(os.listdir(vault_root)):
        full = os.path.join(vault_root, entry)
        if not os.path.isdir(full):
            continue
        if is_system_dir(entry):
            continue
        key = entry.lower().replace(" ", "-")
        types.append({
            "folder": entry,
            "key": key,
            "classification": "living",
            "type": "living/" + key,
            "path": entry,
        })
    return types


def scan_temporal_types(vault_root):
    """Discover temporal artefact types from _Temporal/ subfolders."""
    temporal_dir = os.path.join(vault_root, TEMPORAL_DIR)
    if not os.path.isdir(temporal_dir):
        return []
    types = []
    for entry in sorted(os.listdir(temporal_dir)):
        full = os.path.join(temporal_dir, entry)
        if not os.path.isdir(full):
            continue
        if entry.startswith(".") or entry.startswith("_"):
            continue
        key = entry.lower().replace(" ", "-")
        types.append({
            "folder": entry,
            "key": key,
            "classification": "temporal",
            "type": "temporal/" + key,
            "path": os.path.join(TEMPORAL_DIR, entry),
        })
    return types


def match_artefact(artefacts, type_key):
    """Find an artefact dict matching type_key against key, type,
    or frontmatter_type.

    Accepts plural ("ideas"), singular ("idea"), full plural
    ("living/ideas"), or full singular ("living/idea").
    Returns the matched artefact dict, or None if no match found.
    """
    for a in artefacts:
        if type_key in (a["key"], a["type"], a["frontmatter_type"]):
            return a

    # Bare singular name: "idea" should match frontmatter_type "living/idea".
    # Only try this when the input has no slash (full paths already matched above).
    if "/" not in type_key:
        for a in artefacts:
            ft = a["frontmatter_type"]
            if ft.endswith("/" + type_key):
                return a

    return None
