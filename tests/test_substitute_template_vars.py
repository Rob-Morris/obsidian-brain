"""Tests for substitute_template_vars() in _common._templates."""

from datetime import datetime, timezone, timedelta

from _common import substitute_template_vars


FIXED = datetime(2026, 4, 4, 14, 30, 0, tzinfo=timezone(timedelta(hours=11)))


class TestDatePlaceholders:
    def test_yyyy_mm_dd(self):
        assert substitute_template_vars("## {{date:YYYY-MM-DD}}", _now=FIXED) == "## 2026-04-04"

    def test_yyyymmdd(self):
        assert substitute_template_vars("{{date:YYYYMMDD}}-log", _now=FIXED) == "20260404-log"

    def test_yyyy_mm_dd_ddd(self):
        result = substitute_template_vars("{{date:YYYY-MM-DD ddd}}", _now=FIXED)
        assert result == "2026-04-04 Sat"

    def test_multiple_date_placeholders(self):
        content = "Created {{date:YYYY-MM-DD}}, ref {{date:YYYYMMDD}}"
        result = substitute_template_vars(content, _now=FIXED)
        assert result == "Created 2026-04-04, ref 20260404"

    def test_no_date_placeholder_unchanged(self):
        assert substitute_template_vars("No dates here") == "No dates here"


class TestCustomVars:
    def test_simple_replacement(self):
        result = substitute_template_vars(
            "Hello SOURCE_NAME",
            {"SOURCE_NAME": "World"},
        )
        assert result == "Hello World"

    def test_longest_key_first(self):
        """Combined key must be replaced before individual parts."""
        result = substitute_template_vars(
            "[[SOURCE_DOC_PATH|SOURCE_DOC_TITLE]] and SOURCE_DOC_PATH alone",
            {
                "SOURCE_DOC_PATH|SOURCE_DOC_TITLE": "Designs/My Design|My Design",
                "SOURCE_DOC_PATH": "Designs/My Design",
                "SOURCE_DOC_TITLE": "My Design",
            },
        )
        assert result == "[[Designs/My Design|My Design]] and Designs/My Design alone"

    def test_no_vars_is_noop(self):
        assert substitute_template_vars("unchanged", None) == "unchanged"
        assert substitute_template_vars("unchanged", {}) == "unchanged"


class TestCombined:
    def test_date_and_custom_vars(self):
        content = "## {{date:YYYY-MM-DD}}\n\nTranscript for SOURCE_TITLE."
        result = substitute_template_vars(
            content,
            {"SOURCE_TITLE": "My Design"},
            _now=FIXED,
        )
        assert result == "## 2026-04-04\n\nTranscript for My Design."


class TestAgentInstructions:
    def test_single_line_stripped(self):
        content = "before\n{{agent: do the thing}}\nafter"
        assert substitute_template_vars(content) == "before\n\nafter"

    def test_multiline_stripped(self):
        content = "head\n\n{{agent: multi\nline\ninstruction}}\n\ntail"
        assert substitute_template_vars(content) == "head\n\ntail"

    def test_multiple_instructions(self):
        content = "{{agent: one}}\n\n## Section\n\n{{agent: two}}\n\nbody"
        assert substitute_template_vars(content) == "\n\n## Section\n\nbody"

    def test_inline_instruction(self):
        content = "Prefix {{agent: inline hint}} suffix"
        assert substitute_template_vars(content) == "Prefix  suffix"

    def test_excess_blank_lines_collapsed(self):
        content = "a\n\n\n\n{{agent: x}}\n\n\n\nb"
        assert substitute_template_vars(content) == "a\n\nb"

    def test_no_agent_token_is_noop(self):
        assert substitute_template_vars("no tokens here") == "no tokens here"

    def test_empty_instruction(self):
        assert substitute_template_vars("a {{agent:}} b") == "a  b"

    def test_runs_after_date_and_custom_vars(self):
        content = "{{date:YYYY-MM-DD}}\n\n{{agent: set SLUG to foo}}\n\nbody"
        result = substitute_template_vars(content, _now=FIXED)
        assert result == "2026-04-04\n\nbody"


class TestEdgeCases:
    def test_empty_string(self):
        assert substitute_template_vars("") == ""

    def test_none(self):
        assert substitute_template_vars(None) is None
