"""Markdown structural helpers — headings, callouts, and target resolution."""

from __future__ import annotations

import re

from ._selector import normalize_structural_selector

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)[^\S\n]*$", re.MULTILINE)
_FENCE_RE = re.compile(r"^(`{3,}|~{3,})", re.MULTILINE)
_CALLOUT_TITLE_RE = re.compile(r"^\s*>\s*(\[\![^\]]+\][^\n]*)\s*$")
_BACKTICK_RUN_RE = re.compile(r"`+")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_MATH_FENCE_RE = re.compile(r"\$\$")
_RAW_HTML_BLOCK_RE = re.compile(
    r"<(pre|script|style)\b[^>]*>.*?</\1\s*>",
    re.DOTALL | re.IGNORECASE,
)


def literal_ranges(body):
    """Flat ``(start, end)`` skip list covering every markdown literal region."""
    return [(s, e) for _, s, e in markdown_region_ranges(body)]


def in_any_range(pos, ranges):
    """True when *pos* falls within any ``(start, end)`` in *ranges* (end-exclusive)."""
    return any(s <= pos < e for s, e in ranges)


def collect_headings(body):
    """Collect all markdown headings outside literal regions."""
    skip = literal_ranges(body)
    headings = []
    for m in _HEADING_RE.finditer(body):
        if in_any_range(m.start(), skip):
            continue
        headings.append((
            m.start(),
            len(m.group(1)),
            m.group(2).strip(),
            m.group(0).strip(),
        ))
    return headings


def parse_structural_anchor_line(text):
    """Parse a heading or callout-title line from the start of ``text``."""
    for line in text.splitlines():
        if not line.strip():
            continue
        heading_match = _HEADING_RE.match(line)
        if heading_match:
            return {
                "kind": "heading",
                "raw": heading_match.group(0).strip(),
                "level": len(heading_match.group(1)),
            }
        callout_match = _CALLOUT_TITLE_RE.match(line)
        if callout_match:
            return {
                "kind": "callout",
                "raw": callout_match.group(1).strip(),
                "level": None,
            }
        return None
    return None


def fenced_ranges(body):
    """Return list of (start, end) character ranges for fenced code blocks."""
    fences = [(m.start(), m.end(), m.group(1)[0]) for m in _FENCE_RE.finditer(body)]
    ranges = []
    i = 0
    while i < len(fences):
        open_start, _, char = fences[i]
        close_idx = None
        for j in range(i + 1, len(fences)):
            if fences[j][2] == char:
                close_idx = j
                break
        if close_idx is not None:
            ranges.append((open_start, fences[close_idx][1]))
            i = close_idx + 1
        else:
            ranges.append((open_start, len(body)))
            i += 1
    return ranges


def inline_code_ranges(body, skip=None):
    """Return list of (start, end) ranges for inline code spans."""
    if skip is None:
        skip = fenced_ranges(body)
    runs = []
    for m in _BACKTICK_RUN_RE.finditer(body):
        if in_any_range(m.start(), skip):
            continue
        runs.append((m.start(), m.end()))

    ranges = []
    i = 0
    while i < len(runs):
        open_start = runs[i][0]
        open_len = runs[i][1] - open_start
        close_idx = None
        for j in range(i + 1, len(runs)):
            if runs[j][1] - runs[j][0] == open_len:
                close_idx = j
                break
        if close_idx is not None:
            ranges.append((open_start, runs[close_idx][1]))
            i = close_idx + 1
        else:
            i += 1
    return ranges


def html_comment_ranges(body):
    """Return list of (start, end) ranges for HTML comments (``<!-- ... -->``)."""
    return [(m.start(), m.end()) for m in _HTML_COMMENT_RE.finditer(body)]


