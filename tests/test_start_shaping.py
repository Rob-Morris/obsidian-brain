"""Tests for start_shaping.py — shaping session bootstrap."""

import inspect
import json
import os
from datetime import datetime, timezone

import pytest

import start_shaping
import start_shaping_session
from _common import parse_frontmatter, PartialApplyError


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
    (bc / "session-core.md").write_text("# Session Core\n")

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
        "## Shaping\n\n**Flavour:** Convergent\n"
        "**Bar:** All design decisions are resolved.\n"
        "**Completion status:** `ready`\n\n"
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


def _write_compiled_router(vault, router):
    """Persist the compiled router for CLI bootstrap tests."""
    brain_local = vault / ".brain" / "local"
    brain_local.mkdir(parents=True, exist_ok=True)
    (brain_local / "compiled-router.json").write_text(
        json.dumps(router, indent=2) + "\n"
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestStartShaping:
    def test_module_stays_on_portable_shared_seams(self):
        source = inspect.getsource(start_shaping_session)
        forbidden = (
            "from check import",
            "import check",
            "from _semantic",
            "import _semantic",
            "from config import",
            "import config",
            "from session import",
            "import session",
            "from _repair_runtime",
        )
        for needle in forbidden:
            assert needle not in source

    def test_cli_main_bootstraps_transcript_creation(self, vault, router, monkeypatch, capsys):
        _write_compiled_router(vault, router)
        monkeypatch.setattr(
            start_shaping.sys,
            "argv",
            [
                "start_shaping.py",
                "--target",
                "Designs/My Design.md",
                "--title",
                "Legacy Session Title",
                "--vault",
                str(vault),
            ],
        )

        start_shaping.main()

        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "ok"
        assert payload["transcript_operation"] == "created"
        assert payload["appended"] is False
        assert "transcript_appended" not in payload
        assert "transcript_created" not in payload
        assert "Legacy Session Title" not in payload["transcript_path"]
        transcript_path = vault / payload["transcript_path"]
        assert transcript_path.is_file()
        source_content = (vault / "Designs" / "My Design.md").read_text()
        assert "**Transcripts:**" in source_content
        assert payload["transcript_path"].replace(".md", "") in source_content

    def test_cli_main_reports_partial_lifecycle_failure_without_traceback(
        self, vault, monkeypatch, capsys
    ):
        status_dir = vault / "Designs" / "+Ready"
        status_dir.mkdir()
        source = vault / "Designs" / "My Design.md"
        target = status_dir / "My Design.md"
        source.rename(target)
        import compile_router
        router = compile_router.compile(str(vault))
        _write_compiled_router(vault, router)

        def fail_lifecycle(*_args, **_kwargs):
            from _common import PartialApplyError
            raise PartialApplyError("move set partially applied")

        monkeypatch.setattr(
            start_shaping_session.edit,
            "update_lifecycle_field",
            fail_lifecycle,
        )
        monkeypatch.setattr(
            start_shaping.sys,
            "argv",
            [
                "start_shaping.py",
                "--target",
                "Designs/+Ready/My Design.md",
                "--vault",
                str(vault),
            ],
        )

        with pytest.raises(SystemExit) as exc_info:
            start_shaping.main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Error: move set partially applied" in captured.err
        assert "move set partially applied" in captured.err
        assert "Traceback" not in captured.err

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

    def test_type_without_shaping_contract_is_rejected(self, vault, router):
        result = start_shaping.start_shaping(
            str(vault), router, {"target": "Wiki/Brain Overview.md"}
        )
        assert "not shapeable" in result["error"]
        source_path = os.path.join(str(vault), "Wiki", "Brain Overview.md")
        with open(source_path) as f:
            content = f.read()
        fields, _ = parse_frontmatter(content)
        assert "status" not in fields

    def test_target_content_may_begin_with_error_text(self, vault, router):
        source = vault / "Designs" / "Error Opening.md"
        source.write_text(
            "---\ntype: living/designs\ntags: []\nstatus: new\n---\n\n"
            "Error: this is ordinary artefact content.\n"
        )

        result = start_shaping.start_shaping(
            str(vault), router, {"target": "Designs/Error Opening.md"}
        )

        assert result["status"] == "ok"
        assert "Error: this is ordinary artefact content." in source.read_text()

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

    def test_legacy_title_does_not_change_transcript_identity(self, vault, router):
        result = start_shaping.start_shaping(
            str(vault), router, {
                "target": "Designs/My Design.md",
                "title": "Custom Session Title",
            }
        )
        assert result["status"] == "ok"
        assert "My Design" in result["transcript_path"]
        assert "Custom Session Title" not in result["transcript_path"]

    def test_basename_resolution(self, vault, router):
        """Target can be just a basename without path."""
        result = start_shaping.start_shaping(
            str(vault), router, {"target": "My Design"}
        )
        assert result["status"] == "ok"
        assert result["target_path"] == "Designs/My Design.md"

    def test_legacy_title_still_continues_same_transcript(self, vault, router):
        start_shaping.start_shaping(
            str(vault), router, {"target": "Designs/My Design.md"}
        )
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
        assert "**Transcripts:**" in content
        assert "My Design" in content
        assert "Session Two" not in content
        assert content.count("**Transcripts:**") == 1
        assert result2["appended"] is True

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

    def test_cli_main_appends_same_day_session(self, vault, router, monkeypatch, capsys):
        _write_compiled_router(vault, router)
        argv = [
            "start_shaping.py",
            "--target",
            "Designs/My Design.md",
            "--vault",
            str(vault),
        ]
        monkeypatch.setattr(start_shaping.sys, "argv", argv)

        start_shaping.main()
        first = json.loads(capsys.readouterr().out)

        start_shaping.main()
        second = json.loads(capsys.readouterr().out)

        assert first["transcript_path"] == second["transcript_path"]
        assert second["transcript_operation"] == "appended"
        transcript_path = vault / second["transcript_path"]
        content = transcript_path.read_text()
        assert content.count("## ") >= 2
        assert len(list(transcript_path.parent.glob("*My Design*.md"))) == 1

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

    def test_default_mode_comes_from_taxonomy_flavour(self, vault, router):
        result = start_shaping.start_shaping(
            str(vault), router, {"target": "Designs/My Design.md"}
        )
        transcript_path = os.path.join(str(vault), result["transcript_path"])
        with open(transcript_path) as f:
            content = f.read()
        assert "## Refine session start" in content

    @pytest.mark.parametrize("legacy_label", ["Shaping", "Custom Legacy Label"])
    def test_legacy_non_mode_skill_type_uses_taxonomy_default(
        self, vault, router, legacy_label
    ):
        result = start_shaping.start_shaping(
            str(vault),
            router,
            {"target": "Designs/My Design.md", "skill_type": legacy_label},
        )

        assert result["status"] == "ok"
        assert result["mode"] == "refine"

    def test_legacy_default_mode_resolves_target_once(
        self, vault, router, monkeypatch
    ):
        original = start_shaping_session.resolve_and_validate_folder
        calls = []

        def counting_resolve(*args, **kwargs):
            calls.append(args[2])
            return original(*args, **kwargs)

        monkeypatch.setattr(
            start_shaping_session,
            "resolve_and_validate_folder",
            counting_resolve,
        )

        result = start_shaping.start_shaping(
            str(vault),
            router,
            {"target": "My Design", "skill_type": "Shaping"},
        )

        assert result["status"] == "ok"
        assert calls == ["My Design"]


class TestStartShapingSessionContract:
    def test_rejects_unshapeable_type_before_writing(self, vault, router):
        source = vault / "Wiki" / "Brain Overview.md"
        before = source.read_text()

        with pytest.raises(ValueError, match="not shapeable"):
            start_shaping_session.start_shaping_session(
                str(vault), router, "Wiki/Brain Overview.md", mode="refine"
            )

        assert source.read_text() == before
        assert not list((vault / "_Temporal" / "Shaping Transcripts").rglob("*.md"))

    def test_missing_template_is_preflighted_before_status_change(self, vault, router):
        template = vault / "_Config" / "Templates" / "Temporal" / "Shaping Transcripts.md"
        template.unlink()
        source = vault / "Designs" / "My Design.md"
        before = source.read_text()

        with pytest.raises(FileNotFoundError, match="template"):
            start_shaping_session.start_shaping_session(
                str(vault), router, "Designs/My Design.md", mode="refine"
            )

        assert source.read_text() == before

    def test_stale_linked_transcript_does_not_block_session(
        self, vault, router
    ):
        source = vault / "Designs" / "Already Shaping.md"
        missing = (
            "_Temporal/Shaping Transcripts/2026-07/"
            "20260722-shaping-transcript~Already Shaping"
        )
        source.write_text(
            source.read_text() + f"\n**Transcripts:** [[{missing}|Session]]\n"
        )

        result = start_shaping_session.start_shaping_session(
            str(vault),
            router,
            "Designs/Already Shaping.md",
            mode="refine",
            _now=datetime(2026, 7, 22, 10, 0, tzinfo=timezone.utc),
        )

        assert result["transcript_operation"] == "created"
        assert (vault / result["transcript_path"]).is_file()
        assert source.read_text().count("[[" + missing) == 1

    def test_nonreciprocal_linked_transcript_is_not_appended(
        self, vault, router
    ):
        transcript_rel = (
            "_Temporal/Shaping Transcripts/2026-07/"
            "20260722-shaping-transcript~Already Shaping.md"
        )
        transcript = vault / transcript_rel
        transcript.parent.mkdir(parents=True)
        transcript.write_text(
            "---\ntype: temporal/shaping-transcript\n---\n"
            "**Source:** [[Designs/Somewhere Else|Somewhere Else]]\n"
        )
        source = vault / "Designs" / "Already Shaping.md"
        source.write_text(
            source.read_text()
            + f"\n**Transcripts:** [[{transcript_rel[:-3]}|Session]]\n"
        )

        result = start_shaping_session.start_shaping_session(
            str(vault),
            router,
            "Designs/Already Shaping.md",
            mode="refine",
            _now=datetime(2026, 7, 22, 10, 0, tzinfo=timezone.utc),
        )

        assert "session start" not in transcript.read_text()
        assert result["transcript_operation"] == "created"
        assert result["transcript_path"] != transcript_rel

    def test_basename_links_are_valid_for_transcript_reciprocity(
        self, vault, router
    ):
        transcript_rel = (
            "_Temporal/Shaping Transcripts/2026-07/"
            "20260722-shaping-transcript~Already Shaping.md"
        )
        transcript = vault / transcript_rel
        transcript.parent.mkdir(parents=True)
        transcript.write_text(
            "---\ntype: temporal/shaping-transcript\n---\n"
            "**Source:** [[Already Shaping]]\n"
        )
        source = vault / "Designs" / "Already Shaping.md"
        source.write_text(
            source.read_text()
            + "\n**Transcripts:** [[20260722-shaping-transcript~Already Shaping]]\n"
        )

        result = start_shaping_session.start_shaping_session(
            str(vault),
            router,
            "Designs/Already Shaping.md",
            mode="refine",
            _now=datetime(2026, 7, 22, 10, 0, tzinfo=timezone.utc),
        )

        assert result["transcript_path"] == transcript_rel
        assert result["transcript_operation"] == "appended"

    def test_transcript_template_comes_from_router(self, vault, router):
        custom = vault / "_Config" / "Templates" / "Temporal" / "Custom Session.md"
        custom.write_text(
            "---\ntype: temporal/shaping-transcript\ntags: []\n---\n"
            "Custom transcript for [[SOURCE_DOC_PATH|SOURCE_DOC_TITLE]]"
        )
        transcript_type = next(
            item
            for item in router["artefacts"]
            if item["frontmatter_type"] == "temporal/shaping-transcript"
        )
        transcript_type["template_file"] = (
            "_Config/Templates/Temporal/Custom Session"
        )

        result = start_shaping_session.start_shaping_session(
            str(vault), router, "Designs/My Design.md", mode="refine"
        )

        assert "Custom transcript for" in (vault / result["transcript_path"]).read_text()

    def test_transcript_path_and_filename_come_from_router(self, vault, router):
        transcript_type = next(
            item
            for item in router["artefacts"]
            if item["frontmatter_type"] == "temporal/shaping-transcript"
        )
        transcript_type["path"] = "_Temporal/Session Notes"
        transcript_type["naming"]["pattern"] = "yyyymmdd-session~{Title}.md"
        transcript_type["naming"]["rules"][0]["pattern"] = (
            "yyyymmdd-session~{Title}.md"
        )
        transcript_type["frontmatter_type"] = "temporal/custom-session"
        local_tz = datetime.now().astimezone().tzinfo

        result = start_shaping_session.start_shaping_session(
            str(vault),
            router,
            "Designs/My Design.md",
            mode="refine",
            _now=datetime(2026, 7, 22, 10, 0, tzinfo=local_tz),
        )

        assert result["transcript_path"] == (
            "_Temporal/Session Notes/2026-07/20260722-session~My Design.md"
        )
        assert result["type"] == "temporal/custom-session"
        assert (vault / result["transcript_path"]).is_file()

    def test_missing_compiled_transcript_type_is_rejected(self, vault, router):
        transcript_type = next(
            item
            for item in router["artefacts"]
            if item["frontmatter_type"] == "temporal/shaping-transcript"
        )
        transcript_type["frontmatter_type"] = ""

        with pytest.raises(ValueError, match="frontmatter type"):
            start_shaping_session.start_shaping_session(
                str(vault), router, "Designs/My Design.md", mode="refine"
            )

    def test_multiple_reciprocal_same_day_transcripts_are_rejected(
        self, vault, router
    ):
        folder = vault / "_Temporal" / "Shaping Transcripts" / "2026-07"
        folder.mkdir(parents=True)
        names = [
            "20260722-shaping-transcript~Already Shaping.md",
            "20260722-shaping-transcript~Already Shaping (2).md",
        ]
        for name in names:
            (folder / name).write_text(
                "---\ntype: temporal/shaping-transcript\n---\n"
                "**Source:** [[Designs/Already Shaping|Already Shaping]]\n"
            )
        source = vault / "Designs" / "Already Shaping.md"
        links = " ".join(
            f"[[_Temporal/Shaping Transcripts/2026-07/{name[:-3]}|Session]]"
            for name in names
        )
        source.write_text(source.read_text() + f"\n**Transcripts:** {links}\n")

        with pytest.raises(ValueError, match="Multiple shaping transcripts"):
            start_shaping_session.start_shaping_session(
                str(vault),
                router,
                "Designs/Already Shaping.md",
                mode="refine",
                _now=datetime(2026, 7, 22, 10, 0, tzinfo=timezone.utc),
            )

        for name in names:
            assert "session start" not in (folder / name).read_text()

    def test_transcript_write_failure_reports_applied_lifecycle(
        self, vault, router, monkeypatch
    ):
        original_safe_write = start_shaping_session.safe_write

        def fail_transcript_write(path, content, **kwargs):
            if "Shaping Transcripts" in str(path):
                raise OSError("transcript disk failure")
            return original_safe_write(path, content, **kwargs)

        monkeypatch.setattr(
            start_shaping_session, "safe_write", fail_transcript_write
        )

        with pytest.raises(PartialApplyError, match="durable files.*My Design"):
            start_shaping_session.start_shaping_session(
                str(vault), router, "Designs/My Design.md", mode="refine"
            )

        fields, _ = parse_frontmatter(
            (vault / "Designs" / "My Design.md").read_text()
        )
        assert fields["status"] == "shaping"
        assert not list((vault / "_Temporal" / "Shaping Transcripts").rglob("*.md"))

    def test_non_io_failure_after_lifecycle_is_reported_as_partial_apply(
        self, vault, router, monkeypatch
    ):
        def fail_substitution(*_args, **_kwargs):
            raise ValueError("malformed date token")

        monkeypatch.setattr(
            start_shaping_session,
            "substitute_template_vars",
            fail_substitution,
        )

        with pytest.raises(PartialApplyError, match="malformed date token"):
            start_shaping_session.start_shaping_session(
                str(vault), router, "Designs/My Design.md", mode="refine"
            )

        fields, _ = parse_frontmatter(
            (vault / "Designs" / "My Design.md").read_text()
        )
        assert fields["status"] == "shaping"

    def test_backlink_only_write_updates_modified(self, vault, router):
        now = datetime(2026, 7, 22, 10, 0, tzinfo=timezone.utc)

        start_shaping_session.start_shaping_session(
            str(vault),
            router,
            "Designs/Already Shaping.md",
            mode="refine",
            _now=now,
        )

        fields, _ = parse_frontmatter(
            (vault / "Designs" / "Already Shaping.md").read_text()
        )
        assert fields["modified"] == now.isoformat()

    def test_living_title_with_tilde_is_preserved(self, vault, router):
        source = vault / "Designs" / "A~B.md"
        source.write_text(
            "---\ntype: living/designs\ntags: []\nstatus: new\n"
            "created: 2026-07-22T10:00:00+00:00\n"
            "modified: 2026-07-22T10:00:00+00:00\n---\n\n# A~B\n"
        )

        result = start_shaping_session.start_shaping_session(
            str(vault),
            router,
            "Designs/A~B.md",
            mode="refine",
            _now=datetime(2026, 7, 22, 10, 0, tzinfo=timezone.utc),
        )

        assert result["transcript_path"].endswith(
            "20260722-shaping-transcript~A~B.md"
        )

    def test_backlink_failure_reports_target_and_transcript_as_durable(
        self, vault, router, monkeypatch
    ):
        def fail_backlink(*_args, **_kwargs):
            raise OSError("source backlink failure")

        monkeypatch.setattr(
            start_shaping_session, "_add_transcript_link", fail_backlink
        )

        with pytest.raises(PartialApplyError, match="durable files") as exc_info:
            start_shaping_session.start_shaping_session(
                str(vault), router, "Designs/My Design.md", mode="refine"
            )

        assert "Designs/My Design.md" in str(exc_info.value)
        transcripts = list(
            (vault / "_Temporal" / "Shaping Transcripts").rglob("*.md")
        )
        assert len(transcripts) == 1
        assert transcripts[0].name in str(exc_info.value)

    def test_uses_lifecycle_hooks_for_shaping_status(self, vault, router):
        designs = next(art for art in router["artefacts"] if art["key"] == "designs")
        designs["on_status_change"] = {
            "shaping": {"set": {"shaping_started": "today"}}
        }

        result = start_shaping_session.start_shaping_session(
            str(vault), router, "Designs/My Design.md", mode="refine"
        )

        fields, _ = parse_frontmatter((vault / result["target_path"]).read_text())
        assert fields["status"] == "shaping"
        assert fields["shaping_started"]
        assert result["status_changed"] is True

    def test_same_title_sources_get_distinct_transcripts(self, vault, router):
        nested = vault / "Designs" / "project"
        nested.mkdir()
        second = nested / "My Design.md"
        second.write_text(
            "---\ntype: living/designs\ntags: []\nstatus: new\n---\n\n# Other design\n"
        )

        first_result = start_shaping_session.start_shaping_session(
            str(vault), router, "Designs/My Design.md", mode="refine"
        )
        second_result = start_shaping_session.start_shaping_session(
            str(vault), router, "Designs/project/My Design.md", mode="refine"
        )

        assert first_result["transcript_path"] != second_result["transcript_path"]
        first_transcript = (vault / first_result["transcript_path"]).read_text()
        second_transcript = (vault / second_result["transcript_path"]).read_text()
        assert "[[Designs/My Design|My Design]]" in first_transcript
        assert "[[Designs/project/My Design|My Design]]" in second_transcript

    def test_resume_finds_linked_transcript_after_source_rename(self, vault, router):
        first = start_shaping_session.start_shaping_session(
            str(vault), router, "Designs/My Design.md", mode="refine"
        )
        source = vault / "Designs" / "My Design.md"
        renamed = vault / "Designs" / "Renamed Design.md"
        source.rename(renamed)
        transcript_path = vault / first["transcript_path"]
        transcript_path.write_text(
            transcript_path.read_text().replace(
                "Designs/My Design|", "Designs/Renamed Design|"
            )
        )

        second = start_shaping_session.start_shaping_session(
            str(vault), router, "Designs/Renamed Design.md", mode="refine"
        )

        assert second["transcript_path"] == first["transcript_path"]
        assert second["transcript_operation"] == "appended"

    def test_mode_is_constrained_and_recorded(self, vault, router):
        result = start_shaping_session.start_shaping_session(
            str(vault), router, "Designs/My Design.md", mode="brainstorm"
        )
        transcript = (vault / result["transcript_path"]).read_text()
        assert "## Brainstorm session start" in transcript

        with pytest.raises(ValueError, match="mode"):
            start_shaping_session.start_shaping_session(
                str(vault), router, "Designs/Already Shaping.md", mode="invalid"
            )
