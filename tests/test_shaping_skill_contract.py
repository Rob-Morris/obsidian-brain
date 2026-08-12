from pathlib import Path


SKILL_ROOT = Path("src/brain-core/skills/shaping")


def _read(relative_path):
    return (SKILL_ROOT / relative_path).read_text()


def test_assess_reads_taxonomy_and_selects_mode_before_opening_session():
    assess = _read("assess/SKILL.md")

    taxonomy_read = assess.index(
        'resource.read(resource="type", reference="{type-key}")'
    )
    mode_selection = assess.index("Select the shaping mode")
    session_open = assess.index('shaping.start(target="{path}", mode="{mode}")')

    assert taxonomy_read < mode_selection < session_open
    assert "start-shaping" not in assess
    assert "skill_type" not in assess


def test_parent_router_does_not_hardcode_shapeable_type_lists():
    parent = _read("SKILL.md")

    assert "Designs, Plans, Tasks" not in parent
    assert "People, Ideas, Cookies" not in parent
    assert "taxonomy's `## Shaping` metadata" in parent


def test_all_terminal_modes_apply_taxonomy_completion_status():
    for relative_path in ("refine/SKILL.md", "discover/SKILL.md"):
        content = _read(relative_path)
        assert "{completion_status}" in content
        assert "artefact.set-status" in content


def test_skill_uses_granular_shaping_start_consistently():
    content = "\n".join(path.read_text() for path in SKILL_ROOT.rglob("SKILL.md"))

    assert "start-shaping" not in content
    assert '"action": "shape"' not in content
    assert "shaping.start" in content
