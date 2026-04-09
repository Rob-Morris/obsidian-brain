"""Tests for start_shaping.py — shaping session bootstrap."""

import os
from datetime import datetime, timezone

import pytest

import start_shaping
from _common import parse_frontmatter


# ---------------------------------------------------------------------------
# Vault fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def vault(tmp_path):
    """Create a vault with configured types, templates, and sample artefacts."""
    # .brain-core/VERSION
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("0.19.0\n")

    # _Config
    config = tmp_path / "_Config"
    config.mkdir()
    (config / "router.md").write_text("Prefer MCP tools.\n")

    # Living type: Designs (has status with shaping)
    designs_dir = tmp_path / "Designs"
    designs_dir.mkdir()
    (designs_dir / "My Design.md").write_text(
        "---\ntype: living/designs\ntags:\n  - design\nstatus: new\n"
        "created: 2026-03-01T10:00:00+00:00\nmodified: 2026-03-01T10:00:00+00:00\n---\n\n"
        "# My Design\n\nA design for something.\n"
    )
    (designs_dir / "Already Shaping.md").write_text(
        "---\ntype: living/designs\ntags:\n  - design\nstatus: shaping\n"
        "created: 2026-03-01T10:00:00+00:00\nmodified: 2026-03-01T10:00:00+00:00\n---\n\n"
        "# Already Shaping\n\nThis is already being shaped.\n"
    )

    # Living type: Wiki (no status enum → no shaping status)
    wiki_dir = tmp_path / "Wiki"
    wiki_dir.mkdir()
    (wiki_dir / "Brain Overview.md").write_text(
        "---\ntype: living/wiki\ntags:\n  - overview\n---\n\n"
        "# Brain Overview\n\nOverview content.\n"
    )

    # Temporal type: Shaping Transcripts
    temporal = tmp_path / "_Temporal"
    temporal.mkdir()
    (temporal / "Shaping Transcripts").mkdir()

    # Taxonomy: Designs (with shaping in status enum)
    tax_living = config / "Taxonomy" / "Living"
    tax_living.mkdir(parents=True)
    (tax_living / "designs.md").write_text(
        "# Designs\n\n"
        "## Lifecycle\n\n"
        "| `new` | Newly created |\n"
        "| `shaping` | Being shaped |\n"
        "| `ready` | Ready to implement |\n"
        "| `implemented` | Implemented |\n\n"
        "## Naming\n\n`{Title}.md` in `Designs/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: living/designs\ntags:\n  - design\n"
        "status: new  # new | shaping | ready | implemented\n---\n```\n\n"
        "## Template\n\n[[_Config/Templates/Living/Designs]]\n"
    )

    # Taxonomy: Wiki (no status enum)
    (tax_living / "wiki.md").write_text(
        "# Wiki\n\n"
        "## Naming\n\n`{Title}.md` in `Wiki/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: living/wiki\ntags:\n  - topic-tag\n---\n```\n\n"
        "## Template\n\n[[_Config/Templates/Living/Wiki]]\n"
    )

    # Taxonomy: Shaping Transcripts
    tax_temporal = config / "Taxonomy" / "Temporal"
    tax_temporal.mkdir(parents=True)
    (tax_temporal / "shaping-transcripts.md").write_text(
        "# Shaping Transcripts\n\n"
        "## Naming\n\n`yyyymmdd-shaping-transcript~{Title}.md` in "
        "`_Temporal/Shaping Transcripts/yyyy-mm/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: temporal/shaping-transcript\ntags:\n"
        "  - transcript\n---\n```\n\n"
        "## Template\n\n[[_Config/Templates/Temporal/Shaping Transcripts]]\n"
    )

    # Templates
    templates_living = config / "Templates" / "Living"
    templates_living.mkdir(parents=True)
    (templates_living / "Designs.md").write_text(
        "---\ntype: living/designs\ntags: []\nstatus: new\n---\n\n# {{title}}\n\n"
    )
    (templates_living / "Wiki.md").write_text(
        "---\ntype: living/wiki\ntags: []\n---\n\n# {{title}}\n\n"
    )

    templates_temporal = config / "Templates" / "Temporal"
    templates_temporal.mkdir(parents=True)
    (templates_temporal / "Shaping Transcripts.md").write_text(
        "---\ntype: temporal/shaping-transcript\ntags:\n  - transcript\n  - SOURCE_TYPE\n---\n"
        "**Source:** [[SOURCE_DOC_PATH|SOURCE_DOC_TITLE]]"
    )

    return tmp_path


