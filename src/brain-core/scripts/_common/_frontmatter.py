"""Frontmatter parsing and serialisation."""

import re

FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def _parse_yaml_lines(fm_text):
    """Parse the YAML-ish body between the `---` delimiters into a fields dict."""
    fields = {}
    pending_list_key = None

    for line in fm_text.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if stripped.startswith("- ") and pending_list_key:
            fields[pending_list_key].append(stripped[2:].strip().strip("'\""))
            continue

        if pending_list_key:
            pending_list_key = None

        colon_idx = stripped.find(":")
        if colon_idx < 0:
            continue

        key = stripped[:colon_idx].strip()
        value = stripped[colon_idx + 1:].strip()

        if value.startswith("["):
            inner = value.strip("[]")
            fields[key] = [t.strip().strip("'\"") for t in inner.split(",") if t.strip()]
            continue

        if not value:
            fields[key] = []
            pending_list_key = key
            continue

        if (value.startswith("'") and value.endswith("'")) or \
           (value.startswith('"') and value.endswith('"')):
            value = value[1:-1]

        fields[key] = value

    return fields


def parse_frontmatter(text):
    """Extract frontmatter fields from markdown text. Returns (fields, body)."""
    m = FM_RE.match(text)
    if not m:
        return {}, text
    return _parse_yaml_lines(m.group(1)), text[m.end():]


def read_frontmatter(path):
    """Read frontmatter from a markdown file, stopping at the closing ``---``.

    Returns a fields dict, or ``{}`` when frontmatter is absent or unterminated.
    Use :func:`read_artefact` when the body is also needed.
    """
    with open(path, "r", encoding="utf-8") as f:
        first = f.readline()
        if first.strip() != "---":
            return {}
        lines = []
        for line in f:
            if line.rstrip("\n").strip() == "---":
                return _parse_yaml_lines("\n".join(lines))
            lines.append(line.rstrip("\n"))
    return {}


def read_artefact(path):
    """Read a markdown file and return ``(fields, body)``.

    Whole-file read. Use when the caller needs the body; use
    :func:`read_frontmatter` when only the fields are needed.
    """
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    return parse_frontmatter(text)


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
