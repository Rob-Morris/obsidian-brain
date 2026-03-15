#!/usr/bin/env python3
"""
compile_router.py — Brain-core compiled router generator

Transforms human-readable vault config (router.md, taxonomy files, skills,
styles, plugins) into _Config/.compiled-router.json — a local, gitignored,
hash-invalidated cache that all brain-core tools read.

Usage:
    python3 compile_router.py           # write _Config/.compiled-router.json
    python3 compile_router.py --json    # output JSON to stdout
"""

import hashlib
import json
import os
import platform
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

# _Temporal contains temporal artefacts — scanned separately from living types
TEMPORAL_DIR = "_Temporal"

OUTPUT_PATH = os.path.join("_Config", ".compiled-router.json")


# ---------------------------------------------------------------------------
# Vault root discovery
# ---------------------------------------------------------------------------

def find_vault_root():
    """Walk up from script location looking for a Brain vault root."""
    # Script lives at .brain-core/scripts/compile_router.py — vault is 2 up
    current = Path(__file__).resolve().parent
    for _ in range(10):
        current = current.parent
        if (current / ".brain-core" / "VERSION").is_file():
            return current
        if (current / "Agents.md").is_file():
            return current
    print("Error: could not find vault root.", file=sys.stderr)
    sys.exit(1)


def read_version(vault_root):
    """Read brain-core version from the canonical VERSION file."""
    version_file = os.path.join(str(vault_root), ".brain-core", "VERSION")
    with open(version_file, "r", encoding="utf-8") as f:
        return f.read().strip()


# ---------------------------------------------------------------------------
# File hashing
# ---------------------------------------------------------------------------

