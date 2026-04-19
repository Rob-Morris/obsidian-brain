"""Edge-case fixtures for `markdown_region_ranges` and the underlying
region primitives in `_common/_markdown`.

Cases are drawn from CommonMark-style edge behaviours (code spans, fences,
HTML blocks) plus the Obsidian extensions we care about ($$ math, the
well-scoped raw HTML subset).

Each case is a ``(markdown, expected_kinds, literal_spans)`` triple:

- ``markdown`` is the input body.
- ``expected_kinds`` is the multiset of region kinds we expect, in any order.
- ``literal_spans`` is a list of substrings that MUST be covered by at least
  one returned region (used to assert that an ignore context actually wraps
  the thing it should).
"""

import pytest

from _common import (
    REGION_FENCE,
    REGION_HTML_COMMENT,
    REGION_INLINE_CODE,
    REGION_MATH_BLOCK,
    REGION_RAW_HTML,
    markdown_region_ranges,
)


def _covers(regions, body, needle):
    """True if some returned region fully covers the substring *needle*."""
    pos = body.find(needle)
    if pos < 0:
        return False
    end = pos + len(needle)
    return any(start <= pos and end <= region_end for _, start, region_end in regions)


CASES = [
    # --- Fenced code blocks ---
    pytest.param(
        "Before\n```\n[[in-fence]]\n```\nAfter",
        {REGION_FENCE: 1},
        ["[[in-fence]]"],
        id="fence-backtick",
    ),
    pytest.param(
        "Before\n~~~\n[[in-tilde]]\n~~~\nAfter",
        {REGION_FENCE: 1},
        ["[[in-tilde]]"],
        id="fence-tilde",
    ),
    pytest.param(
        "Before\n```python\n[[in-lang]]\n```\n",
        {REGION_FENCE: 1},
        ["[[in-lang]]"],
        id="fence-with-lang",
    ),
    pytest.param(
        "```\n[[never-closed]]\nto end of file",
        {REGION_FENCE: 1},
        ["[[never-closed]]"],
        id="fence-unterminated",
    ),

    # --- Inline code spans ---
    pytest.param(
        "Use `[[in-single]]` here.",
        {REGION_INLINE_CODE: 1},
        ["[[in-single]]"],
        id="inline-single-backtick",
    ),
    pytest.param(
        "Use ``[[in-double]]`` here.",
        {REGION_INLINE_CODE: 1},
        ["[[in-double]]"],
        id="inline-double-backtick",
    ),
    pytest.param(
        "Use `[[first]]` and `[[second]]`.",
        {REGION_INLINE_CODE: 2},
        ["[[first]]", "[[second]]"],
        id="inline-two-spans",
    ),
    pytest.param(
        "Text with `unterminated",
        {},
        [],
        id="inline-unterminated-no-match",
    ),
    pytest.param(
        "```\n`[[not-inline]]`\n```",
        {REGION_FENCE: 1},
        ["[[not-inline]]"],
        id="inline-inside-fence-deferred-to-fence",
    ),

    # --- HTML comments ---
    pytest.param(
        "Before <!-- [[in-comment]] --> after.",
        {REGION_HTML_COMMENT: 1},
        ["[[in-comment]]"],
        id="html-comment-inline",
    ),
    pytest.param(
        "<!--\n[[multi-line-comment]]\n-->",
        {REGION_HTML_COMMENT: 1},
        ["[[multi-line-comment]]"],
        id="html-comment-multiline",
    ),
    pytest.param(
        "<!-- a --> x <!-- b -->",
        {REGION_HTML_COMMENT: 2},
        [],
        id="html-comment-two-per-line",
    ),

    # --- Math blocks ---
    pytest.param(
        "$$[[in-math-inline]]$$",
        {REGION_MATH_BLOCK: 1},
        ["[[in-math-inline]]"],
        id="math-inline-on-one-line",
    ),
    pytest.param(
        "$$\n[[in-math-block]]\n$$",
        {REGION_MATH_BLOCK: 1},
        ["[[in-math-block]]"],
        id="math-block-multiline",
    ),
    pytest.param(
        "```\n$$ [[not-math]] $$\n```",
        {REGION_FENCE: 1},
        ["[[not-math]]"],
        id="math-markers-inside-fence-deferred",
    ),
    pytest.param(
        "Inline: `$$ [[not-math]] $$` here.",
        {REGION_INLINE_CODE: 1},
        ["[[not-math]]"],
        id="math-markers-inside-inline-code-deferred",
    ),

    # --- Raw HTML blocks ---
    pytest.param(
        "<pre>\n[[in-pre]]\n</pre>",
        {REGION_RAW_HTML: 1},
        ["[[in-pre]]"],
        id="raw-html-pre",
    ),
    pytest.param(
        "<script>\n[[in-script]]\n</script>",
        {REGION_RAW_HTML: 1},
        ["[[in-script]]"],
        id="raw-html-script",
    ),
    pytest.param(
        "<style>\n[[in-style]]\n</style>",
        {REGION_RAW_HTML: 1},
        ["[[in-style]]"],
        id="raw-html-style",
    ),
    pytest.param(
        '<PRE class="x">\n[[in-uppercase]]\n</PRE>',
        {REGION_RAW_HTML: 1},
        ["[[in-uppercase]]"],
        id="raw-html-case-and-attrs",
    ),

    # --- Mixed ---
    pytest.param(
        "Live: [[real-link]]. In code: `[[masked]]`. In comment: <!-- [[also]] -->.",
        {REGION_INLINE_CODE: 1, REGION_HTML_COMMENT: 1},
        ["[[masked]]", "[[also]]"],
        id="mixed-live-and-masked",
    ),
]


@pytest.mark.parametrize("body, expected_kinds, literal_spans", CASES)
def test_markdown_region_ranges(body, expected_kinds, literal_spans):
    regions = markdown_region_ranges(body)

    counts = {}
    for kind, _, _ in regions:
        counts[kind] = counts.get(kind, 0) + 1
    assert counts == expected_kinds, (
        f"Expected region kinds {expected_kinds}, got {counts}"
    )

    for needle in literal_spans:
        assert _covers(regions, body, needle), (
            f"No region covered literal span {needle!r}"
        )


def test_live_wikilink_not_covered():
    body = "Live: [[real-link]]. In code: `[[masked]]`."
    regions = markdown_region_ranges(body)
    assert not _covers(regions, body, "[[real-link]]")
    assert _covers(regions, body, "[[masked]]")


def test_region_kinds_are_strings():
    regions = markdown_region_ranges("`code` and [[foo]]")
    for kind, start, end in regions:
        assert isinstance(kind, str)
        assert isinstance(start, int)
        assert isinstance(end, int)
        assert start < end
