"""Frontmatter parsing and serialisation."""

import re

FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def parse_frontmatter(text):
    """Extract frontmatter fields from markdown text. Returns (fields, body)."""
    m = FM_RE.match(text)
    if not m:
        return {}, text

    fm_text = m.group(1)
    body = text[m.end():]
    fields = {}
    # Simple YAML parser for flat fields and list fields
    fm_lines = fm_text.split("\n")
    pending_list_key = None

    for line in fm_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # List item under a pending key
        if stripped.startswith("- ") and pending_list_key:
            fields[pending_list_key].append(stripped[2:].strip().strip("'\""))
            continue

        # Non-list-item ends any pending list collection
        if pending_list_key:
            pending_list_key = None

        colon_idx = stripped.find(":")
        if colon_idx < 0:
            continue

        key = stripped[:colon_idx].strip()
        value = stripped[colon_idx + 1:].strip()

        # Handle inline list: [item1, item2]
        if value.startswith("["):
            inner = value.strip("[]")
            fields[key] = [t.strip().strip("'\"") for t in inner.split(",") if t.strip()]
            continue

        if not value:
            # Empty value after colon — could be a multi-line list; collect on next lines
            fields[key] = []
            pending_list_key = key
            continue

        # Strip quotes
        if (value.startswith("'") and value.endswith("'")) or \
           (value.startswith('"') and value.endswith('"')):
            value = value[1:-1]

        fields[key] = value

    return fields, body


def serialize_frontmatter(fields, body=""):
    """Produce markdown with YAML frontmatter from a fields dict and body.

    Handles scalars and list fields (tags, aliases, cssclasses, etc.)
    as multi-line YAML lists (- item). Round-trips with parse_frontmatter().
    """
    lines = ["---"]
    for key, value in fields.items():
        if isinstance(value, list):
            if value:
                lines.append(f"{key}:")
                for item in value:
                    lines.append(f"  - {item}")
            else:
                lines.append(f"{key}: []")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    lines.append("")  # blank line after frontmatter

    fm_block = "\n".join(lines)
    if body:
        return fm_block + body
    return fm_block
