import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


SKILL_ROOT = Path("src/brain-core/skills/shaping")
PORTABLE_REVISION = "022f6dcd3c412a49163682218f3346a5f0299201"
PORTABLE_FILES = {
    ("SKILL.md", "portable.md"): "43bc56f5cfce84e601550b1c09383beeba207fbde9dbd1239851fe39fc3f0279",
    ("references/assess.md", "references/assess.md"): "d14ec41547f0473eb91103229011424a515876a4baa6b68f009f724fe348dc98",
    ("references/brainstorm.md", "references/brainstorm.md"): "8cbc339c004bcf03455af9d0071e7903151cf52651042546d2f1ab08ae1c027e",
    ("references/discover.md", "references/discover.md"): "8a981243a651ef0bec4bc4e992d8f5170127fb023cd231816920f45a5757594f",
    ("references/refine.md", "references/refine.md"): "6dbb7d1a0513f375f84f43a9fae262351d26bf48dc0045d4bb2b4f3a19e23555",
    ("references/review.md", "references/review.md"): "2ed2faaeb95d24e3f9cac6add8c3721eebb660715e8ee82d918d6364176124e5",
}


def _read(relative_path):
    return (SKILL_ROOT / relative_path).read_text()


def _load_vendor_module():
    path = Path("src/scripts/vendor_shaping_skill.py")
    spec = importlib.util.spec_from_file_location("vendor_shaping_skill", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_vendor_uses_the_canonical_portable_path_contract():
    from _common import validate_portable_relative_path as common_validator

    module = _load_vendor_module()

    assert module.validate_portable_relative_path is common_validator
    assert module._portable_relative_path(
        "references/assess.md",
        field="destination",
    ) == "references/assess.md"
    for invalid in ("../outside.md", r"folder\file.md", "NUL.md", "name?.md"):
        with pytest.raises(ValueError, match="destination must be"):
            module._portable_relative_path(invalid, field="destination")


def test_brain_entry_point_composes_portable_workflow_and_narrow_adaptor():
    root = _read("SKILL.md")
    prose = " ".join(root.split())

    assert "name: shaping" in root
    portable = root.index("[portable.md](portable.md)")
    adaptor = root.index("[references/brain.md](references/brain.md)")
    assert portable < adaptor
    assert "behavioural source of truth" in prose
    assert "does not override the portable workflow" in prose
    assert "connected Brain is not, by itself" in prose


def test_vendored_portable_workflow_is_complete_and_content_exact():
    provenance = json.loads(_read("portable-provenance.json"))
    entries = provenance["materialised_files"]

    assert provenance["schema_version"] == 1
    assert provenance["source"] == {
        "repository": "https://github.com/Rob-Morris/agent-skills.git",
        "revision": PORTABLE_REVISION,
        "skill_path": "shaping",
    }
    assert {
        (entry["source"], entry["destination"]): entry["sha256"]
        for entry in entries
    } == PORTABLE_FILES
    for entry in entries:
        content = (SKILL_ROOT / entry["destination"]).read_bytes()
        assert hashlib.sha256(content).hexdigest() == entry["sha256"]


def test_materialiser_updates_only_portable_files_and_records_provenance(tmp_path):
    module = _load_vendor_module()
    source, revision = _source_repository(tmp_path, module)
    destination = tmp_path / "destination"
    destination.mkdir()
    brain_adaptor = destination / "references/brain.md"
    brain_adaptor.parent.mkdir()
    brain_adaptor.write_text("Brain owned\n", encoding="utf-8")

    provenance = module.materialise(
        source,
        destination,
        expected_repository="https://example.test/skills.git",
        expected_revision=revision,
    )

    assert brain_adaptor.read_text() == "Brain owned\n"
    assert provenance["source"] == {
        "repository": "https://example.test/skills.git",
        "revision": revision,
        "skill_path": "shaping",
    }
    assert json.loads(
        (destination / "portable-provenance.json").read_text(encoding="utf-8")
    ) == provenance
    for entry in provenance["materialised_files"]:
        assert (destination / entry["destination"]).read_bytes() == (
            source / entry["source"]
        ).read_bytes()


def test_materialiser_rejects_unmapped_committed_source_before_writing(tmp_path):
    module = _load_vendor_module()
    source, _revision = _source_repository(tmp_path, module)
    unexpected = source / "references/unmapped.md"
    unexpected.write_text("new portable contract\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(source.parent), "add", "shaping"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(source.parent), "commit", "-m", "Add unmapped file"],
        check=True,
        capture_output=True,
    )
    revision = subprocess.run(
        ["git", "-C", str(source.parent), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    destination = tmp_path / "destination"
    destination.mkdir()
    sentinel = destination / "sentinel.md"
    sentinel.write_text("unchanged\n", encoding="utf-8")

    with pytest.raises(ValueError, match="unmapped source files"):
        module.materialise(
            source,
            destination,
            expected_repository="https://example.test/skills.git",
            expected_revision=revision,
        )

    assert sentinel.read_text(encoding="utf-8") == "unchanged\n"
    assert not (destination / "portable.md").exists()


def test_materialiser_removes_only_stale_provenance_owned_files(tmp_path):
    module = _load_vendor_module()
    source, revision = _source_repository(tmp_path, module)
    destination = tmp_path / "destination"
    references = destination / "references"
    references.mkdir(parents=True)
    brain_owned = references / "brain.md"
    brain_owned.write_text("Brain owned\n", encoding="utf-8")
    unclaimed = references / "notes.md"
    unclaimed.write_text("not vendor owned\n", encoding="utf-8")
    stale = references / "legacy.md"
    stale.write_text("stale\n", encoding="utf-8")
    (destination / "portable-provenance.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "materialised_files": [
                    {
                        "source": "references/legacy.md",
                        "destination": "references/legacy.md",
                        "sha256": "0" * 64,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    module.materialise(
        source,
        destination,
        expected_repository="https://example.test/skills.git",
        expected_revision=revision,
    )

    assert not stale.exists()
    assert brain_owned.read_text(encoding="utf-8") == "Brain owned\n"
    assert unclaimed.read_text(encoding="utf-8") == "not vendor owned\n"


def test_materialiser_rejects_revision_repository_and_dirty_source(tmp_path):
    module = _load_vendor_module()
    source, revision = _source_repository(tmp_path, module)

    with pytest.raises(ValueError, match="revision mismatch"):
        module.materialise(
            source,
            tmp_path / "wrong-revision",
            expected_repository="https://example.test/skills.git",
            expected_revision="0" * 40,
        )
    with pytest.raises(ValueError, match="repository mismatch"):
        module.materialise(
            source,
            tmp_path / "wrong-repository",
            expected_repository="https://wrong.example/skills.git",
            expected_revision=revision,
        )

    (source / "SKILL.md").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(ValueError, match="uncommitted changes"):
        module.materialise(
            source,
            tmp_path / "dirty-source",
            expected_repository="https://example.test/skills.git",
            expected_revision=revision,
        )


def test_portable_setup_proposes_plan_before_any_persistence():
    portable = _read("portable.md")
    assess = _read("references/assess.md")

    assert "Read and follow [references/assess.md]" in portable
    proposal = assess.index("Present the result in a compact form")
    approval = assess.index("After approval, initialise the selected records")
    assert proposal < approval
    assert "The user's requested locations and recording preferences remain authoritative" in portable


def test_brain_adaptor_reuses_portable_plan_contract_without_redeclaring_it():
    adaptor = _read("references/brain.md")
    prose = " ".join(adaptor.split())

    assert "Use the session-plan contract defined by the portable assessment workflow" in adaptor
    assert "do not rename, remove, or redeclare its fields" in prose
    for field in (
        "**Style and workflow:**",
        "**Done when:**",
        "**Artefact:**",
        "**Decisions and agent work:**",
        "**Transcript:**",
        "**Environment contributions:**",
    ):
        assert field not in adaptor
    assert "ready to begin or wants to change" in adaptor
    assert "session contract" in adaptor


def test_brain_artefact_defaults_select_brain_capabilities_after_confirmation():
    scenario = _scenario("Brain artefact workflow", "Connected Brain, local document")

    assert "Brain design" in scenario
    assert "design's normal shaping sections" in scenario
    assert "Brain shaping transcript" in scenario
    assert "Brain for target, decisions/work, transcript" in scenario
    assert "`shaping.start` after confirmation" in scenario


def test_new_brain_target_preflights_authority_before_create_and_refresh():
    from _application.registry import current_application_catalogue

    adaptor = _read("references/brain.md")
    standard = Path("src/brain-core/standards/shaping.md").read_text()
    standard_prose = " ".join(standard.split())
    authorities = {
        entry.command_id: entry.authority.value
        for entry in current_application_catalogue().entries
    }

    preflight = adaptor.index("Before proposing a plan that may create")
    create = adaptor.index("confirmed `artefact.create`")
    refresh = adaptor.index("`runtime.refresh-router`", create)
    shaping_start = adaptor.index("`shaping.start`", refresh)
    assert preflight < create < refresh < shaping_start
    assert authorities["artefact.create"] == "contributor"
    assert authorities["runtime.refresh-router"] == "maintainer"
    assert "Creation is a Contributor operation" not in adaptor
    assert "`artefact.create` requires Contributor authority" in standard_prose
    assert "`runtime.refresh-router` requires Maintainer authority" in standard_prose
    assert "When refresh is unavailable, do not create first" in standard_prose


def test_connected_brain_does_not_capture_local_document_records():
    scenario = _scenario(
        "Connected Brain, local document",
        "Brain artefact with scratch transcript",
    )

    assert "repository document through local-file tooling" in scenario
    assert "requested local sidecar" in scenario
    assert "requested OS-temporary scratch path" in scenario
    assert "no Brain record" in scenario
    assert "no `shaping.start`" in scenario


def test_scratch_transcript_overrides_brain_default_for_brain_target():
    scenario = _scenario("Brain artefact with scratch transcript", "No durable artefact")

    assert "Brain artefact, resolved and mutated through Brain" in scenario
    assert "explicit OS-temporary scratch path" in scenario
    assert "scratch storage for the transcript" in scenario
    assert "Do not call `shaping.start`" in scenario
    assert "violate the confirmed plan" in scenario


def test_lifecycle_without_brain_transcript_has_an_explicit_operation():
    adaptor = _read("references/brain.md")
    prose = " ".join(adaptor.split())
    scenario = _scenario(
        "Brain artefact with scratch transcript",
        "Brain transcript with lifecycle override",
    )

    assert "**Lifecycle without a Brain transcript:**" in adaptor
    assert '`artefact.set-status(path="{path}", status="shaping")`' in adaptor
    assert "preserving taxonomy" in prose
    scenario_prose = " ".join(scenario.split())
    assert "Brain for the target, selected decision/work persistence, and selected lifecycle" in scenario_prose
    assert "scratch storage for the transcript" in scenario
    assert "Apply transition lifecycle with `artefact.set-status`" in scenario
    assert "Do not call `shaping.start`" in scenario
    standard = Path("src/brain-core/standards/shaping.md").read_text()
    standard_prose = " ".join(standard.split())
    assert "leave an enduring non-terminal status unchanged" in standard_prose
    assert "began the pass already in `shaping`" in standard_prose
    assert "began the pass already in `shaping`" not in prose


def test_brain_transcript_without_lifecycle_has_an_explicit_operation():
    adaptor = _read("references/brain.md")
    prose = " ".join(adaptor.split())
    scenario = _scenario(
        "Brain transcript with lifecycle override",
        "No durable artefact",
    )

    assert "**Brain transcript without Brain lifecycle:**" in adaptor
    assert "Resolve the normal shaping-transcript artefact" in adaptor
    assert "If none exists, create it with `artefact.create`" in adaptor
    assert "If it already exists, select the standard read-revision-mutate rule" in prose
    assert "select the standard new-target refresh rule" in prose
    assert "under the standard provenance rules" in prose
    assert "leave lifecycle status unchanged" in prose
    assert "explicit lifecycle override leaves status unchanged" in scenario
    assert "Do not call `shaping.start`" in scenario


def test_no_durable_artefact_keeps_all_state_in_session_by_default():
    adaptor = _read("references/brain.md")
    start = adaptor.index("### No durable artefact")
    scenario = adaptor[start : adaptor.index("## Guardrails", start)]

    assert "in-session draft" in scenario
    assert "in-session agenda or omitted" in scenario
    assert "Transcript: omitted unless requested" in scenario
    assert "none beyond session context" in scenario


def test_brain_standard_is_conditional_on_selected_plan_roles():
    standard = Path("src/brain-core/standards/shaping.md").read_text()
    prose = " ".join(standard.split())

    assert "portable shaping workflow](../skills/shaping/portable.md) owns session" in standard
    assert "A connected Brain does not select Brain persistence" in standard
    assert "canonical owner of Brain operation selection" in prose
    assert "Brain lifecycle outcomes" in standard
    assert "transcript stored outside Brain follows the confirmed session plan" in prose
    for portable_heading in (
        "## During Shaping",
        "### One user commitment",
        "## Completing a Shaping Pass",
        "## Optional four-Cs review",
    ):
        assert portable_heading not in standard


def test_effective_shaping_transcript_trigger_requires_confirmed_brain_plan():
    compiler_path = Path("src/brain-core/scripts/compile_router.py")
    spec = importlib.util.spec_from_file_location("compile_router", compiler_path)
    compiler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compiler)

    taxonomy_path = Path(
        "template-vault/_Config/Taxonomy/Temporal/shaping-transcripts.md"
    )
    source_taxonomy = Path(
        "src/brain-core/artefact-library/temporal/shaping-transcripts/taxonomy.md"
    )
    assert taxonomy_path.read_text() == source_taxonomy.read_text()

    _, conditionals = compiler.parse_router(Path("template-vault/_Config/router.md"))
    parsed = compiler.parse_taxonomy_file(taxonomy_path)
    triggers = compiler.merge_triggers(
        conditionals,
        [
            {
                "taxonomy_file": (
                    "_Config/Taxonomy/Temporal/shaping-transcripts.md"
                ),
                "trigger": parsed["trigger"],
            }
        ],
    )
    trigger = next(
        item
        for item in triggers
        if item["target"] == "_Config/Taxonomy/Temporal/shaping-transcripts"
    )

    assert trigger["condition"] == (
        "When a confirmed shaping plan selects a Brain transcript"
    )
    assert "After the shaping plan is confirmed" in trigger["detail"]
    assert "only when that plan selects Brain" in trigger["detail"]
    assert "At the start of shaping, create" not in trigger["detail"]


def _scenario(start_heading, end_heading):
    adaptor = _read("references/brain.md")
    start = adaptor.index(f"### {start_heading}")
    end = adaptor.index(f"### {end_heading}", start)
    return adaptor[start:end]


def _source_repository(tmp_path, module):
    repository = tmp_path / "source-repository"
    source = repository / "shaping"
    repository.mkdir()
    subprocess.run(["git", "init", str(repository)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.name", "Test Author"],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "remote",
            "add",
            "origin",
            "https://example.test/skills.git",
        ],
        check=True,
    )
    for index, relative in enumerate(module.SOURCE_TO_DESTINATION):
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"source {index}\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repository), "add", "shaping"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "commit", "-m", "Add shaping"],
        check=True,
        capture_output=True,
    )
    revision = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return source, revision
