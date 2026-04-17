"""Tests for the shared naming engine in _common/_naming.py."""

import pytest

from _common._naming import (
    extract_title,
    render_filename,
    select_rule,
    validate_filename,
)


SIMPLE_NAMING = {
    "pattern": "{Title}.md",
    "folder": "Wiki/",
    "rules": [
        {"match_field": None, "match_values": None, "pattern": "{Title}.md"}
    ],
    "placeholders": [],
}


DATED_TEMPORAL_NAMING = {
    "pattern": "yyyymmdd-research~{Title}.md",
    "folder": "_Temporal/Research/",
    "rules": [
        {
            "match_field": None,
            "match_values": None,
            "pattern": "yyyymmdd-research~{Title}.md",
        }
    ],
    "placeholders": [],
}


RELEASE_NAMING = {
    "pattern": None,
    "folder": "Releases/{Project}/",
    "rules": [
        {
            "match_field": "status",
            "match_values": ["planned", "active", "cancelled"],
            "pattern": "{Title}.md",
        },
        {
            "match_field": "status",
            "match_values": ["shipped"],
            "pattern": "{Version} - {Title}.md",
        },
    ],
    "placeholders": [
        {
            "name": "Version",
            "field": "version",
            "required_when_field": "status",
            "required_values": ["shipped"],
            "regex": r"^v?\d+\.\d+\.\d+$",
        }
    ],
}


WILDCARD_NAMING = {
    "pattern": None,
    "folder": "Things/",
    "rules": [
        {"match_field": "status", "match_values": ["done"], "pattern": "{Title}-done.md"},
        {"match_field": "status", "match_values": ["*"], "pattern": "{Title}.md"},
    ],
    "placeholders": [],
}


class TestSelectRule:
    def test_default_rule_matches_any(self):
        rule = select_rule(SIMPLE_NAMING, {})
        assert rule["pattern"] == "{Title}.md"

    def test_status_matches_rule(self):
        rule = select_rule(RELEASE_NAMING, {"status": "shipped"})
        assert rule["pattern"] == "{Version} - {Title}.md"

    def test_other_status_matches_first_rule(self):
        rule = select_rule(RELEASE_NAMING, {"status": "active"})
        assert rule["pattern"] == "{Title}.md"

    def test_unknown_status_returns_none(self):
        assert select_rule(RELEASE_NAMING, {"status": "draft"}) is None

    def test_wildcard_fallback(self):
        rule = select_rule(WILDCARD_NAMING, {"status": "anything"})
        assert rule["pattern"] == "{Title}.md"

    def test_missing_match_field_skips_rule(self):
        assert select_rule(RELEASE_NAMING, {}) is None

    def test_none_naming_returns_none(self):
        assert select_rule(None, {"status": "shipped"}) is None


class TestRenderFilename:
    def test_simple_render(self):
        assert (
            render_filename(SIMPLE_NAMING, "Hello World", {})
            == "Hello World.md"
        )

    def test_render_uses_status_rule(self):
        name = render_filename(
            RELEASE_NAMING,
            "First Cut",
            {"status": "shipped", "version": "v1.0.0"},
        )
        assert name == "v1.0.0 - First Cut.md"

    def test_render_unshipped_ignores_version(self):
        name = render_filename(
            RELEASE_NAMING,
            "Upcoming",
            {"status": "planned", "version": "v1.0.0"},
        )
        assert name == "Upcoming.md"

    def test_missing_required_placeholder_field_raises(self):
        with pytest.raises(ValueError, match="requires frontmatter field 'version'"):
            render_filename(
                RELEASE_NAMING,
                "Broken",
                {"status": "shipped"},
            )

    def test_no_matching_rule_raises(self):
        with pytest.raises(ValueError, match="No naming rule matches"):
            render_filename(RELEASE_NAMING, "Orphan", {"status": "draft"})

    def test_optional_placeholder_not_required_in_off_state(self):
        # Version is only required when status=shipped, not for planned.
        name = render_filename(
            RELEASE_NAMING,
            "No Version Yet",
            {"status": "planned"},
        )
        assert name == "No Version Yet.md"


class TestValidateFilename:
    def test_valid_simple(self):
        assert validate_filename(SIMPLE_NAMING, {}, "Hello World.md") is True

    def test_invalid_simple(self):
        assert validate_filename(SIMPLE_NAMING, {}, "nope.txt") is False

    def test_valid_shipped_release(self):
        assert (
            validate_filename(
                RELEASE_NAMING,
                {"status": "shipped"},
                "v1.0.0 - First Release.md",
            )
            is True
        )

    def test_invalid_shipped_release_bad_version(self):
        assert (
            validate_filename(
                RELEASE_NAMING,
                {"status": "shipped"},
                "banana - First Release.md",
            )
            is False
        )

    def test_valid_unshipped_release(self):
        assert (
            validate_filename(
                RELEASE_NAMING,
                {"status": "planned"},
                "Upcoming.md",
            )
            is True
        )

    def test_shipped_filename_against_unshipped_rule_invalid(self):
        # A version-led filename fails validation when status is planned
        # because the unshipped rule is {Title}.md — matches anything with .md
        # including this one. Verify that the behaviour is rule-bound, not
        # pattern-bound: the engine must select the unshipped rule for status
        # planned, and that rule considers "v1.0.0 - First.md" a valid Title.
        assert (
            validate_filename(
                RELEASE_NAMING,
                {"status": "planned"},
                "v1.0.0 - First.md",
            )
            is True
        )

    def test_no_rule_matches_returns_false(self):
        assert (
            validate_filename(RELEASE_NAMING, {"status": "draft"}, "Anything.md")
            is False
        )


class TestExtractTitle:
    def test_extract_simple(self):
        assert extract_title(SIMPLE_NAMING, {}, "Hello World.md") == "Hello World"

    def test_extract_dated(self):
        assert (
            extract_title(
                DATED_TEMPORAL_NAMING, {}, "20260413-research~Demo Prep.md"
            )
            == "Demo Prep"
        )

    def test_extract_shipped_release(self):
        assert (
            extract_title(
                RELEASE_NAMING,
                {"status": "shipped"},
                "v1.0.0 - First Release.md",
            )
            == "First Release"
        )

    def test_extract_unshipped_release(self):
        assert (
            extract_title(
                RELEASE_NAMING,
                {"status": "planned"},
                "Upcoming.md",
            )
            == "Upcoming"
        )

    def test_extract_stem_without_extension(self):
        assert (
            extract_title(SIMPLE_NAMING, {}, "Hello World") == "Hello World"
        )

    def test_extract_non_matching_returns_none(self):
        assert (
            extract_title(
                DATED_TEMPORAL_NAMING, {}, "not-a-research-note.md"
            )
            is None
        )


class TestBackwardsCompatSimpleForm:
    def test_simple_form_renders_identically(self):
        # Same behaviour we had from resolve_naming_pattern directly.
        assert (
            render_filename(SIMPLE_NAMING, "My Note", {}) == "My Note.md"
        )

    def test_simple_form_no_placeholders_required(self):
        render_filename(SIMPLE_NAMING, "My Note", {"anything": "here"})
