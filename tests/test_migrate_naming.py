"""Tests for migrate_naming.py — vault naming migration."""

import os

import pytest

import migrate_naming


# ---------------------------------------------------------------------------
# slug_to_title
# ---------------------------------------------------------------------------

class TestSlugToTitle:
    def test_basic(self):
        assert migrate_naming.slug_to_title("api-refactor") == "Api Refactor"

    def test_single_word(self):
        assert migrate_naming.slug_to_title("auth") == "Auth"

    def test_multiple_hyphens(self):
        assert migrate_naming.slug_to_title("brain-app-main-shell") == "Brain App Main Shell"

    def test_numbers(self):
        assert migrate_naming.slug_to_title("q3-review") == "Q3 Review"


# ---------------------------------------------------------------------------
# compute_new_filename
# ---------------------------------------------------------------------------

class TestComputeNewFilename:
    def test_old_temporal_pattern(self):
        art = {"classification": "temporal"}
        result = migrate_naming.compute_new_filename("20260324-plan--api-refactor.md", art)
        assert result == "20260324-plan~Api Refactor.md"

    def test_old_temporal_multi_word_prefix(self):
        art = {"classification": "temporal"}
        result = migrate_naming.compute_new_filename("20260324-idea-log--voice-memo.md", art)
        assert result == "20260324-idea-log~Voice Memo.md"

    def test_old_shaping_transcript(self):
        art = {"classification": "temporal"}
        result = migrate_naming.compute_new_filename(
            "20260307-design-transcript--pistols-at-dawn.md", art
        )
        assert result == "20260307-design-transcript~Pistols At Dawn.md"

    def test_log_no_migration(self):
        art = {"classification": "temporal"}
        result = migrate_naming.compute_new_filename("20260324-log.md", art)
        assert result is None

    def test_old_living_pattern(self):
        art = {"classification": "living"}
        result = migrate_naming.compute_new_filename("my-project.md", art)
        assert result == "My Project.md"

    def test_living_already_titled(self):
        art = {"classification": "living"}
        result = migrate_naming.compute_new_filename("My Project.md", art)
        assert result is None  # doesn't match old aggressive slug pattern

    def test_temporal_already_new_style(self):
        art = {"classification": "temporal"}
        result = migrate_naming.compute_new_filename("20260324-plan~API Refactor.md", art)
        assert result is None  # doesn't match old -- pattern

    def test_prefixless_temporal(self):
        """Old research/plan/transcript files without a type prefix."""
        art = {"classification": "temporal", "naming": {"pattern": "yyyymmdd-research~{Title}.md"}}
        result = migrate_naming.compute_new_filename(
            "20260307-discord-animation-research.md", art
        )
        assert result == "20260307-research~Discord Animation Research.md"

    def test_prefixless_temporal_plan(self):
        art = {"classification": "temporal", "naming": {"pattern": "yyyymmdd-plan~{Title}.md"}}
        result = migrate_naming.compute_new_filename("20260309-obsidian-brain-standard.md", art)
        assert result == "20260309-plan~Obsidian Brain Standard.md"

    def test_prefixless_temporal_no_naming_pattern(self):
        """Prefixless temporal without a naming pattern should not migrate."""
        art = {"classification": "temporal"}
        result = migrate_naming.compute_new_filename("20260307-some-slug.md", art)
        assert result is None

    def test_single_word_slug(self):
        art = {"classification": "living"}
        result = migrate_naming.compute_new_filename("auth.md", art)
        assert result == "Auth.md"


