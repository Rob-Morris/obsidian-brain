"""Schema tests for the authored brain-core session-core.md.

session.py extracts ``## Core Docs`` and ``## Standards`` from this file
during MCP bootstrap and now requires both to resolve unambiguously.
A duplicate or missing heading would raise inside ``brain_session``.
"""

from pathlib import Path

import pytest

from _common._markdown import collect_headings, resolve_structural_target

SESSION_CORE_PATH = Path(__file__).resolve().parents[1] / "src" / "brain-core" / "session-core.md"

REQUIRED_SECTIONS = ("Core Docs", "Standards")


@pytest.fixture(scope="module")
def session_core_body():
    with open(SESSION_CORE_PATH, encoding="utf-8") as f:
        return f.read()


class TestSessionCoreSchema:
    @pytest.mark.parametrize("heading", REQUIRED_SECTIONS)
    def test_required_section_appears_exactly_once(self, session_core_body, heading):
        h2_matches = [
            text
            for _, level, text, _ in collect_headings(session_core_body)
            if level == 2 and text == heading
        ]
        assert h2_matches == [heading], (
            f"session-core.md must contain exactly one '## {heading}' heading "
            f"(found {len(h2_matches)})"
        )

    @pytest.mark.parametrize("heading", REQUIRED_SECTIONS)
    def test_required_section_resolves_via_resolver(self, session_core_body, heading):
        resolved = resolve_structural_target(session_core_body, f"## {heading}")
        assert resolved["kind"] == "heading"
        body_start, body_end = resolved["ranges"]["body"]
        assert body_end > body_start, (
            f"## {heading} resolved but its body range is empty"
        )
