#!/usr/bin/env python3
"""
migrate_to_0_19_0.py — Migrate Ideas status values and transcript naming.

Three tasks:
  1. Rename Ideas status values: developing→shaping, graduated→adopted
  2. Rename Ideas/+Graduated/ folder to Ideas/+Adopted/
  3. Rename shaping transcripts: yyyymmdd-WORD-transcript~TITLE.md
     → yyyymmdd-shaping-transcript~TITLE.md
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from _common import parse_frontmatter, serialize_frontmatter, safe_write
from rename import rename_and_update_links

VERSION = "0.19.0"

_TRANSCRIPT_RE = re.compile(
    r"^(\d{8})-(?!shaping-)(\w[\w-]*)-transcript~(.+\.md)$"
)


def _migrate_ideas_status(vault_root, actions):
    """Walk Ideas/ and update status fields for living/idea artefacts."""
    ideas_dir = os.path.join(vault_root, "Ideas")
    if not os.path.isdir(ideas_dir):
        return 0

    updated = 0
    for dirpath, _dirnames, filenames in os.walk(ideas_dir):
        for fname in filenames:
            if not fname.endswith(".md"):
                continue
            abs_path = os.path.join(dirpath, fname)
            try:
                with open(abs_path, "r", encoding="utf-8") as f:
                    content = f.read()
            except OSError:
                continue

            fields, body = parse_frontmatter(content)
            if fields.get("type") != "living/idea":
                continue

            status = fields.get("status")
            if status == "developing":
                fields["status"] = "shaping"
            elif status == "graduated":
                fields["status"] = "adopted"
            else:
                continue

            new_content = serialize_frontmatter(fields, body=body)
            safe_write(abs_path, new_content, bounds=vault_root)
            rel = os.path.relpath(abs_path, vault_root)
            actions.append(f"updated status in {rel}: {status} → {fields['status']}")
            updated += 1

    return updated


def _migrate_folder_rename(vault_root, actions):
    """Rename Ideas/+Graduated/ to Ideas/+Adopted/ if it exists."""
    old_path = os.path.join(vault_root, "Ideas", "+Graduated")
    new_path = os.path.join(vault_root, "Ideas", "+Adopted")

    if not os.path.isdir(old_path):
        return False

    os.rename(old_path, new_path)
    actions.append("renamed Ideas/+Graduated/ → Ideas/+Adopted/")
    return True


def _migrate_transcripts(vault_root, actions):
    """Rename yyyymmdd-WORD-transcript~TITLE.md to yyyymmdd-shaping-transcript~TITLE.md."""
    transcripts_dir = os.path.join(vault_root, "_Temporal", "Shaping Transcripts")
    if not os.path.isdir(transcripts_dir):
        return 0

    renamed = 0
    # Collect files first to avoid modifying while walking
    to_rename = []
    for dirpath, _dirnames, filenames in os.walk(transcripts_dir):
        for fname in filenames:
            if not fname.endswith(".md"):
                continue
            m = _TRANSCRIPT_RE.match(fname)
            if not m:
                continue
            date_part, _word, title = m.group(1), m.group(2), m.group(3)
            new_fname = f"{date_part}-shaping-transcript~{title}"
            old_rel = os.path.relpath(os.path.join(dirpath, fname), vault_root)
            new_rel = os.path.relpath(os.path.join(dirpath, new_fname), vault_root)
            to_rename.append((old_rel, new_rel))

    for old_rel, new_rel in to_rename:
        links_updated = rename_and_update_links(vault_root, old_rel, new_rel)
        actions.append(
            f"renamed {old_rel} → {new_rel} ({links_updated} links updated)"
        )
        renamed += 1

    return renamed


def migrate(vault_root):
    """Migrate Ideas status values, folder name, and transcript naming.

    Returns dict with status and list of actions taken.
    """
    vault_root = str(vault_root)
    actions = []

    ideas_updated = _migrate_ideas_status(vault_root, actions)
    folder_renamed = _migrate_folder_rename(vault_root, actions)
    transcripts_renamed = _migrate_transcripts(vault_root, actions)

    if not actions:
        return {"status": "skipped", "actions": []}

    return {
        "status": "ok",
        "ideas_updated": ideas_updated,
        "folder_renamed": folder_renamed,
        "transcripts_renamed": transcripts_renamed,
        "actions": actions,
    }