def math_block_ranges(body, skip=None):
    """Return list of (start, end) ranges for ``$$...$$`` math blocks."""
    if skip is None:
        skip = fenced_ranges(body) + inline_code_ranges(body)
    markers = []
    for m in _MATH_FENCE_RE.finditer(body):
        if in_any_range(m.start(), skip):
            continue
        markers.append((m.start(), m.end()))

    ranges = []
    i = 0
    while i + 1 < len(markers):
        ranges.append((markers[i][0], markers[i + 1][1]))
        i += 2
    return ranges


def raw_html_block_ranges(body):
    """Return list of (start, end) ranges for raw HTML blocks."""
    return [(m.start(), m.end()) for m in _RAW_HTML_BLOCK_RE.finditer(body)]


REGION_FENCE = "fence"
REGION_RAW_HTML = "raw_html"
REGION_MATH_BLOCK = "math_block"
REGION_INLINE_CODE = "inline_code"
REGION_HTML_COMMENT = "html_comment"


def markdown_region_ranges(body):
    """Return typed ``(kind, start, end)`` regions for literal markdown contexts."""
    fence = fenced_ranges(body)
    raw_html = raw_html_block_ranges(body)
    inline = inline_code_ranges(body, skip=fence)
    math = math_block_ranges(body, skip=fence + inline)
    comment = html_comment_ranges(body)

    regions = []
    regions.extend((REGION_FENCE, s, e) for s, e in fence)
    regions.extend((REGION_RAW_HTML, s, e) for s, e in raw_html)
    regions.extend((REGION_MATH_BLOCK, s, e) for s, e in math)
    regions.extend((REGION_INLINE_CODE, s, e) for s, e in inline)
    regions.extend((REGION_HTML_COMMENT, s, e) for s, e in comment)
    return regions


def _normalize_structural_text(text):
    """Normalize structural text for case-insensitive exact matching."""
    return " ".join(text.strip().split()).lower()


def _line_offsets(body):
    """Return ``(lines, starts)`` for ``body`` preserving line endings."""
    lines = body.splitlines(keepends=True)
    if not body:
        return [], []

    starts = []
    pos = 0
    for line in lines:
        starts.append(pos)
        pos += len(line)
    return lines, starts


def _is_blockquote_line(line):
    return line.lstrip().startswith(">")


def _match_heading_line(line):
    return _HEADING_RE.match(line.rstrip("\n"))


def _match_callout_title_line(line):
    return _CALLOUT_TITLE_RE.match(line.rstrip("\n"))


