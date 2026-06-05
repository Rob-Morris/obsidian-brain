"""Tests for _common._slugs — slug generation and filename conversion."""

import pytest

import _common as common
from _common import _slugs


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
# derive_distinctive_slug
# ---------------------------------------------------------------------------

class TestDeriveDistinctiveSlug:
    def test_generate_slug_suffix_always_contains_a_letter(self):
        for _ in range(2000):
            suffix = common.generate_slug_suffix()

            assert len(suffix) == _slugs.SLUG_SUFFIX_LENGTH
            assert all(char in _slugs.SLUG_ALPHABET for char in suffix)
            assert any(char.isalpha() for char in suffix)

    def test_prefers_free_two_word_key(self):
        assert common.derive_distinctive_slug("Pistols at Dawn", set()) == "pistols-dawn"

    def test_uses_single_keyword_when_pair_taken(self):
        assert (
            common.derive_distinctive_slug("Pistols at Dawn", {"pistols-dawn"})
            == "pistols"
        )

    def test_numeric_title_always_yields_valid_key(self):
        for _ in range(1000):
            assert common.is_valid_key(common.derive_distinctive_slug("1984", set()))

    def test_sentinel_title_uses_husk_fallback(self, monkeypatch):
        monkeypatch.setattr(_slugs, "generate_slug_suffix", lambda: "abc")

        assert common.derive_distinctive_slug("!!!", {"husk"}) == "husk-abc"

    def test_raises_when_suffix_candidates_exhausted(self, monkeypatch):
        monkeypatch.setattr(_slugs, "SLUG_SUFFIX_MAX_RETRIES", 2)
        monkeypatch.setattr(_slugs, "generate_slug_suffix", lambda: "abc")

        with pytest.raises(RuntimeError, match="could not find a free suffix"):
            common.derive_distinctive_slug("1984", {"1984-abc"})


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
