"""Markdown section parsing — headings, fenced code blocks, callouts."""

import re

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
    """Flat ``(start, end)`` skip list covering every markdown literal region.

    Frontmatter is intentionally **not** skipped: wikilinks in YAML properties
    (e.g. ``parent: "[[foo]]"``) are real links, matching Obsidian's
    property-as-link model.
    """
    return [(s, e) for _, s, e in markdown_region_ranges(body)]


def in_any_range(pos, ranges):
    """True when *pos* falls within any ``(start, end)`` in *ranges* (end-exclusive)."""
    return any(s <= pos < e for s, e in ranges)


def collect_headings(body):
    """Collect all markdown headings outside literal regions.

    Returns list of (position, level, text, raw) tuples where:
    - position: character offset of the heading line start
    - level: heading level (1-6)
    - text: heading text (stripped, original case)
    - raw: full heading line (e.g. "## Alpha")

    Headings inside fenced code, raw HTML blocks, HTML comments, ``$$`` math
    blocks, and inline code spans are ignored.
    """
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
    """Parse a heading or callout-title line from the start of ``text``.

    Returns ``None`` when the first non-empty line is ordinary content.
    Otherwise returns a dict with:
    - kind: ``"heading"`` or ``"callout"``
    - raw: the exact structural line (trimmed)
    - level: heading level for headings, else ``None``
    """
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
    """Return list of (start, end) ranges for inline code spans.

    Pairs backtick runs of equal length outside fenced code blocks. Ranges
    cover the full span including the delimiters. *skip* may carry
    pre-computed fenced ranges to avoid recomputation.
    """
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
    """Return list of (start, end) ranges for ``$$...$$`` math blocks.

    Pairs ``$$`` markers outside fenced code blocks and inline code spans.
    Ranges cover the full span including the delimiters. *skip* may carry
    pre-computed fenced+inline-code ranges to avoid recomputation.
    """
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
    """Return list of (start, end) ranges for raw HTML blocks.

    Matches ``<pre>``, ``<script>``, and ``<style>`` elements (case-insensitive)
    and everything up to their matching close tag.
    """
    return [(m.start(), m.end()) for m in _RAW_HTML_BLOCK_RE.finditer(body)]


# Region kinds — tags on each range returned by `markdown_region_ranges`.
# Callers that care about *which* context a range belongs to can match on kind;
# callers that just need a flat skip list can discard it.
REGION_FENCE = "fence"
REGION_RAW_HTML = "raw_html"
REGION_MATH_BLOCK = "math_block"
REGION_INLINE_CODE = "inline_code"
REGION_HTML_COMMENT = "html_comment"


def markdown_region_ranges(body):
    """Return typed ``(kind, start, end)`` regions for markdown contexts
    where inline syntax should be treated as literal text.

    Walks block-level constructs first (fences, raw HTML blocks, ``$$`` math
    blocks), then inline constructs (backtick code spans, HTML comments) in
    a second pass that respects the block ranges. Each returned tuple is
    ``(kind, start, end)``; the start/end are character offsets into *body*.
    """
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


def find_section(body, heading, include_heading=False):
    """Find start/end of a markdown section by heading or callout title.

    Returns (start, end) character offsets into body, where:
    - start is the position after the heading/callout title line (including its newline)
    - end is the position before the next heading of same or higher level (or EOF);
      for callouts, end is the last contiguous blockquote line

    When include_heading=True, start points to the heading/callout line itself
    (i.e. the position of the '#' or '>' character). Useful for inserting content
    before a section.

    Matching is case-insensitive on the text.
    If heading includes # markers (e.g. "## Notes"), matches on level AND text.
    If heading starts with [! (e.g. "[!note] Status"), matches a callout title.
    If heading is plain text (e.g. "Notes"), matches on text at any level.
    Sub-headings are part of the parent section (lower-level headings don't end it).
    Headings inside literal regions (fenced code, raw HTML, HTML comments,
    math blocks, inline code) are ignored.

    Raises ValueError if heading/callout not found.
    """
    stripped = heading.strip()

    # Callout matching: [!type] title
    if stripped.startswith("[!"):
        return _find_callout_section(body, stripped, include_heading=include_heading)

    if stripped.startswith("#"):
        markers = stripped.split()[0]
        target_level = len(markers)
        target_text = stripped[len(markers):].strip().lower()
    else:
        target_level = None
        target_text = stripped.lower()

    skip = literal_ranges(body)
    headings = []
    for m in _HEADING_RE.finditer(body):
        if in_any_range(m.start(), skip):
            continue
        headings.append((m, len(m.group(1)), m.group(2).strip().lower()))

    for idx, (m, level, text) in enumerate(headings):
        if text != target_text:
            continue
        if target_level is not None and level != target_level:
            continue

        if include_heading:
            start = m.start()
        else:
            start = m.end()
            if start < len(body) and body[start] == "\n":
                start += 1

        end = len(body)
        for m2, level2, _ in headings[idx + 1:]:
            if level2 <= level:
                end = m2.start()
                break

        return start, end

    raise ValueError(f"Section '{heading}' not found")


def _find_callout_section(body, target, include_heading=False):
    """Find start/end of an Obsidian callout by its [!type] title.

    The section includes all contiguous blockquote lines after the title.
    A non-blockquote line (including blank) ends the section.
    Callouts inside literal regions (fenced code, raw HTML, HTML comments,
    math blocks, inline code) are ignored.

    When include_heading=True, start points to the callout title line itself.
    """
    target_lower = target.lower()
    skip = literal_ranges(body)
    lines = body.split("\n")
    pos = 0

    for i, line in enumerate(lines):
        if in_any_range(pos, skip):
            pos += len(line) + 1
            continue
        after_gt = line.lstrip()[1:].lstrip() if line.lstrip().startswith(">") else None
        if after_gt is not None and after_gt.lower().startswith(target_lower):
            content_start = pos + len(line) + 1
            if content_start > len(body):
                content_start = len(body)

            start = pos if include_heading else content_start
            end = content_start
            for j in range(i + 1, len(lines)):
                next_line = lines[j]
                if next_line.lstrip().startswith(">"):
                    end += len(next_line) + 1
                else:
                    break

            if end > len(body):
                end = len(body)
            return start, end

        pos += len(line) + 1

    raise ValueError(f"Section '{target}' not found")


def find_body_preamble(body):
    """Return the leading body range before the first targetable section.

    Returns ``(start, end)`` offsets into ``body``. The range is:
    - the full body when the document has no headings or callout sections
    - empty when the document starts with a heading or callout section
    - otherwise everything before the first targetable section, ignoring
      heading-shaped lines inside literal regions (fenced code, raw HTML,
      HTML comments, math blocks, inline code)
    """
    skip = literal_ranges(body)
    lines = body.split("\n")
    pos = 0

    for line in lines:
        if in_any_range(pos, skip):
            pos += len(line) + 1
            continue
        if _HEADING_RE.match(line) or _CALLOUT_TITLE_RE.match(line):
            return 0, pos
        pos += len(line) + 1

    return 0, len(body)