def _scan_structural_nodes(body):
    """Scan the body into targetable heading and callout nodes."""
    skip = literal_ranges(body)
    lines, starts = _line_offsets(body)

    heading_nodes = []
    for line, start in zip(lines, starts):
        if in_any_range(start, skip):
            continue
        match = _match_heading_line(line)
        if not match:
            continue
        heading_nodes.append(
            {
                "kind": "heading",
                "raw": match.group(0).strip(),
                "text": match.group(2).strip(),
                "normalized_text": _normalize_structural_text(match.group(2)),
                "level": len(match.group(1)),
                "start": start,
                "anchor_end": start + len(line),
                "section_end": len(body),
                "body_start": start + len(line),
                "body_end": len(body),
                "intro_end": len(body),
                "parent": None,
            }
        )

    stack = []
    for node in heading_nodes:
        while stack and stack[-1]["level"] >= node["level"]:
            stack.pop()
        node["parent"] = stack[-1] if stack else None
        stack.append(node)

    for idx, node in enumerate(heading_nodes):
        section_end = len(body)
        intro_end = section_end
        next_sibling = None
        for other in heading_nodes[idx + 1:]:
            if other["level"] <= node["level"]:
                section_end = other["start"]
                next_sibling = other
                break
        for other in heading_nodes[idx + 1:]:
            if other["start"] >= section_end:
                break
            if other["level"] > node["level"]:
                intro_end = other["start"]
                break
        node["section_end"] = section_end
        node["body_end"] = section_end
        node["intro_end"] = intro_end
        node["next_sibling_raw"] = next_sibling["raw"] if next_sibling else None
        node["next_sibling_level"] = next_sibling["level"] if next_sibling else None

    callout_nodes = []
    i = 0
    while i < len(lines):
        line = lines[i]
        start = starts[i]
        if in_any_range(start, skip):
            i += 1
            continue

        title_match = _match_callout_title_line(line)
        prev_is_blockquote = False
        if i > 0 and not in_any_range(starts[i - 1], skip):
            prev_is_blockquote = _is_blockquote_line(lines[i - 1])
        if not title_match or prev_is_blockquote:
            i += 1
            continue

        end = start + len(line)
        j = i + 1
        while j < len(lines):
            next_start = starts[j]
            if in_any_range(next_start, skip):
                break
            if not _is_blockquote_line(lines[j]):
                break
            end = next_start + len(lines[j])
            j += 1

        callout_nodes.append(
            {
                "kind": "callout",
                "raw": title_match.group(1).strip(),
                "text": title_match.group(1).strip(),
                "normalized_text": _normalize_structural_text(title_match.group(1)),
                "level": None,
                "start": start,
                "anchor_end": start + len(line),
                "section_end": end,
                "body_start": start + len(line),
                "body_end": end,
                "intro_end": None,
                "parent": None,
                "next_sibling_raw": None,
                "next_sibling_level": None,
            }
        )
        i = j

    for node in callout_nodes:
        parent = None
        for heading in heading_nodes:
            if heading["start"] < node["start"] < heading["section_end"]:
                if parent is None or heading["start"] > parent["start"]:
                    parent = heading
        node["parent"] = parent

    nodes = sorted(heading_nodes + callout_nodes, key=lambda item: item["start"])
    intro_end = heading_nodes[0]["start"] if heading_nodes else len(body)
    root = {
        "kind": "body",
        "raw": ":body",
        "text": ":body",
        "normalized_text": ":body",
        "level": None,
        "start": 0,
        "anchor_end": 0,
        "section_end": len(body),
        "body_start": 0,
        "body_end": len(body),
        "intro_end": intro_end,
        "parent": None,
    }
    return root, nodes


def _container_descendants(container, nodes):
    """Return nodes inside ``container``'s search space in document order."""
    start = 0 if container["kind"] == "body" else container["body_start"]
    end = container["section_end"]
    return [node for node in nodes if start <= node["start"] < end and node is not container]


def _heading_target_spec(target):
    stripped = target.strip()
    if stripped.startswith("#"):
        markers = stripped.split()[0]
        return len(markers), _normalize_structural_text(stripped[len(markers):])
    return None, _normalize_structural_text(stripped)


def _matches_target(node, target):
    stripped = target.strip()
    if stripped == ":body":
        return node["kind"] == "body"
    if stripped.startswith("[!"):
        return (
            node["kind"] == "callout"
            and node["normalized_text"] == _normalize_structural_text(stripped)
        )
    level, text = _heading_target_spec(stripped)
    return (
        node["kind"] == "heading"
        and node["normalized_text"] == text
        and (level is None or node["level"] == level)
    )


def _step_display(raw, occurrence):
    if occurrence and occurrence > 1:
        return f"{raw} [{occurrence}]"
    return raw


def _node_chain(node):
    chain = []
    current = node
    while current is not None and current["kind"] != "body":
        chain.append(current)
        current = current.get("parent")
    return list(reversed(chain))


def _candidate_label(node, occurrence=None):
    parts = [ancestor["raw"] for ancestor in _node_chain(node)]
    if not parts:
        label = ":body"
    else:
        label = " > ".join(parts)
    if occurrence:
        label = f"{label} [occurrence {occurrence}]"
    return label


