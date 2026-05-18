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


def has_leading_frontmatter(text):
    """Return whether ``text`` begins with a frontmatter block."""
    if not text:
        return False
    return FM_RE.match(text) is not None


def parse_leading_frontmatter(text, *, allow_leading_blank_lines=False):
    """Parse one leading frontmatter block when present.

    Returns ``(fields, body)`` when a frontmatter block is found, otherwise
    ``None``. When ``allow_leading_blank_lines`` is true, leading blank lines
    are ignored before matching the block.
    """
    if not text:
        return None

    candidate = text.lstrip("\r\n") if allow_leading_blank_lines else text
    m = FM_RE.match(candidate)
    if not m:
        return None
    return _parse_yaml_lines(m.group(1)), candidate[m.end():]


def _coerce_frontmatter_list(value):
    """Normalise a frontmatter field into a list for additive union rules."""
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    return [value]


def _ordered_union(outer, nested):
    """Preserve outer order, then append unique items from nested."""
    merged = []
    seen = set()
    for item in _coerce_frontmatter_list(outer) + _coerce_frontmatter_list(nested):
        if item in seen:
            continue
        seen.add(item)
        merged.append(item)
    return merged


def _merge_duplicate_frontmatter_fields(outer_fields, nested_fields):
    """Normalize duplicate frontmatter using the conservative repair policy.

    The outer block remains authoritative for every field except ``tags``,
    which use ordered additive union with dedup.
    """
    merged_fields = dict(outer_fields)

    # Preserve the absence of tags entirely rather than synthesising `tags: []`
    # on repaired files that never had tags in either block.
    if "tags" in outer_fields or "tags" in nested_fields:
        merged_fields["tags"] = _ordered_union(
            outer_fields.get("tags"),
            nested_fields.get("tags"),
        )

    return merged_fields


def inspect_duplicate_frontmatter_document(text):
    """Inspect a full markdown document for an accidental second frontmatter block.

    Returns ``None`` when the document has zero or one frontmatter block.
    When a duplicate block is found at the start of the body, returns a dict
    containing the outer fields, nested fields, normalized fields, and body.
    """
    outer = parse_leading_frontmatter(text)
    if outer is None:
        return None

    outer_fields, outer_body = outer
    nested = parse_leading_frontmatter(
        outer_body,
        allow_leading_blank_lines=True,
    )
    if nested is None:
        return None

    nested_fields, nested_body = nested
    merged_fields = _merge_duplicate_frontmatter_fields(outer_fields, nested_fields)
    return {
        "outer_fields": outer_fields,
        "nested_fields": nested_fields,
        "merged_fields": merged_fields,
        "body": nested_body,
    }


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
