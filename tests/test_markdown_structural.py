"""Tests for structural scanners (headings, sections, preamble, callouts).

These scanners now treat all five literal-text regions (fenced code, raw HTML,
HTML comments, ``$$`` math, inline code) as contexts where heading-shaped or
callout-shaped lines do NOT count as structural landmarks.
"""

import pytest

from _common._markdown import (
    collect_headings,
    find_body_preamble,
    find_section,
)


class TestCollectHeadings:
    def test_headings_outside_literal_regions(self):
        body = "# Alpha\n## Beta\n### Gamma\n"
        headings = collect_headings(body)
        levels = [h[1] for h in headings]
        texts = [h[2] for h in headings]
        assert levels == [1, 2, 3]
        assert texts == ["Alpha", "Beta", "Gamma"]

    def test_heading_inside_fence_ignored(self):
        body = "# Real\n```\n# Not-a-heading\n```\n"
        headings = collect_headings(body)
        assert [h[2] for h in headings] == ["Real"]

    def test_heading_inside_html_comment_ignored(self):
        body = "# Real\n<!-- # Not-a-heading -->\n"
        headings = collect_headings(body)
        assert [h[2] for h in headings] == ["Real"]

    def test_heading_inside_raw_html_ignored(self):
        body = "# Real\n<pre>\n# Not-a-heading\n</pre>\n"
        headings = collect_headings(body)
        assert [h[2] for h in headings] == ["Real"]

    def test_heading_inside_math_block_ignored(self):
        body = "# Real\n$$\n# Not-a-heading\n$$\n"
        headings = collect_headings(body)
        assert [h[2] for h in headings] == ["Real"]


class TestFindSection:
    def test_finds_real_heading_when_comment_has_fake_one(self):
        """A heading-shaped line inside an HTML comment shouldn't shadow the real heading."""
        body = "<!-- ## Target -->\nstart\n## Target\nreal body\n"
        start, end = find_section(body, "## Target")
        assert body[start:end] == "real body\n"

    def test_raises_when_only_match_is_in_comment(self):
        body = "<!-- ## Target -->\nsome body\n"
        with pytest.raises(ValueError, match="not found"):
            find_section(body, "## Target")

    def test_raises_when_only_match_is_in_fence(self):
        body = "```\n## Target\n```\n"
        with pytest.raises(ValueError, match="not found"):
            find_section(body, "## Target")

    def test_raises_when_only_match_is_in_raw_html(self):
        body = "<pre>\n## Target\n</pre>\n"
        with pytest.raises(ValueError, match="not found"):
            find_section(body, "## Target")


class TestFindBodyPreamble:
    def test_preamble_stops_at_first_real_heading(self):
        body = "intro\nparagraph\n# Alpha\nafter\n"
        start, end = find_body_preamble(body)
        assert body[start:end] == "intro\nparagraph\n"

    def test_comment_heading_does_not_end_preamble(self):
        body = "intro\n<!-- # not-real -->\nmore intro\n# Real\ntail\n"
        start, end = find_body_preamble(body)
        assert body[start:end] == "intro\n<!-- # not-real -->\nmore intro\n"

    def test_fence_heading_does_not_end_preamble(self):
        body = "intro\n```\n# not-real\n```\nmore intro\n# Real\n"
        start, end = find_body_preamble(body)
        assert body[start:end] == "intro\n```\n# not-real\n```\nmore intro\n"

    def test_raw_html_heading_does_not_end_preamble(self):
        body = "intro\n<pre>\n# not-real\n</pre>\nmore intro\n# Real\n"
        start, end = find_body_preamble(body)
        assert body[start:end] == "intro\n<pre>\n# not-real\n</pre>\nmore intro\n"

    def test_no_headings_returns_full_body(self):
        body = "just paragraphs\nno headings here\n"
        start, end = find_body_preamble(body)
        assert body[start:end] == body

    def test_empty_preamble_when_heading_first(self):
        body = "# Right Away\ncontent\n"
        start, end = find_body_preamble(body)
        assert start == 0
        assert end == 0
