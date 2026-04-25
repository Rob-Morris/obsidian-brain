"""Tests for structural scanners (headings, sections, preamble, callouts).

These scanners now treat all five literal-text regions (fenced code, raw HTML,
HTML comments, ``$$`` math, inline code) as contexts where heading-shaped or
callout-shaped lines do NOT count as structural landmarks.
"""

import pytest

from _common._markdown import (
    collect_headings,
    find_section,
    resolve_structural_target,
)


def _body_intro(body):
    """Convenience wrapper used by intro-range tests."""
    return resolve_structural_target(body, ":body")["ranges"]["intro"]


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


class TestBodyIntroRange:
    def test_preamble_stops_at_first_real_heading(self):
        body = "intro\nparagraph\n# Alpha\nafter\n"
        start, end = _body_intro(body)
        assert body[start:end] == "intro\nparagraph\n"

    def test_comment_heading_does_not_end_preamble(self):
        body = "intro\n<!-- # not-real -->\nmore intro\n# Real\ntail\n"
        start, end = _body_intro(body)
        assert body[start:end] == "intro\n<!-- # not-real -->\nmore intro\n"

    def test_fence_heading_does_not_end_preamble(self):
        body = "intro\n```\n# not-real\n```\nmore intro\n# Real\n"
        start, end = _body_intro(body)
        assert body[start:end] == "intro\n```\n# not-real\n```\nmore intro\n"

    def test_raw_html_heading_does_not_end_preamble(self):
        body = "intro\n<pre>\n# not-real\n</pre>\nmore intro\n# Real\n"
        start, end = _body_intro(body)
        assert body[start:end] == "intro\n<pre>\n# not-real\n</pre>\nmore intro\n"

    def test_no_headings_returns_full_body(self):
        body = "just paragraphs\nno headings here\n"
        start, end = _body_intro(body)
        assert body[start:end] == body

    def test_empty_preamble_when_heading_first(self):
        body = "# Right Away\ncontent\n"
        start, end = _body_intro(body)
        assert start == 0
        assert end == 0

    def test_callout_does_not_end_preamble(self):
        body = (
            "intro\n"
            "> [!note] Status\n"
            "> Content.\n"
            "# Real\n"
            "tail\n"
        )
        start, end = _body_intro(body)
        assert body[start:end] == "intro\n> [!note] Status\n> Content.\n"


class TestResolveStructuralTarget:
    def test_resolves_body_section_and_intro(self):
        body = "Intro.\n\n> [!note] Status\n> Fine.\n\n## Notes\n\nMore.\n"
        resolved = resolve_structural_target(body, ":body")
        assert resolved["kind"] == "body"
        assert body[slice(*resolved["ranges"]["section"])] == body
        assert body[slice(*resolved["ranges"]["intro"])] == "Intro.\n\n> [!note] Status\n> Fine.\n\n"

    def test_resolves_heading_ranges(self):
        body = "## Alpha\n\nIntro.\n\n### Child\n\nChild.\n\n## Beta\n"
        resolved = resolve_structural_target(body, "## Alpha")
        assert resolved["kind"] == "heading"
        assert body[slice(*resolved["ranges"]["heading"])] == "## Alpha\n"
        assert body[slice(*resolved["ranges"]["intro"])] == "\nIntro.\n\n"
        assert "### Child" in body[slice(*resolved["ranges"]["body"])]

    def test_resolves_callout_ranges(self):
        body = "## Alpha\n\n> [!note] Status\n> One.\n>\n> Two.\n\nTail.\n"
        resolved = resolve_structural_target(body, "[!note] Status")
        assert resolved["kind"] == "callout"
        assert body[slice(*resolved["ranges"]["header"])] == "> [!note] Status\n"
        assert body[slice(*resolved["ranges"]["body"])] == "> One.\n>\n> Two.\n"

    def test_callout_match_is_exact_not_prefix(self):
        body = "> [!note] Status detail\n> One.\n"
        with pytest.raises(ValueError, match="not found"):
            resolve_structural_target(body, "[!note] Status")

    def test_selector_within_and_occurrence_disambiguate_heading(self):
        body = (
            "# API\n\n"
            "## Notes\n\nFirst.\n\n"
            "# API\n\n"
            "## Notes\n\nSecond.\n"
        )
        resolved = resolve_structural_target(
            body,
            "## Notes",
            selector={"within": [{"target": "# API", "occurrence": 2}]},
        )
        assert resolved["display_path"] == "# API [2] > ## Notes"
        assert "Second." in body[slice(*resolved["ranges"]["body"])]

    def test_selector_occurrence_disambiguates_callout(self):
        body = (
            "# API\n\n"
            "> [!note] Status\n> One.\n\n"
            "> [!note] Status\n> Two.\n"
        )
        resolved = resolve_structural_target(
            body,
            "[!note] Status",
            selector={"occurrence": 2},
        )
        assert resolved["occurrence"] == 2
        assert "Two." in body[slice(*resolved["ranges"]["body"])]

    def test_selector_occurrence_rejects_bool(self):
        body = "# API\n\n## Notes\n\nOne.\n"
        with pytest.raises(ValueError, match="selector.occurrence must be a positive integer"):
            resolve_structural_target(body, "## Notes", selector={"occurrence": True})

    def test_selector_within_occurrence_rejects_bool(self):
        body = "# API\n\n## Notes\n\nOne.\n"
        with pytest.raises(ValueError, match="selector.within\\[0\\]\\.occurrence must be a positive integer"):
            resolve_structural_target(
                body,
                "## Notes",
                selector={"within": [{"target": "# API", "occurrence": True}]},
            )

    def test_ambiguity_errors_with_candidates(self):
        body = "# API\n\n## Notes\n\nOne.\n\n## Notes\n\nTwo.\n"
        with pytest.raises(ValueError, match="Ambiguous target '## Notes'"):
            resolve_structural_target(body, "## Notes")

    def test_selector_within_rejects_body(self):
        body = "# API\n\n## Notes\n\nOne.\n"
        with pytest.raises(ValueError, match="cannot use ':body'"):
            resolve_structural_target(
                body,
                "## Notes",
                selector={"within": [{"target": ":body"}]},
            )
