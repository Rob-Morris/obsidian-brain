#!/usr/bin/env python3
"""
compile_router.py — Brain-core compiled router generator

Transforms human-readable vault config (router.md, taxonomy files, skills,
styles, plugins) into .brain/local/compiled-router.json — a local, gitignored,
hash-invalidated cache that all brain-core tools read.

Usage:
    python3 compile_router.py           # write .brain/local/compiled-router.json
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

from _common import (
    PLACEHOLDER_TOKEN_RE,
    TEMPORAL_DIR,
    find_vault_root,
    is_system_dir,
    read_version,
    safe_write,
    scan_living_types,
    scan_temporal_types,
)
from _common._artefacts import pattern_has_date_tokens
import session

OUTPUT_PATH = os.path.join(".brain", "local", "compiled-router.json")


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
# Taxonomy parsing
# ---------------------------------------------------------------------------

def parse_status_enum(content):
    """Extract valid status values from taxonomy content.

    Recognises three patterns used across taxonomy files:
    1. Inline YAML comment:  status: default  # val1 | val2 | val3
    2. Markdown table:       | `value` | description |  (under ## Lifecycle)
    3. Prose line:           Status values: `val1`, `val2`, `val3`.
    """
    # Pattern 1: inline comment in frontmatter YAML block
    comment_match = re.search(
        r"^status:\s*\S+\s*#\s*(.+)$", content, re.MULTILINE
    )
    if comment_match:
        return [v.strip() for v in comment_match.group(1).split("|") if v.strip()]

    # Pattern 2: markdown table rows with backtick-delimited status values
    # Scope to the ## Lifecycle section so unrelated tables elsewhere (e.g.
    # ## Naming placeholder tables) don't leak into the status enum.
    lifecycle_match = re.search(
        r"^## Lifecycle\s*\n(.*?)(?=^## |\Z)",
        content,
        re.MULTILINE | re.DOTALL,
    )
    search_scope = lifecycle_match.group(1) if lifecycle_match else content
    table_values = re.findall(
        r"^\|\s*`([^`]+)`\s*\|", search_scope, re.MULTILINE
    )
    # Filter out header rows (e.g. "Status")
    table_values = [v for v in table_values if v.lower() != "status"]
    if table_values:
        return table_values

    # Pattern 3: prose line "Status values: `val1`, `val2`, `val3`."
    prose_match = re.search(
        r"Status values?:\s*(.+)", content, re.IGNORECASE
    )
    if prose_match:
        return re.findall(r"`([^`]+)`", prose_match.group(1))

    return None


def parse_terminal_statuses(content, status_enum):
    """Extract terminal statuses from the ## Terminal Status or ## Archiving section.

    Looks for backtick-delimited status values mentioned in terminal status
    or archiving instructions (e.g. "reaches `implemented` status",
    "status: adopted"). Cross-references against status_enum when the
    section uses natural language (e.g. "Adopted ideas remain searchable").
    """
    terminal_match = re.search(
        r"^## (?:Terminal Status|Archiving)\s*\n(.*?)(?=^## |\Z)",
        content,
        re.MULTILINE | re.DOTALL,
    )
    if not terminal_match:
        return None
    terminal_text = terminal_match.group(1)
    # Find status values referenced via `status: value` or `value` status
    candidates = re.findall(r"`(\w+)`\s+status", terminal_text)
    candidates += re.findall(r"status:\s*(\w+)`", terminal_text)
    # Also match "Set `status: value`" pattern
    candidates += re.findall(r"status:\s*(\w+)", terminal_text)
    # Cross-reference: if terminal-status text uses a status enum value as an
    # adjective describing the artefact (e.g. "Graduated ideas remain searchable",
    # "Published writing...can be archived"). Match at word start to avoid
    # false positives like "active folder".
    if status_enum:
        for val in status_enum:
            # Match "Graduated ideas", "Published writing" — capitalised status
            # at sentence/clause start followed by the artefact noun
            pattern = r"(?:^|\.\s+)" + re.escape(val.capitalize()) + r"\b"
            if re.search(pattern, terminal_text) and val not in candidates:
                candidates.append(val)
    # Deduplicate preserving order
    seen = set()
    terminal = []
    for v in candidates:
        if v not in seen:
            seen.add(v)
            terminal.append(v)
    return terminal if terminal else None


_BUILTIN_NAMING_PLACEHOLDERS = {
    "yyyy", "yyyymmdd", "yyyy-mm-dd", "mm", "dd", "ddd",
    "Title", "title", "slug", "name",
    "sourcedoctype",
}


def _unwrap_backticks(cell):
    """Strip wrapping backticks from a single-value cell.

    '`foo`' → 'foo'. 'foo' → 'foo'. '' → ''.
    """
    cell = cell.strip()
    m = re.match(r"^`([^`]*)`$", cell)
    return m.group(1) if m else cell


def _split_backticked_values(cell):
    """Parse a value cell: '`a`, `b`, `c`' → ['a','b','c']; '`*`' or '*' → ['*'].

    Empty or whitespace-only cell returns []. Cells without any backticked
    value (and not '*') raise ValueError.
    """
    cell = cell.strip()
    if not cell:
        return []
    if cell == "*" or cell == "`*`":
        return ["*"]
    values = re.findall(r"`([^`]+)`", cell)
    if not values:
        raise ValueError(
            f"Naming value cell '{cell}' contains no backticked literals or `*` wildcard"
        )
    return values


def _parse_markdown_table(text):
    """Parse a markdown table. Returns list of row dicts keyed by lowercased header.

    Stops at the first blank line after the table. Skips the |---|---| separator.
    Returns [] if no table is present.
    """
    lines = text.split("\n")
    rows = []
    headers = None
    in_table = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if in_table:
                break
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if headers is None:
                headers = [c.strip().lower() for c in cells]
                in_table = True
                continue
            if all(re.match(r"^:?-+:?$", c) for c in cells):
                continue
            if len(cells) != len(headers):
                continue
            rows.append(dict(zip(headers, cells)))
        elif in_table:
            break
    return rows


def _parse_advanced_naming(naming_text):
    """Parse advanced table-form ## Naming. Returns (folder, rules, placeholders).

    Raises ValueError for missing rules, malformed value cells, or undeclared
    non-built-in placeholders.
    """
    folder_match = re.search(r"Primary folder:\s*`([^`]+)`", naming_text)
    folder = folder_match.group(1) if folder_match else None

    rules_match = re.search(
        r"^### Rules\s*\n(.*?)(?=^### |^## |\Z)",
        naming_text,
        re.MULTILINE | re.DOTALL,
    )
    if not rules_match:
        raise ValueError("## Naming advanced form requires a ### Rules subsection")

    rules_rows = _parse_markdown_table(rules_match.group(1))
    if not rules_rows:
        raise ValueError("## Naming ### Rules table has no data rows")

    rules = []
    for row in rules_rows:
        match_field = _unwrap_backticks(row.get("match field", ""))
        match_values_cell = row.get("match values", "")
        pattern = _unwrap_backticks(row.get("pattern", ""))
        date_source = _unwrap_backticks(row.get("date source", "")) or None
        if not pattern:
            raise ValueError("## Naming ### Rules row missing pattern")
        if not match_field:
            raise ValueError(
                "## Naming ### Rules row missing match field "
                "(use `*` values for unconditional rules or use simple one-line form)"
            )
        match_values = _split_backticked_values(match_values_cell)
        if not match_values:
            raise ValueError(
                f"## Naming ### Rules row for '{match_field}' has blank match values "
                "(use `*` for wildcard)"
            )
        rules.append({
            "match_field": match_field,
            "match_values": match_values,
            "pattern": pattern,
            "date_source": date_source,
        })

    placeholders = []
    ph_match = re.search(
        r"^### Placeholders\s*\n(.*?)(?=^### |^## |\Z)",
        naming_text,
        re.MULTILINE | re.DOTALL,
    )
    if ph_match:
        for row in _parse_markdown_table(ph_match.group(1)):
            name = _unwrap_backticks(row.get("placeholder", ""))
            field = _unwrap_backticks(row.get("field", ""))
            required_when = _unwrap_backticks(row.get("required when field", ""))
            required_vals_cell = row.get("required values", "").strip()
            regex_cell = row.get("regex", "").strip()
            if not name:
                raise ValueError("## Naming ### Placeholders row missing placeholder name")
            if not field:
                raise ValueError(
                    f"## Naming ### Placeholders row for '{name}' missing field"
                )
            required_values = (
                _split_backticked_values(required_vals_cell)
                if required_vals_cell and required_when
                else None
            )
            placeholders.append({
                "name": name,
                "field": field,
                "required_when_field": required_when or None,
                "required_values": required_values,
                "regex": _unwrap_backticks(regex_cell) if regex_cell else None,
            })

    declared_names = {p["name"] for p in placeholders}
    declared_names |= {p["name"].lower() for p in placeholders}
    for rule in rules:
        for token in PLACEHOLDER_TOKEN_RE.findall(rule["pattern"]):
            if token in _BUILTIN_NAMING_PLACEHOLDERS or token.lower() in _BUILTIN_NAMING_PLACEHOLDERS:
                continue
            if token in declared_names:
                continue
            raise ValueError(
                f"## Naming pattern '{rule['pattern']}' uses undeclared "
                f"placeholder '{{{token}}}'. Declare it in ### Placeholders."
            )

    return folder, rules, placeholders


def _parse_naming_section(content):
    """Parse ## Naming section. Returns dict or None.

    Simple form ('`pattern.md` in `folder/`'):
        {"pattern": "...", "folder": "...",
         "rules": [{"match_field": None, "match_values": None, "pattern": "..."}],
         "placeholders": []}

    Advanced form (### Rules [+ ### Placeholders] subsections):
        {"pattern": None, "folder": "...",
         "rules": [{...}, ...], "placeholders": [{...}, ...]}

    Returns None if no ## Naming section present or section has no content.
    """
    naming_match = re.search(
        r"^## Naming\s*\n(.*?)(?=^## |\Z)", content, re.MULTILINE | re.DOTALL
    )
    if not naming_match:
        return None
    naming_text = naming_match.group(1)

    if re.search(r"^### Rules\s*$", naming_text, re.MULTILINE):
        folder, rules, placeholders = _parse_advanced_naming(naming_text)
        return {
            "pattern": None,
            "folder": folder,
            "rules": rules,
            "placeholders": placeholders,
        }

    naming_text_stripped = naming_text.strip()
    pattern_match = re.search(r"`([^`]+\.md)`", naming_text_stripped)
    folder_match = re.search(r"in `([^`]+)`", naming_text_stripped)
    date_source_match = re.search(
        r"date\s+source:?\s*`([^`]+)`", naming_text_stripped, re.IGNORECASE
    )
    if not (pattern_match or folder_match):
        return None
    pattern = pattern_match.group(1) if pattern_match else None
    folder = folder_match.group(1) if folder_match else None
    date_source = date_source_match.group(1) if date_source_match else None
    rules = (
        [{
            "match_field": None,
            "match_values": None,
            "pattern": pattern,
            "date_source": date_source,
        }]
        if pattern
        else []
    )
    return {
        "pattern": pattern,
        "folder": folder,
        "rules": rules,
        "placeholders": [],
    }


def finalize_naming_date_sources(naming, classification, type_key):
    """Apply classification defaults for ``date_source`` and validate.

    - Temporal rules with date tokens and no explicit ``date_source`` default
      to ``created``.
    - Living rules with date tokens and no ``date_source`` → ``ValueError``.
    - Rules without date tokens are left untouched (``date_source`` stays
      whatever the taxonomy declared, typically ``None``).

    Returns ``naming`` (mutated in place for the rules list).
    """
    if not naming:
        return naming
    for rule in naming.get("rules") or []:
        pattern = rule.get("pattern") or ""
        if not pattern_has_date_tokens(pattern):
            continue
        if rule.get("date_source"):
            continue
        if classification == "temporal":
            rule["date_source"] = "created"
            continue
        raise ValueError(
            f"Type '{type_key}' rule '{pattern}' has date tokens but no "
            f"`date_source` declared. Add a `date source` column to the "
            f"### Rules table (or switch to the simple one-line form without "
            f"date tokens)."
        )
    return naming


def parse_taxonomy_file(path):
    """Parse a taxonomy .md file, extracting Naming, Frontmatter, Trigger, Template sections."""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    result = {
        "naming": None,
        "frontmatter": None,
        "trigger": None,
        "template_file": None,
        "on_status_change": None,
    }

    result["naming"] = _parse_naming_section(content)

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

    # Extract status enum and terminal statuses from full content
    status_enum = parse_status_enum(content)
    terminal_statuses = parse_terminal_statuses(content, status_enum)
    if result["frontmatter"]:
        result["frontmatter"]["status_enum"] = status_enum
        result["frontmatter"]["terminal_statuses"] = terminal_statuses
    elif status_enum or terminal_statuses:
        result["frontmatter"] = {
            "type": None,
            "required": [],
            "status_enum": status_enum,
            "terminal_statuses": terminal_statuses,
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

    # Parse ## On Status Change — optional per-status hook that overrides the
    # {status}_at convention. Format:
    #
    #   ## On Status Change
    #
    #   When `status` transitions to `published`, set `publisheddate` to today.
    #
    # Compiles to {"published": {"set": {"publisheddate": "today"}}}.
    hooks_match = re.search(
        r"^## On Status Change\s*\n(.*?)(?=^## |\Z)", content, re.MULTILINE | re.DOTALL
    )
    if hooks_match:
        hooks = {}
        for m in re.finditer(
            r"transitions?\s+to\s+`([^`]+)`[^\n]*?set\s+`([^`]+)`\s+to\s+([^\n.]+)",
            hooks_match.group(1),
            re.IGNORECASE,
        ):
            status_val = m.group(1).strip()
            field_name = m.group(2).strip()
            raw_value = m.group(3).strip().rstrip(".").strip("`").strip()
            hooks.setdefault(status_val, {}).setdefault("set", {})[field_name] = raw_value
        if hooks:
            result["on_status_change"] = hooks

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
            # Non-list line ends the current section (headers, prose, etc.)
            section = None
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
            skills.append({"name": entry, "skill_doc": rel, "source": "user"})
    return skills


def discover_core_skills(vault_root):
    """Find core skills at .brain-core/skills/*/SKILL.md."""
    skills_dir = os.path.join(vault_root, ".brain-core", "skills")
    if not os.path.isdir(skills_dir):
        return []
    skills = []
    for entry in sorted(os.listdir(skills_dir)):
        skill_doc = os.path.join(skills_dir, entry, "SKILL.md")
        if os.path.isfile(skill_doc):
            rel = os.path.relpath(skill_doc, vault_root)
            skills.append({"name": entry, "skill_doc": rel, "source": "core"})
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


def _parse_memory_triggers(path):
    """Extract triggers list from YAML frontmatter of a memory file.

    Handles both inline format (triggers: [a, b]) and list format:
        triggers:
          - a
          - b
    """
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    # Check for YAML frontmatter
    if not content.startswith("---"):
        return []

    fm_end = content.find("---", 3)
    if fm_end == -1:
        return []
    frontmatter = content[3:fm_end]

    # Inline format: triggers: [a, b, c]
    inline_match = re.search(
        r"^triggers:\s*\[([^\]]*)\]", frontmatter, re.MULTILINE
    )
    if inline_match:
        return [t.strip().strip("'\"") for t in inline_match.group(1).split(",") if t.strip()]

    # List format: triggers:\n  - a\n  - b
    list_match = re.search(
        r"^triggers:\s*\n((?:\s+-\s+.+\n?)+)", frontmatter, re.MULTILINE
    )
    if list_match:
        return re.findall(r"^\s+-\s+(.+)", list_match.group(1), re.MULTILINE)

    return []


def discover_memories(vault_root):
    """Find memories at _Config/Memories/*.md, excluding README.md."""
    memories_dir = os.path.join(vault_root, "_Config", "Memories")
    if not os.path.isdir(memories_dir):
        return []
    memories = []
    for entry in sorted(os.listdir(memories_dir)):
        if not entry.endswith(".md") or entry.upper() == "README.MD":
            continue
        path = os.path.join(memories_dir, entry)
        name = entry[:-3]  # strip .md
        triggers = _parse_memory_triggers(path)
        rel = os.path.relpath(path, vault_root)
        memories.append({"name": name, "triggers": triggers, "memory_doc": rel})
    return memories


def count_memories(vault_root):
    """Count memory files at _Config/Memories/*.md, excluding README.md.

    Lighter than ``discover_memories`` — skips file reads (no trigger parsing).
    """
    memories_dir = os.path.join(vault_root, "_Config", "Memories")
    if not os.path.isdir(memories_dir):
        return 0
    return sum(
        1 for entry in os.listdir(memories_dir)
        if entry.endswith(".md") and entry.upper() != "README.MD"
    )


def resource_counts(vault_root):
    """Return ``{router_key: count}`` for all discoverable resource categories.

    Canonical mapping between discovery functions and router keys — used by
    the server's staleness check to detect new or deleted resources without
    a full recompile.
    """
    return {
        "artefacts": len(scan_living_types(vault_root))
                     + len(scan_temporal_types(vault_root)),
        "skills": len(discover_skills(vault_root))
                  + len(discover_core_skills(vault_root)),
        "memories": count_memories(vault_root),
        "styles": len(discover_styles(vault_root)),
        "plugins": len(discover_plugins(vault_root)),
    }


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

    # Parse system rules from session-core. A valid `.brain-core/` install is
    # version-bound and atomic; if this file is missing, the install is broken.
    session_core_path = os.path.join(".brain-core", "session-core.md")
    session_core_abs = os.path.join(vault_root, session_core_path)
    if not os.path.isfile(session_core_abs):
        raise FileNotFoundError(
            "Missing required .brain-core/session-core.md. "
            "The .brain-core install is incomplete or broken."
        )
    track(session_core_path)
    system_rules, _ = parse_router(session_core_abs)

    # Merge: system rules first, vault-specific additions after
    always_rules = system_rules + always_rules

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
            parsed["naming"] = finalize_naming_date_sources(
                parsed.get("naming"), classification, t["key"]
            )
            artefacts.append({
                "folder": t["folder"],
                "type": t["type"],
                "key": t["key"],
                "classification": classification,
                "configured": True,
                "naming": parsed["naming"],
                "frontmatter": parsed["frontmatter"],
                "frontmatter_type": (parsed.get("frontmatter") or {}).get("type") or t["type"],
                "trigger": parsed["trigger"],
                "taxonomy_file": tax_rel,
                "template_file": parsed["template_file"],
                "on_status_change": parsed.get("on_status_change"),
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
                "frontmatter_type": t["type"],
                "trigger": None,
                "taxonomy_file": None,
                "template_file": None,
                "on_status_change": None,
                "path": t["path"],
            })

    # Merge triggers
    triggers = merge_triggers(conditionals, artefacts)

    # Discover enrichments
    skills = discover_skills(vault_root)
    for s in skills:
        track(s["skill_doc"])

    core_skills = discover_core_skills(vault_root)
    for s in core_skills:
        track(s["skill_doc"])
    skills = core_skills + skills

    plugins = discover_plugins(vault_root)
    for p in plugins:
        track(p["skill_doc"])

    styles = discover_styles(vault_root)
    for s in styles:
        track(s["style_doc"])

    memories = discover_memories(vault_root)
    for m in memories:
        track(m["memory_doc"])

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
        "memories": memories,
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
        safe_write(output_path, json_output + "\n", bounds=str(vault_root))

        art_count = len(compiled["artefacts"])
        configured = sum(1 for a in compiled["artefacts"] if a["configured"])
        trigger_count = len(compiled["triggers"])
        skill_count = len(compiled["skills"])
        memory_count = len(compiled["memories"])
        try:
            model = session.build_session_model(compiled, str(vault_root))
            session.persist_session_markdown(model, str(vault_root))
        except Exception as e:
            print(f"Warning: failed to refresh {session.SESSION_MARKDOWN_REL}: {e}",
                  file=sys.stderr)
        print(
            f"Compiled router: {art_count} artefacts ({configured} configured), "
            f"{trigger_count} triggers, {skill_count} skills, "
            f"{memory_count} memories → {OUTPUT_PATH}",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