@pytest.fixture
def router(vault):
    """Compile the router for the vault fixture."""
    import compile_router
    return compile_router.compile(str(vault))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestStartShaping:

    def test_missing_target_returns_error(self, vault, router):
        result = start_shaping.start_shaping(str(vault), router, {})
        assert "error" in result
        assert "target" in result["error"]

    def test_none_params_returns_error(self, vault, router):
        result = start_shaping.start_shaping(str(vault), router, None)
        assert "error" in result

    def test_empty_target_returns_error(self, vault, router):
        result = start_shaping.start_shaping(str(vault), router, {"target": "  "})
        assert "error" in result

    def test_target_not_found_returns_error(self, vault, router):
        result = start_shaping.start_shaping(
            str(vault), router, {"target": "Nonexistent File"}
        )
        assert "error" in result
        assert "No artefact found" in result["error"]

    def test_creates_transcript_for_existing_artefact(self, vault, router):
        result = start_shaping.start_shaping(
            str(vault), router, {"target": "Designs/My Design.md"}
        )
        assert result["status"] == "ok"
        assert result["target_path"] == "Designs/My Design.md"
        assert "shaping-transcript" in result["transcript_path"]
        assert "My Design" in result["transcript_path"]
        # Transcript file exists on disk
        abs_path = os.path.join(str(vault), result["transcript_path"])
        assert os.path.isfile(abs_path)

    def test_sets_status_to_shaping(self, vault, router):
        result = start_shaping.start_shaping(
            str(vault), router, {"target": "Designs/My Design.md"}
        )
        assert result["set_status"] is True
        # Verify the source file was updated
        source_path = os.path.join(str(vault), "Designs", "My Design.md")
        with open(source_path) as f:
            content = f.read()
        fields, _ = parse_frontmatter(content)
        assert fields["status"] == "shaping"

    def test_idempotent_status(self, vault, router):
        """Already-shaping artefact should not have status changed."""
        result = start_shaping.start_shaping(
            str(vault), router, {"target": "Designs/Already Shaping.md"}
        )
        assert result["status"] == "ok"
        assert result["set_status"] is False

    def test_no_status_change_for_type_without_shaping(self, vault, router):
        """Wiki type has no status enum → status should not be set."""
        result = start_shaping.start_shaping(
            str(vault), router, {"target": "Wiki/Brain Overview.md"}
        )
        assert result["status"] == "ok"
        assert result["set_status"] is False
        # Verify wiki file is unchanged (no status field added)
        source_path = os.path.join(str(vault), "Wiki", "Brain Overview.md")
        with open(source_path) as f:
            content = f.read()
        fields, _ = parse_frontmatter(content)
        assert "status" not in fields

    def test_transcript_has_correct_source_link(self, vault, router):
        result = start_shaping.start_shaping(
            str(vault), router, {"target": "Designs/My Design.md"}
        )
        transcript_path = os.path.join(str(vault), result["transcript_path"])
        with open(transcript_path) as f:
            content = f.read()
        assert "**Source:** [[Designs/My Design|My Design]]" in content

    def test_transcript_has_correct_type_tag(self, vault, router):
        result = start_shaping.start_shaping(
            str(vault), router, {"target": "Designs/My Design.md"}
        )
        transcript_path = os.path.join(str(vault), result["transcript_path"])
        with open(transcript_path) as f:
            content = f.read()
        fields, _ = parse_frontmatter(content)
        assert "designs" in fields.get("tags", [])

    def test_adds_transcript_link_to_source(self, vault, router):
        result = start_shaping.start_shaping(
            str(vault), router, {"target": "Designs/My Design.md"}
        )
        source_path = os.path.join(str(vault), "Designs", "My Design.md")
        with open(source_path) as f:
            content = f.read()
        assert "**Transcripts:**" in content
        assert "shaping-transcript~My Design" in content

    def test_custom_title_override(self, vault, router):
        result = start_shaping.start_shaping(
            str(vault), router, {
                "target": "Designs/My Design.md",
                "title": "Custom Session Title",
            }
        )
        assert result["status"] == "ok"
        assert "Custom Session Title" in result["transcript_path"]

    def test_basename_resolution(self, vault, router):
        """Target can be just a basename without path."""
        result = start_shaping.start_shaping(
            str(vault), router, {"target": "My Design"}
        )
        assert result["status"] == "ok"
        assert result["target_path"] == "Designs/My Design.md"

    def test_second_transcript_appends_link(self, vault, router):
        """Multiple shaping sessions should append links, not overwrite."""
        start_shaping.start_shaping(
            str(vault), router, {"target": "Designs/My Design.md"}
        )
        # Create a second transcript with a different title
        result2 = start_shaping.start_shaping(
            str(vault), router, {
                "target": "Designs/My Design.md",
                "title": "Session Two",
            }
        )
        assert result2["status"] == "ok"
        source_path = os.path.join(str(vault), "Designs", "My Design.md")
        with open(source_path) as f:
            content = f.read()
        # Both links should be on the same Transcripts line
        assert "**Transcripts:**" in content
        assert "My Design" in content
        assert "Session Two" in content
        # Only one Transcripts line
        assert content.count("**Transcripts:**") == 1

    def test_first_call_creates_with_session_heading(self, vault, router):
        """First call creates transcript with source link and session heading."""
        result = start_shaping.start_shaping(
            str(vault), router, {
                "target": "Designs/My Design.md",
                "skill_type": "Refine",
            }
        )
        assert result["status"] == "ok"
        assert result["appended"] is False
        transcript_path = os.path.join(str(vault), result["transcript_path"])
        with open(transcript_path) as f:
            content = f.read()
        assert "**Source:**" in content
        assert "## Refine session start —" in content

    def test_second_same_day_call_appends(self, vault, router):
        """Second same-day call appends session heading to existing file."""
        result1 = start_shaping.start_shaping(
            str(vault), router, {"target": "Designs/My Design.md"}
        )
        assert result1["appended"] is False
        result2 = start_shaping.start_shaping(
            str(vault), router, {"target": "Designs/My Design.md"}
        )
        assert result2["status"] == "ok"
        assert result2["appended"] is True
        # Same path
        assert result1["transcript_path"] == result2["transcript_path"]
        # File has two session headings
        transcript_path = os.path.join(str(vault), result2["transcript_path"])
        with open(transcript_path) as f:
            content = f.read()
        assert content.count("## ") >= 2
        # Only one file exists (not two)
        folder = os.path.dirname(transcript_path)
        transcript_files = [f for f in os.listdir(folder) if "My Design" in f]
        assert len(transcript_files) == 1

    def test_no_duplicate_transcript_link(self, vault, router):
        """Same-day append should not add a duplicate transcript link."""
        start_shaping.start_shaping(
            str(vault), router, {"target": "Designs/My Design.md"}
        )
        start_shaping.start_shaping(
            str(vault), router, {"target": "Designs/My Design.md"}
        )
        source_path = os.path.join(str(vault), "Designs", "My Design.md")
        with open(source_path) as f:
            content = f.read()
        # Only one Transcripts line with exactly one link
        assert content.count("**Transcripts:**") == 1
        transcript_line = [l for l in content.splitlines() if l.startswith("**Transcripts:**")][0]
        assert transcript_line.count("[[") == 1

    def test_different_artefacts_get_separate_files(self, vault, router):
        """Different artefacts on the same day produce separate transcript files."""
        result1 = start_shaping.start_shaping(
            str(vault), router, {"target": "Designs/My Design.md"}
        )
        result2 = start_shaping.start_shaping(
            str(vault), router, {"target": "Designs/Already Shaping.md"}
        )
        assert result1["transcript_path"] != result2["transcript_path"]
        assert os.path.isfile(os.path.join(str(vault), result1["transcript_path"]))
        assert os.path.isfile(os.path.join(str(vault), result2["transcript_path"]))

    def test_skill_type_appears_in_heading(self, vault, router):
        """skill_type parameter controls the session heading label."""
        result = start_shaping.start_shaping(
            str(vault), router, {
                "target": "Designs/My Design.md",
                "skill_type": "Brainstorm",
            }
        )
        transcript_path = os.path.join(str(vault), result["transcript_path"])
        with open(transcript_path) as f:
            content = f.read()
        assert "## Brainstorm session start" in content

    def test_default_skill_type(self, vault, router):
        """Without skill_type, session heading uses 'Shaping'."""
        result = start_shaping.start_shaping(
            str(vault), router, {"target": "Designs/My Design.md"}
        )
        transcript_path = os.path.join(str(vault), result["transcript_path"])
        with open(transcript_path) as f:
            content = f.read()
        assert "## Shaping session start" in content
