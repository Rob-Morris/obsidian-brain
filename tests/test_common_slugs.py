"""Tests for _common._slugs — slug generation and filename conversion."""

import _common as common


# ---------------------------------------------------------------------------
# title_to_slug
# ---------------------------------------------------------------------------

class TestTitleToSlug:
    def test_basic_title(self):
        assert common.title_to_slug("My Great Idea") == "my-great-idea"

    def test_special_characters(self):
        assert common.title_to_slug("Hello, World! (2026)") == "hello-world-2026"

    def test_unicode(self):
        assert common.title_to_slug("Café Résumé") == "cafe-resume"

    def test_multiple_spaces(self):
        assert common.title_to_slug("  lots   of   spaces  ") == "lots-of-spaces"

    def test_hyphens_and_underscores(self):
        assert common.title_to_slug("already-has-hyphens_and_underscores") == "already-has-hyphens-and-underscores"

    def test_all_special(self):
        assert common.title_to_slug("!!!") == ""

    def test_single_word(self):
        assert common.title_to_slug("Python") == "python"

    def test_numbers(self):
        assert common.title_to_slug("3 Ways to Code") == "3-ways-to-code"


# ---------------------------------------------------------------------------
# title_to_filename
# ---------------------------------------------------------------------------

class TestTitleToFilename:
    def test_basic_title(self):
        assert common.title_to_filename("My Project") == "My Project"

    def test_preserves_caps(self):
        assert common.title_to_filename("API Refactor") == "API Refactor"

    def test_strips_unsafe_chars(self):
        assert common.title_to_filename('Rob\'s Q3/Q4 Review') == "Rob's Q3Q4 Review"

    def test_strips_all_unsafe(self):
        assert common.title_to_filename('a/b\\c:d*e?f"g<h>i|j') == "abcdefghij"

    def test_preserves_unicode(self):
        assert common.title_to_filename("Café Notes") == "Café Notes"

    def test_trims_whitespace(self):
        assert common.title_to_filename("  My Project  ") == "My Project"

    def test_collapses_spaces(self):
        assert common.title_to_filename("lots   of   spaces") == "lots of spaces"

    def test_collapses_spaces_from_stripped_chars(self):
        # Stripping / leaves double space which gets collapsed
        assert common.title_to_filename("Q3 / Q4 Review") == "Q3 Q4 Review"

    def test_empty_title(self):
        assert common.title_to_filename("") == ""

    def test_all_unsafe(self):
        assert common.title_to_filename('/:*?"<>|') == ""

    def test_hyphens_preserved(self):
        assert common.title_to_filename("brain-core") == "brain-core"

    def test_underscores_preserved(self):
        assert common.title_to_filename("my_project") == "my_project"

    def test_numbers(self):
        assert common.title_to_filename("3 Ways to Code") == "3 Ways to Code"

    def test_parentheses_preserved(self):
        assert common.title_to_filename("Hello (World)") == "Hello (World)"