def hash_file(path):
    """Return sha256 hex digest of a file's contents."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def compute_source_hash(sources):
    """Compute composite hash from sorted individual file hashes."""
    h = hashlib.sha256()
    for key in sorted(sources.keys()):
        h.update(sources[key].encode("utf-8"))
    return "sha256:" + h.hexdigest()


# ---------------------------------------------------------------------------
# Filesystem scanning (DD-016)
# ---------------------------------------------------------------------------

def is_system_dir(name):
    """Check if a directory name is infrastructure (not a living artefact).

    Convention: any folder starting with _ or . is excluded from living type
    discovery. _Temporal contains artefacts but is scanned separately — it is
    still excluded here because its children are temporal, not living.
    """
    return name.startswith("_") or name.startswith(".")


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


# ---------------------------------------------------------------------------
# Taxonomy parsing
# ---------------------------------------------------------------------------

def parse_taxonomy_file(path):
    """Parse a taxonomy .md file, extracting Naming, Frontmatter, Trigger, Template sections."""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    result = {
        "naming": None,
        "frontmatter": None,
        "trigger": None,
        "template_file": None,
    }

    # Parse ## Naming
    naming_match = re.search(
        r"^## Naming\s*\n(.*?)(?=^## |\Z)", content, re.MULTILINE | re.DOTALL
    )
    if naming_match:
        naming_text = naming_match.group(1).strip()
        # Extract pattern (first backtick-delimited string)
        pattern_match = re.search(r"`([^`]+\.md)`", naming_text)
        # Extract folder path — look for "in `path`"
        folder_match = re.search(r"in `([^`]+)`", naming_text)
        if pattern_match or folder_match:
            result["naming"] = {
                "pattern": pattern_match.group(1) if pattern_match else None,
                "folder": folder_match.group(1) if folder_match else None,
            }

    # Parse ## Frontmatter — extract YAML code block
    fm_match = re.search(
        r"^## Frontmatter\s*\n.*?```ya?ml\s*\n---\s*\n(.*?)---\s*\n```",
        content,
        re.MULTILINE | re.DOTALL,
    )
    if fm_match:
        yaml_text = fm_match.group(1).strip()
        # Extract type field
        type_match = re.search(r"^type:\s*(.+)$", yaml_text, re.MULTILINE)
        # Extract required fields (all top-level keys)
        required = re.findall(r"^(\w[\w-]*):", yaml_text, re.MULTILINE)
        if type_match or required:
            result["frontmatter"] = {
                "type": type_match.group(1).strip() if type_match else None,
                "required": required if required else [],
            }

    # Parse ## Trigger
    trigger_match = re.search(
        r"^## Trigger\s*\n(.*?)(?=^## |\Z)", content, re.MULTILINE | re.DOTALL
    )
    if trigger_match:
        trigger_text = trigger_match.group(1).strip()
        if trigger_text:
            condition_line = trigger_text.split("\n")[0].strip()
            category = infer_trigger_category(condition_line)
            result["trigger"] = {
                "category": category,
                "condition": condition_line,
                "detail": trigger_text,
            }

    # Parse ## Template — extract wikilink
    template_match = re.search(
        r"^## Template\s*\n(.*?)(?=^## |\Z)", content, re.MULTILINE | re.DOTALL
    )
    if template_match:
        template_text = template_match.group(1).strip()
        link_match = re.search(r"\[\[([^\]|]+?)(?:\|[^\]]+)?\]\]", template_text)
        if link_match:
            result["template_file"] = link_match.group(1)

    return result


def infer_trigger_category(condition):
    """Infer trigger category from condition text."""
    lower = condition.lower()
    if lower.startswith("after"):
        return "after"
    if lower.startswith("before"):
        return "before"
    return "ongoing"


# ---------------------------------------------------------------------------
# Router parsing
# ---------------------------------------------------------------------------

def parse_router(path):
    """Parse _Config/router.md into always_rules and conditional triggers."""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    always_rules = []
    conditionals = []

    section = None
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped == "Always:":
            section = "always"
            continue
        elif stripped == "Conditional:":
            section = "conditional"
            continue
        elif stripped and not stripped.startswith("-") and not stripped.startswith("["):
            # Non-list line outside of a section (preamble text)
            if section is None:
                continue

        if not stripped.startswith("- "):
            continue

        item = stripped[2:].strip()
        if section == "always":
            always_rules.append(item)
        elif section == "conditional":
            # Parse "condition → [[target]]"
            arrow_match = re.match(
                r"(.+?)\s*→\s*\[\[([^\]]+)\]\]", item
            )
            if arrow_match:
                conditionals.append({
                    "condition": arrow_match.group(1).strip(),
                    "target": arrow_match.group(2).strip(),
                })

    if not always_rules and not conditionals:
        print(
            f"Warning: no rules parsed from {path} — check section headers "
            f"(expected exact 'Always:' and 'Conditional:' lines)",
            file=sys.stderr,
        )

    return always_rules, conditionals


# ---------------------------------------------------------------------------
# Trigger merging
# ---------------------------------------------------------------------------

def merge_triggers(conditionals, artefacts):
    """Merge router conditionals with taxonomy trigger sections."""
    triggers = []
    # Build lookup: target path → artefact trigger detail
    taxonomy_triggers = {}
    for art in artefacts:
        if art.get("taxonomy_file") and art.get("trigger"):
            # taxonomy_file is like "_Config/Taxonomy/Temporal/logs.md"
            # Router target is like "_Config/Taxonomy/Temporal/logs" (no .md)
            target_key = art["taxonomy_file"].replace(".md", "")
            taxonomy_triggers[target_key] = art["trigger"]

    for cond in conditionals:
        target = cond["target"]
        tax_trigger = taxonomy_triggers.get(target)
        if tax_trigger:
            triggers.append({
                "category": tax_trigger["category"],
                "condition": cond["condition"],
                "detail": tax_trigger["detail"],
                "target": target,
            })
        else:
            # Non-taxonomy target (e.g. skill)
            category = infer_trigger_category(cond["condition"])
            triggers.append({
                "category": category,
                "condition": cond["condition"],
                "detail": None,
                "target": target,
            })

    return triggers


# ---------------------------------------------------------------------------
# Enrichment discovery
# ---------------------------------------------------------------------------

def discover_skills(vault_root):
    """Find skills at _Config/Skills/*/SKILL.md."""
    skills_dir = os.path.join(vault_root, "_Config", "Skills")
    if not os.path.isdir(skills_dir):
        return []
    skills = []
    for entry in sorted(os.listdir(skills_dir)):
        skill_doc = os.path.join(skills_dir, entry, "SKILL.md")
        if os.path.isfile(skill_doc):
            rel = os.path.relpath(skill_doc, vault_root)
            skills.append({"name": entry, "skill_doc": rel})
    return skills


def discover_plugins(vault_root):
    """Find plugins at _Plugins/*/SKILL.md."""
    plugins_dir = os.path.join(vault_root, "_Plugins")
    if not os.path.isdir(plugins_dir):
        return []
    plugins = []
    for entry in sorted(os.listdir(plugins_dir)):
        skill_doc = os.path.join(plugins_dir, entry, "SKILL.md")
        if os.path.isfile(skill_doc):
            rel = os.path.relpath(skill_doc, vault_root)
            plugins.append({"name": entry, "skill_doc": rel})
    return plugins


def discover_styles(vault_root):
    """Find styles at _Config/Styles/*.md."""
    styles_dir = os.path.join(vault_root, "_Config", "Styles")
    if not os.path.isdir(styles_dir):
        return []
    styles = []
    for entry in sorted(os.listdir(styles_dir)):
        if entry.endswith(".md"):
            name = entry[:-3]  # strip .md
            rel = os.path.relpath(os.path.join(styles_dir, entry), vault_root)
            styles.append({"name": name, "style_doc": rel})
    return styles


# ---------------------------------------------------------------------------
# Environment detection
# ---------------------------------------------------------------------------

def detect_environment(vault_root):
    """Detect runtime environment."""
    return {
        "vault_root": str(vault_root),
        "platform": sys.platform,
        "python_version": platform.python_version(),
        "cli_available": shutil.which("brain") is not None,
    }


# ---------------------------------------------------------------------------
# Main compilation
# ---------------------------------------------------------------------------

def compile(vault_root):
    """Compile the full router from vault source files."""
    vault_root = str(vault_root)
    version = read_version(vault_root)
    sources = {}

    def track(rel_path):
        """Track a source file for hashing. Returns absolute path."""
        abs_path = os.path.join(vault_root, rel_path)
        if os.path.isfile(abs_path):
            sources[rel_path] = hash_file(abs_path)
        return abs_path

    # Parse router
    router_path = os.path.join("_Config", "router.md")
    track(router_path)
    always_rules, conditionals = parse_router(
        os.path.join(vault_root, router_path)
    )

    # Track VERSION
    version_path = os.path.join(".brain-core", "VERSION")
    track(version_path)

    # Discover artefact types
    living = scan_living_types(vault_root)
    temporal = scan_temporal_types(vault_root)
    all_types = living + temporal

    # Enrich with taxonomy — try exact folder name first, then derived key
    artefacts = []
    for t in all_types:
        classification = t["classification"]
        tax_subdir = "Living" if classification == "living" else "Temporal"

        # Prefer exact folder name match (e.g. "Wiki.md"), fall back to key
        tax_rel = None
        for candidate in [t["folder"], t["key"]]:
            candidate_rel = os.path.join(
                "_Config", "Taxonomy", tax_subdir, candidate + ".md"
            )
            if os.path.isfile(os.path.join(vault_root, candidate_rel)):
                tax_rel = candidate_rel
                break

        if tax_rel:
            track(tax_rel)
            parsed = parse_taxonomy_file(os.path.join(vault_root, tax_rel))
            artefacts.append({
                "folder": t["folder"],
                "type": t["type"],
                "key": t["key"],
                "classification": classification,
                "configured": True,
                "naming": parsed["naming"],
                "frontmatter": parsed["frontmatter"],
                "trigger": parsed["trigger"],
                "taxonomy_file": tax_rel,
                "template_file": parsed["template_file"],
                "path": t["path"],
            })
        else:
            artefacts.append({
                "folder": t["folder"],
                "type": t["type"],
                "key": t["key"],
                "classification": classification,
                "configured": False,
                "naming": None,
                "frontmatter": None,
                "trigger": None,
                "taxonomy_file": None,
                "template_file": None,
                "path": t["path"],
            })

    # Merge triggers
    triggers = merge_triggers(conditionals, artefacts)

    # Discover enrichments
    skills = discover_skills(vault_root)
    for s in skills:
        track(s["skill_doc"])

    plugins = discover_plugins(vault_root)
    for p in plugins:
        track(p["skill_doc"])

    styles = discover_styles(vault_root)
    for s in styles:
        track(s["style_doc"])

    # Build output
    source_hash = compute_source_hash(sources)

    compiled = {
        "meta": {
            "brain_core_version": version,
            "compiled_at": datetime.now(timezone.utc).astimezone().isoformat(),
            "source_hash": source_hash,
            "sources": sources,
        },
        "environment": detect_environment(vault_root),
        "always_rules": always_rules,
        "artefacts": artefacts,
        "triggers": triggers,
        "skills": skills,
        "plugins": plugins,
        "styles": styles,
    }

    return compiled


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    vault_root = find_vault_root()
    compiled = compile(vault_root)

    json_output = json.dumps(compiled, indent=2, ensure_ascii=False)

    if "--json" in sys.argv:
        print(json_output)
    else:
        output_path = os.path.join(str(vault_root), OUTPUT_PATH)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(json_output + "\n")

        art_count = len(compiled["artefacts"])
        configured = sum(1 for a in compiled["artefacts"] if a["configured"])
        trigger_count = len(compiled["triggers"])
        skill_count = len(compiled["skills"])
        print(
            f"Compiled router: {art_count} artefacts ({configured} configured), "
            f"{trigger_count} triggers, {skill_count} skills → {OUTPUT_PATH}",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
