from pathlib import Path

import pytest


SKILLS_ROOT = Path("src/brain-core/skills")


@pytest.mark.parametrize(
    ("family", "workflows"),
    [
        ("code-review", ("investigate", "fix")),
        ("swarm-test", ("review", "evaluate")),
    ],
)
def test_core_skill_family_has_one_public_skill_and_direct_references(
    family, workflows
):
    family_root = SKILLS_ROOT / family
    assert list(family_root.rglob("SKILL.md")) == [family_root / "SKILL.md"]

    parent = (family_root / "SKILL.md").read_text()
    references = family_root / "references"
    assert sorted(path.name for path in references.glob("*.md")) == sorted(
        f"{workflow}.md" for workflow in workflows
    )
    for workflow in workflows:
        reference = references / f"{workflow}.md"
        assert f"references/{workflow}.md" in parent
        assert not reference.read_text().startswith("---")


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


def test_code_review_root_preserves_review_and_fix_routing():
    family_root = SKILLS_ROOT / "code-review"
    parent = (family_root / "SKILL.md").read_text()
    investigate = (family_root / "references" / "investigate.md").read_text()
    fix = (family_root / "references" / "fix.md").read_text()

    assert "Review (default)" in parent
    assert "Do not edit files" in parent
    assert "**Fix:**" in parent
    assert "[investigate.md](investigate.md)" in fix
    for contract in (
        "git diff HEAD",
        "Reviewer: Reuse",
        "Reviewer: Quality",
        "Reviewer: Efficiency",
    ):
        assert contract in investigate


def test_swarm_test_root_preserves_review_and_evaluate_routing():
    family_root = SKILLS_ROOT / "swarm-test"
    parent = (family_root / "SKILL.md").read_text()
    review = (family_root / "references" / "review.md").read_text()
    evaluate = (family_root / "references" / "evaluate.md").read_text()

    assert "`swarm-test <target>` → **review**" in parent
    assert "`swarm-test evaluate <target>` → **evaluate**" in parent
    assert "Create 8-12 test scenarios" in review
    assert "The user approves the plan before dispatch" in evaluate
    assert "### Counter-factual" in evaluate
