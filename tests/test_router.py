"""Unit tests for _common._router helpers."""

import os
import sys

SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "src", "brain-core", "scripts")
sys.path.insert(0, os.path.abspath(SCRIPTS_DIR))

from _common._router import extract_title_from_naming_pattern


class TestExtractTitleFromNamingPattern:
    def test_extracts_title_from_dated_pattern(self):
        result = extract_title_from_naming_pattern(
            "yyyymmdd-research~{Title}.md",
            "20260413-research~Demo Prep Synthesis",
        )
        assert result == "Demo Prep Synthesis"

    def test_extracts_title_from_slug_pattern(self):
        result = extract_title_from_naming_pattern(
            "{slug}.md",
            "my-living-artefact",
        )
        assert result == "my-living-artefact"

    def test_returns_none_when_pattern_is_none(self):
        assert extract_title_from_naming_pattern(None, "anything") is None

    def test_returns_none_when_pattern_does_not_match(self):
        result = extract_title_from_naming_pattern(
            "yyyymmdd-research~{Title}.md",
            "not-a-dated-file",
        )
        assert result is None

    def test_handles_stem_without_md_suffix(self):
        result = extract_title_from_naming_pattern(
            "yyyymmdd-report~{Title}.md",
            "20260413-report~My Report",
        )
        assert result == "My Report"

    def test_title_with_tildes_and_spaces(self):
        result = extract_title_from_naming_pattern(
            "yyyymmdd-research~{Title}.md",
            "20260413-research~A Title With ~ Tildes",
        )
        assert result == "A Title With ~ Tildes"
