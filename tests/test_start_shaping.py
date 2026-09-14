"""Tests for start_shaping.py — shaping session bootstrap."""

import inspect
from datetime import datetime, timezone

import pytest

import start_shaping_session
from _common import build_vault_file_index, parse_frontmatter, PartialApplyError


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
        "`_Temporal/Shaping Transcripts/`.\n\n"
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


def _add_preserved_person(vault, *, status="active"):
    people = vault / "People"
    people.mkdir(exist_ok=True)
    folder = people
    if status == "deprecated":
        folder = people / "+Deprecated"
        folder.mkdir(exist_ok=True)
    source = folder / "Alice Smith.md"
    source.write_text(
        "---\ntype: living/person\nkey: alice-smith\ntags:\n"
        "  - person/alice-smith\n"
        f"status: {status}\n---\n\n# Alice Smith\n"
    )

    taxonomy = vault / "_Config" / "Taxonomy" / "Living" / "people.md"
    taxonomy.write_text(
        "# People\n\n"
        "## Naming\n\n`{Title}.md` in `People/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\n"
        "type: living/person\nkey: {key}\ntags:\n  - person/{key}\n"
        "status: active  # active | shaping | parked | deprecated\n"
        "---\n```\n\n"
        "## Lifecycle\n\n"
        "| Status | Meaning |\n|---|---|\n"
        "| `active` | Active. |\n"
        "| `shaping` | Explicit sustained shaping. |\n"
        "| `parked` | Parked. |\n"
        "| `deprecated` | Terminal. |\n\n"
        "## Terminal Status\n\n"
        "When a person reaches `deprecated` status, move it to +Deprecated/.\n\n"
        "## Shaping\n\n"
        "**Flavour:** Discovery\n"
        "**Bar:** The current picture is faithful and clear.\n"
        "**Status behaviour:** `preserve`\n"
        "**Completion status:** `active`\n\n"
        "## Template\n\n[[_Config/Templates/Living/People]]\n"
    )
    (vault / "_Config" / "Templates" / "Living" / "People.md").write_text(
        "---\ntype: living/person\nkey: {key}\ntags: []\n"
        "status: active\n---\n\n"
    )

    import compile_router
    return source, compile_router.compile(str(vault))


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

    @pytest.mark.parametrize("starting_status", ["active", "parked", "shaping"])
    def test_preserved_status_is_not_changed(
        self, vault, starting_status, monkeypatch
    ):
        source, router = _add_preserved_person(vault, status=starting_status)

        def unexpected_lifecycle_change(*_args, **_kwargs):
            raise AssertionError("status-preserving shaping changed lifecycle")

        monkeypatch.setattr(
            start_shaping_session.edit,
            "update_lifecycle_field",
            unexpected_lifecycle_change,
        )

        result = start_shaping_session.start_shaping_session(
            str(vault), router, str(source.relative_to(vault)), mode="discover"
        )

        fields, _ = parse_frontmatter(source.read_text())
        assert fields["status"] == starting_status
        assert result["status_behaviour"] == "preserve"
        assert result["status_changed"] is False
        assert result["transcript_path"].removesuffix(".md") in source.read_text()

    def test_preserved_status_rejects_terminal_target_before_writing(
        self, vault
    ):
        source, router = _add_preserved_person(vault, status="deprecated")

        with pytest.raises(ValueError, match="terminal status.*deprecated"):
            start_shaping_session.start_shaping_session(
                str(vault),
                router,
                str(source.relative_to(vault)),
                mode="discover",
            )

        assert "**Transcripts:**" not in source.read_text()
        assert not list(
            (vault / "_Temporal" / "Shaping Transcripts").rglob("*.md")
        )

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
            # A legacy month-foldered transcript path: the layout is gone, so
            # the link is stale and must not be treated as today's transcript.
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
            "_Temporal/Shaping Transcripts/"
            "20260722-shaping-transcript~Already Shaping.md"
        )
        transcript = vault / transcript_rel
        transcript.parent.mkdir(parents=True, exist_ok=True)
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
            "_Temporal/Shaping Transcripts/"
            "20260722-shaping-transcript~Already Shaping.md"
        )
        transcript = vault / transcript_rel
        transcript.parent.mkdir(parents=True, exist_ok=True)
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
            "_Temporal/Session Notes/20260722-session~My Design.md"
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
        folder = vault / "_Temporal" / "Shaping Transcripts"
        folder.mkdir(parents=True, exist_ok=True)
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
            f"[[_Temporal/Shaping Transcripts/{name[:-3]}|Session]]"
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

    def test_joint_same_day_transcript_with_widest_source_set_is_continued(
        self, vault, router
    ):
        folder = vault / "_Temporal" / "Shaping Transcripts"
        folder.mkdir(parents=True, exist_ok=True)
        single = folder / "20260722-shaping-transcript~Already Shaping.md"
        joint = folder / "20260722-shaping-transcript~My Design.md"
        single.write_text(
            "---\ntype: temporal/shaping-transcript\n---\n"
            "**Source:** [[Designs/Already Shaping|Already Shaping]]\n"
        )
        joint.write_text(
            "---\ntype: temporal/shaping-transcript\n---\n"
            "**Source:** [[Designs/My Design|My Design]], "
            "[[Designs/Already Shaping|Already Shaping]]\n"
        )
        source = vault / "Designs" / "Already Shaping.md"
        source.write_text(
            source.read_text()
            + "\n**Transcripts:** "
            + "[[_Temporal/Shaping Transcripts/"
            + "20260722-shaping-transcript~Already Shaping|Single]], "
            + "[[_Temporal/Shaping Transcripts/"
            + "20260722-shaping-transcript~My Design|Joint]]\n"
        )

        result = start_shaping_session.start_shaping_session(
            str(vault),
            router,
            "Designs/Already Shaping.md",
            mode="refine",
            _now=datetime(2026, 7, 22, 10, 0, tzinfo=timezone.utc),
        )

        assert result["transcript_path"] == str(joint.relative_to(vault))
        assert "session start" in joint.read_text()
        assert "session start" not in single.read_text()

    def test_source_width_deduplicates_path_and_basename_links(
        self, vault, router
    ):
        file_index = build_vault_file_index(str(vault))
        transcript = (
            "**Source:** [[Designs/Already Shaping|Path]], "
            "[[Already Shaping|Basename]]\n"
        )

        assert (
            start_shaping_session._transcript_source_count(
                transcript,
                file_index,
            )
            == 1
        )

    def test_archived_source_link_still_counts_towards_joint_transcript_width(
        self, vault, router
    ):
        folder = vault / "_Temporal" / "Shaping Transcripts"
        folder.mkdir(parents=True, exist_ok=True)
        archive = vault / "_Archive" / "Designs"
        archive.mkdir(parents=True)
        (archive / "Archived Peer.md").write_text("# Archived Peer\n")

        single = folder / "20260722-shaping-transcript~Already Shaping.md"
        joint = folder / "20260722-shaping-transcript~Archived Peer.md"
        single.write_text(
            "---\ntype: temporal/shaping-transcript\n---\n"
            "**Source:** [[Designs/Already Shaping|Already Shaping]]\n"
        )
        joint.write_text(
            "---\ntype: temporal/shaping-transcript\n---\n"
            "**Source:** [[Designs/Already Shaping|Already Shaping]], "
            "[[_Archive/Designs/Archived Peer|Archived Peer]]\n"
        )
        source = vault / "Designs" / "Already Shaping.md"
        source.write_text(
            source.read_text()
            + "\n**Transcripts:** "
            + "[[_Temporal/Shaping Transcripts/"
            + "20260722-shaping-transcript~Already Shaping|Single]], "
            + "[[_Temporal/Shaping Transcripts/"
            + "20260722-shaping-transcript~Archived Peer|Joint]]\n"
        )

        result = start_shaping_session.start_shaping_session(
            str(vault),
            router,
            "Designs/Already Shaping.md",
            mode="refine",
            _now=datetime(2026, 7, 22, 10, 0, tzinfo=timezone.utc),
        )

        assert result["transcript_path"] == str(joint.relative_to(vault))
        assert "session start" in joint.read_text()
        assert "session start" not in single.read_text()

    def test_transcript_write_failure_reports_applied_lifecycle(
        self, vault, router, monkeypatch
    ):
        original_safe_write = start_shaping_session.safe_write_artefact

        def fail_transcript_write(path, content, **kwargs):
            if "Shaping Transcripts" in str(path):
                raise OSError("transcript disk failure")
            return original_safe_write(path, content, **kwargs)

        monkeypatch.setattr(
            start_shaping_session, "safe_write_artefact", fail_transcript_write
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
