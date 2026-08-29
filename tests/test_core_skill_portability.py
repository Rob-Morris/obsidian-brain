from pathlib import Path


SKILLS_ROOT = Path("src/brain-core/skills")


def test_retired_workflow_families_are_not_shipped_as_core_skills():
    for family in ("code-review", "swarm-test", "superpowers-brain"):
        assert not any(path.is_file() for path in (SKILLS_ROOT / family).rglob("*"))


def test_shaping_has_one_public_skill_and_a_brain_owned_composition_boundary():
    family_root = SKILLS_ROOT / "shaping"
    parent = (family_root / "SKILL.md").read_text()
    references = family_root / "references"

    assert list(family_root.rglob("SKILL.md")) == [family_root / "SKILL.md"]
    assert sorted(path.name for path in references.glob("*.md")) == [
        "assess.md",
        "brain.md",
        "brainstorm.md",
        "discover.md",
        "refine.md",
        "review.md",
    ]
    assert "[portable.md](portable.md)" in parent
    assert "[references/brain.md](references/brain.md)" in parent
    for reference in references.glob("*.md"):
        assert not reference.read_text().startswith("---")
