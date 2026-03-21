#!/usr/bin/env python3
"""Vault session hygiene check — run from vault root or any subdirectory.

Session hygiene only (did you log today? transcripts? backups?).
Structural compliance (naming, frontmatter, archive metadata) has moved
to .brain-core/scripts/check.py.
"""

import sys
from datetime import datetime, date
from pathlib import Path

SKIP = {"---", "tags:", "  -", "created:", "modified:", "# Log"}

def vault_root():
    for p in [Path(__file__).resolve()] + list(Path(__file__).resolve().parents):
        if (p / "CLAUDE.md").exists(): return p
    if (Path.cwd() / "CLAUDE.md").exists(): return Path.cwd()
    sys.exit("ERROR: no CLAUDE.md found")

def check_log(root):
    t = date.today()
    p = root / "_Temporal/Logs" / t.strftime("%Y-%m") / f"log--{t.isoformat()}.md"
    if not p.exists(): return "MISSING", f"No log at {p.relative_to(root)}"
    lines = [l for l in p.read_text().strip().split("\n") if l.strip() and not any(l.startswith(s) for s in SKIP)]
    if not lines: return "EMPTY", "Log exists but has no entries"
    return "OK", f"{len(lines)} content lines"

def check_transcripts(root):
    t = date.today()
    d = root / "_Temporal/Transcripts" / t.strftime("%Y-%m")
    if not d.exists(): return "INFO", "No transcript folder this month"
    found = [f.name for f in d.iterdir() if f.name.startswith(t.strftime("%Y%m%d"))]
    if not found: return "INFO", "No transcripts today — check if Q&A occurred"
    return "OK", f"{len(found)} transcript(s): {', '.join(found)}"

def check_backup(root):
    d = root / ".backups"
    if not d.exists(): return "INFO", "No Backups folder"
    backups = sorted(d.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True)
    if not backups: return "INFO", "Backups folder empty"
    age_h = (datetime.now() - datetime.fromtimestamp(backups[0].stat().st_mtime)).total_seconds() / 3600
    if age_h < 24: return "OK", f"{backups[0].name} ({age_h:.1f}h ago)"
    return "INFO", f"Latest backup {age_h:.0f}h old ({backups[0].name})"

# Files allowed in vault root (not content — system/config only)
ROOT_ALLOW = {"CLAUDE.md", "Agents.md", ".gitignore", ".gitattributes", ".mcp.json", ".obsidian", ".brain-core", ".backups", ".git", ".DS_Store", ".trash"}

def check_root_files(root):
    orphans = []
    for p in root.iterdir():
        name = p.name
        if name in ROOT_ALLOW:
            continue
        if p.is_dir() and not name.startswith("."):
            # Top-level folders are fine (artefact/temporal/config folders)
            continue
        orphans.append(name)
    if not orphans:
        return "OK", "No content files in vault root"
    return "ORPHAN", f"{len(orphans)} file(s) in vault root: {', '.join(sorted(orphans))}"

# Infrastructure folders required by brain-core (other folders are vault-specific)
REQUIRED_FOLDERS = {"_Config", "_Temporal"}

def check_expected_folders(root):
    missing = [f for f in REQUIRED_FOLDERS if not (root / f).is_dir()]
    if missing:
        return "MISSING", f"Missing infrastructure folders: {', '.join(sorted(missing))}"
    return "OK", f"Infrastructure folders present"

def main():
    root = vault_root()
    checks = [
        ("Log", check_log),
        ("Transcripts", check_transcripts),
        ("Backup", check_backup),
        ("Root Files", check_root_files),
        ("Folders", check_expected_folders),
    ]
    icons = {"OK": "✓", "MISSING": "✗", "EMPTY": "✗", "ORPHAN": "✗", "INFO": "?"}
    issues = []
    print(f"Vault: {root}\n")
    for name, fn in checks:
        status, msg = fn(root)
        print(f"{icons[status]} {name}: [{status}] {msg}")
        if status in ("MISSING", "EMPTY", "ORPHAN"): issues.append(f"{name}: {msg}")
    print(f"\n{'ACTION REQUIRED: ' + str(len(issues)) + ' issue(s)' if issues else 'All OK.'}")

if __name__ == "__main__":
    main()
