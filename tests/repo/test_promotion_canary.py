"""The promotion canary carries the judgement the upgrade runner cannot make itself."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_promotion_canary_requires_new_migrations_to_match_the_release_version():
    canary = (REPO_ROOT / ".canaries" / "pre-promotion.md").read_text(encoding="utf-8")
    task = next(line for line in canary.splitlines() if "**Migration versions.**" in line)
    assert "`migrate_to_*.py`" in task
    assert "equal to the release `VERSION`" in task
    assert "[7] Migration versions:" in canary


def test_promotion_canary_runs_the_current_lab_at_the_release_version():
    canary = (REPO_ROOT / ".canaries" / "pre-promotion.md").read_text(encoding="utf-8")
    task = next(line for line in canary.splitlines() if "**Lab at the release version.**" in line)
    assert "`make test-brain-lab-current-docker`" in task
    assert "release `VERSION`" in task
    assert "[8] Lab at the release version:" in canary
