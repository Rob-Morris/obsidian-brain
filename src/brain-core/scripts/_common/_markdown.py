"""Markdown section parsing — headings, fenced code blocks, callouts."""

import re

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)[^\S\n]*$", re.MULTILINE)
_FENCE_RE = re.compile(r"^(`{3,}|~{3,})", re.MULTILINE)


def collect_headings(body):
    """Collect all markdown headings outside fenced code blocks.

    Returns list of (position, level, text, raw) tuples where:
    - position: character offset of the heading line start
    - level: heading level (1-6)
    - text: heading text (stripped, original case)
    - raw: full heading line (e.g. "## Alpha")
    """
    fenced = fenced_ranges(body)
    headings = []
    for m in _HEADING_RE.finditer(body):
        if any(fs <= m.start() < fe for fs, fe in fenced):
            continue
        headings.append((
            m.start(),
            len(m.group(1)),
            m.group(2).strip(),
            m.group(0).strip(),
        ))
    return headings


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
    Headings inside fenced code blocks are ignored.

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

    fenced = fenced_ranges(body)
    headings = []
    for m in _HEADING_RE.finditer(body):
        if any(fs <= m.start() < fe for fs, fe in fenced):
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
    Callouts inside fenced code blocks are ignored.

    When include_heading=True, start points to the callout title line itself.
    """
    target_lower = target.lower()
    fenced = fenced_ranges(body)
    lines = body.split("\n")
    pos = 0

    for i, line in enumerate(lines):
        if any(fs <= pos < fe for fs, fe in fenced):
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