def _select_match(matches, target, occurrence, context):
    if not matches:
        where = f" within {context}" if context else ""
        raise ValueError(f"Target '{target}' not found{where}")

    if occurrence is not None:
        if occurrence > len(matches):
            raise ValueError(
                f"selector.occurrence={occurrence} is out of range for target '{target}' "
                f"({len(matches)} matches found)"
            )
        return matches[occurrence - 1], occurrence

    if len(matches) == 1:
        return matches[0], 1

    candidates = "; ".join(
        _candidate_label(node, idx + 1) for idx, node in enumerate(matches[:5])
    )
    more = "" if len(matches) <= 5 else f"; ... ({len(matches)} total)"
    raise ValueError(
        f"Ambiguous target '{target}'. Use selector.occurrence or selector.within. "
        f"Candidates: {candidates}{more}"
    )


def legacy_target_migration_error(target):
    """Return a ValueError for retired target spellings, else None.

    Single source of truth for the legacy set so the contract validator in
    ``edit.py`` and the structural resolver here cannot drift.
    """
    stripped = (target or "").strip()
    if stripped == ":entire_body":
        return ValueError(
            "target=':entire_body' is no longer valid. "
            "Use target=':body' with scope='section'."
        )
    if stripped in {":body_preamble", ":body_before_first_heading"}:
        return ValueError(
            f"target='{stripped}' is no longer valid. "
            "Use target=':body' with scope='intro'."
        )
    if stripped.startswith(":section:"):
        resolved = stripped[len(":section:"):].strip()
        if not resolved:
            return ValueError(
                "target=':section:' is no longer valid. "
                "Use a real heading or callout target with scope='section'."
            )
        return ValueError(
            f"target='{stripped}' is no longer valid. "
            f"Use target='{resolved}' with scope='section'."
        )
    return None


def resolve_structural_target(body, target, selector=None):
    """Resolve ``target`` and ``selector`` into a structural node + ranges."""
    stripped = (target or "").strip()
    if not stripped:
        raise ValueError("target is required for structural resolution")
    legacy_error = legacy_target_migration_error(stripped)
    if legacy_error is not None:
        raise legacy_error

    selector = normalize_structural_selector(selector)
    if stripped == ":body" and (selector["within"] or selector["occurrence"] is not None):
        raise ValueError("target=':body' does not support selector.within or selector.occurrence")

    root, nodes = _scan_structural_nodes(body)
    container = root
    selected_within = []

    for step in selector["within"]:
        matches = [
            node for node in _container_descendants(container, nodes)
            if _matches_target(node, step["target"])
        ]
        context = _candidate_label(container) if container["kind"] != "body" else None
        node, occurrence = _select_match(matches, step["target"], step["occurrence"], context)
        selected_within.append({"node": node, "occurrence": occurrence})
        container = node

    if stripped == ":body":
        display = ":body"
        return {
            "kind": "body",
            "raw": ":body",
            "occurrence": 1,
            "within": [],
            "display_path": display,
            "ranges": {
                "section": (0, root["section_end"]),
                "intro": (0, root["intro_end"]),
            },
        }

    matches = [
        node for node in _container_descendants(container, nodes)
        if _matches_target(node, stripped)
    ]
    context = _candidate_label(container) if container["kind"] != "body" else None
    node, occurrence = _select_match(matches, stripped, selector["occurrence"], context)

    path_parts = [_step_display(item["node"]["raw"], item["occurrence"]) for item in selected_within]
    path_parts.append(_step_display(node["raw"], occurrence))
    display = " > ".join(path_parts) if path_parts else node["raw"]

    ranges = {"section": (node["start"], node["section_end"]), "body": (node["body_start"], node["body_end"])}
    if node["kind"] == "heading":
        ranges["heading"] = (node["start"], node["anchor_end"])
        ranges["intro"] = (node["body_start"], node["intro_end"])
    else:
        ranges["header"] = (node["start"], node["anchor_end"])

    return {
        "kind": node["kind"],
        "raw": node["raw"],
        "level": node["level"],
        "occurrence": occurrence,
        "within": [
            {"raw": item["node"]["raw"], "occurrence": item["occurrence"]}
            for item in selected_within
        ],
        "display_path": display,
        "ranges": ranges,
        "next_sibling_raw": node["next_sibling_raw"],
        "next_sibling_level": node["next_sibling_level"],
    }