# ---------------------------------------------------------------------------
# Vault fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def vault(tmp_path):
    """Create a vault with old-convention files for migration testing."""
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("0.12.0\n")

    config = tmp_path / "_Config"
    config.mkdir()
    (config / "router.md").write_text(
        "Prefer MCP tools.\n\nAlways:\n- Every artefact belongs in a typed folder.\n"
    )

    # Living type: Wiki
    wiki = tmp_path / "Wiki"
    wiki.mkdir()
    (wiki / "rust-lifetimes.md").write_text(
        "---\ntype: living/wiki\ntags: []\n---\n\n# Rust Lifetimes\n"
    )
    (wiki / "Already Good.md").write_text(
        "---\ntype: living/wiki\ntags: []\n---\n\n# Already Good\n"
    )

    # Temporal type: Plans
    temporal = tmp_path / "_Temporal"
    temporal.mkdir()
    plans = temporal / "Plans"
    plans.mkdir()
    month = plans / "2026-03"
    month.mkdir()
    (month / "20260324-plan--api-refactor.md").write_text(
        "---\ntype: temporal/plans\ntags: []\n---\n\n# API Refactor\n"
    )

    # A file that links to the old names
    (wiki / "index.md").write_text(
        "---\ntype: living/wiki\ntags: []\n---\n\n"
        "See [[Wiki/rust-lifetimes]] and "
        "[[_Temporal/Plans/2026-03/20260324-plan--api-refactor]]\n"
    )

    # Taxonomy: Wiki
    tax_living = config / "Taxonomy" / "Living"
    tax_living.mkdir(parents=True)
    (tax_living / "wiki.md").write_text(
        "# Wiki\n\n"
        "## Naming\n\n`{Title}.md` in `Wiki/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: living/wiki\ntags:\n  - topic-tag\n---\n```\n"
    )

    # Taxonomy: Plans
    tax_temporal = config / "Taxonomy" / "Temporal"
    tax_temporal.mkdir(parents=True)
    (tax_temporal / "plans.md").write_text(
        "# Plans\n\n"
        "## Naming\n\n`yyyymmdd-plan~{Title}.md` in `_Temporal/Plans/yyyy-mm/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: temporal/plans\ntags:\n  - plan-tag\n---\n```\n"
    )

    return tmp_path


@pytest.fixture
def router(vault):
    import compile_router
    return compile_router.compile(str(vault))


# ---------------------------------------------------------------------------
# migrate_vault
# ---------------------------------------------------------------------------

class TestMigrateVault:
    def test_dry_run(self, vault, router):
        result = migrate_naming.migrate_vault(str(vault), router=router, dry_run=True)
        assert result["dry_run"] is True
        assert result["renamed"] >= 2  # rust-lifetimes + plan
        # Files should NOT be renamed on disk
        assert (vault / "Wiki" / "rust-lifetimes.md").is_file()

    def test_actual_rename(self, vault, router):
        result = migrate_naming.migrate_vault(str(vault), router=router, dry_run=False)
        assert result["renamed"] >= 2

        # Old files should be gone
        assert not (vault / "Wiki" / "rust-lifetimes.md").is_file()
        assert not (vault / "_Temporal" / "Plans" / "2026-03" / "20260324-plan--api-refactor.md").is_file()

        # New files should exist
        assert (vault / "Wiki" / "Rust Lifetimes.md").is_file()
        assert (vault / "_Temporal" / "Plans" / "2026-03" / "20260324-plan~Api Refactor.md").is_file()

    def test_wikilinks_updated(self, vault, router):
        migrate_naming.migrate_vault(str(vault), router=router, dry_run=False)

        index_path = vault / "Wiki" / "index.md"
        content = index_path.read_text()
        assert "[[Wiki/Rust Lifetimes]]" in content
        assert "[[_Temporal/Plans/2026-03/20260324-plan~Api Refactor]]" in content
        # Old links should be gone
        assert "rust-lifetimes" not in content
        assert "plan--api-refactor" not in content

    def test_skips_already_good_files(self, vault, router):
        result = migrate_naming.migrate_vault(str(vault), router=router, dry_run=True)
        renamed_sources = [d["source"] for d in result["details"]]
        assert not any("Already Good" in s for s in renamed_sources)

    def test_idempotent(self, vault, router):
        # First run
        migrate_naming.migrate_vault(str(vault), router=router, dry_run=False)
        # Need to recompile router since files moved
        import compile_router
        router2 = compile_router.compile(str(vault))
        # Second run should be a no-op
        result = migrate_naming.migrate_vault(str(vault), router=router2, dry_run=False)
        assert result["renamed"] == 0
