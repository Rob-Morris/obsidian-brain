"""Tests for upgrade.py — post-upgrade definition sync."""

import json
import os
import shutil

import pytest

import upgrade


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Path to the real scripts directory in the repo
_REAL_SCRIPTS = os.path.join(
    os.path.dirname(__file__), "..", "src", "brain-core", "scripts"
)


def _write(path, content="placeholder\n"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _copy_real_scripts(source):
    """Copy the repo scripts directory into a source brain-core tree."""
    src_scripts = source / "scripts"
    shutil.copytree(_REAL_SCRIPTS, str(src_scripts))
    upgrade_in_source = src_scripts / "upgrade.py"
    if upgrade_in_source.exists():
        upgrade_in_source.unlink()


def _make_real_compile_source(tmp_path, version="0.29.1"):
    """Create a source tree with the real compiler and upgrade-time scripts."""
    source = tmp_path / f"source-{version.replace('.', '-')}"
    source.mkdir()
    (source / "VERSION").write_text(version + "\n")
    (source / "session-core.md").write_text("# Session Core\n")
    (source / "index.md").write_text("# Index\n")
    (source / "md-bootstrap.md").write_text("# Markdown Bootstrap\n")
    _copy_real_scripts(source)
    return source


def _make_minimal_upgrade_vault(tmp_path, version="0.28.7"):
    """Create a minimal vault that can run the real compile_router."""
    vault = tmp_path / f"vault-{version.replace('.', '-')}"
    vault.mkdir()

    bc = vault / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text(version + "\n")
    (bc / "session-core.md").write_text("# Session Core\n")
    (bc / "scripts").mkdir()
    (bc / "scripts" / "compile_router.py").write_text("import sys; sys.exit(0)\n")

    config = vault / "_Config"
    config.mkdir()
    (config / "router.md").write_text(
        "Prefer MCP tools.\n\n"
        "Always:\n"
        "- Every artefact belongs in a typed folder.\n"
        "- Keep instruction files lean.\n"
    )
    (config / "Taxonomy" / "Living").mkdir(parents=True)

    brain = vault / ".brain"
    brain.mkdir()
    (brain / "preferences.json").write_text("{}\n")
    (brain / "local").mkdir()
    return vault


def _seed_tracking(vault, type_key, taxonomy_path, version="0.18.0"):
    """Record a tracked installed taxonomy against the file currently on disk."""
    from compile_router import hash_file

    tracking = {
        "schema_version": 1,
        "installed": {
            type_key: {
                "brain_core_version": version,
                "installed_at": "2026-01-01T00:00:00+00:00",
                "files": {
                    "taxonomy": {
                        "source_hash": hash_file(str(taxonomy_path)),
                        "target": f"_Config/Taxonomy/Living/{type_key.split('/', 1)[1]}.md",
                    }
                },
            }
        },
    }
    (vault / ".brain" / "tracking.json").write_text(json.dumps(tracking, indent=2) + "\n")
    return tracking["installed"][type_key]["files"]["taxonomy"]["source_hash"]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def source_and_vault(tmp_path):
    """Create a source brain-core dir and a vault with an older version installed.

    Source is at v2.0.0 with an updated taxonomy.
    Vault is at v1.0.0 with the old taxonomy installed.
    """
    # --- Source brain-core (v2.0.0) ---
    source = tmp_path / "source"
    source.mkdir()
    (source / "VERSION").write_text("2.0.0\n")
    (source / "session-core.md").write_text("# Session Core\n")
    (source / "index.md").write_text("# Index\n")
    (source / "md-bootstrap.md").write_text("# Markdown Bootstrap\n")

    # Copy the real scripts directory into source so sync_definitions works
    # after upgrade copies source -> vault
    src_scripts = source / "scripts"
    shutil.copytree(_REAL_SCRIPTS, str(src_scripts))
    # Remove upgrade.py itself from the source scripts (not needed, avoids recursion)
    upgrade_in_source = src_scripts / "upgrade.py"
    if upgrade_in_source.exists():
        upgrade_in_source.unlink()

    # Library with updated taxonomy
    lib = source / "artefact-library" / "living" / "docs"
    lib.mkdir(parents=True)
    (lib / "manifest.yaml").write_text(
        "files:\n"
        "  taxonomy:\n"
        "    source: taxonomy.md\n"
        "    target: _Config/Taxonomy/Living/docs.md\n"
        "folders:\n"
        "  - Documentation/\n"
    )
    (lib / "taxonomy.md").write_text("# Docs v2\nWith lifecycle.\n")

    # --- Vault (v1.0.0 installed) ---
    vault = tmp_path / "vault"
    vault.mkdir()
    bc = vault / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("1.0.0\n")
    (bc / "session-core.md").write_text("# Session Core\n")
    (bc / "scripts").mkdir()
    (bc / "scripts" / "compile_router.py").write_text(
        "import sys; sys.exit(0)\n"
    )

    # Old library in vault
    vlib = bc / "artefact-library" / "living" / "docs"
    vlib.mkdir(parents=True)
    (vlib / "manifest.yaml").write_text(
        "files:\n"
        "  taxonomy:\n"
        "    source: taxonomy.md\n"
        "    target: _Config/Taxonomy/Living/docs.md\n"
        "folders:\n"
        "  - Documentation/\n"
    )
    (vlib / "taxonomy.md").write_text("# Docs v1\n")

    # Minimal router.md required by compile_router validation
    config = vault / "_Config"
    config.mkdir(exist_ok=True)
    (config / "router.md").write_text("Brain vault.\n\nAlways:\n- Typed folders.\n")

    # Installed taxonomy in _Config (matches v1 library)
    tax_dir = vault / "_Config" / "Taxonomy" / "Living"
    tax_dir.mkdir(parents=True)
    (tax_dir / "docs.md").write_text("# Docs v1\n")

    # Tracking: installed from v1 source
    brain = vault / ".brain"
    brain.mkdir()
    from compile_router import hash_file
    v1_hash = hash_file(str(vlib / "taxonomy.md"))
    tracking = {
        "schema_version": 1,
        "installed": {
            "living/docs": {
                "brain_core_version": "1.0.0",
                "installed_at": "2026-01-01T00:00:00+00:00",
                "files": {
                    "taxonomy": {
                        "source_hash": v1_hash,
                        "target": "_Config/Taxonomy/Living/docs.md",
                    }
                },
            }
        },
    }
    (brain / "tracking.json").write_text(json.dumps(tracking, indent=2))
    (brain / "preferences.json").write_text("{}")
    (brain / "local").mkdir()

    return source, vault


class TestPostUpgradeSync:
    def test_upgrade_with_auto_preference_syncs(self, source_and_vault):
        """artefact_sync: auto → definitions synced after upgrade."""
        source, vault = source_and_vault
        (vault / ".brain" / "preferences.json").write_text(
            json.dumps({"artefact_sync": "auto"})
        )
        result = upgrade.upgrade(str(vault), str(source))
        assert result["status"] == "ok"
        assert "sync_result" in result
        assert len(result["sync_result"]["updated"]) > 0
        # Verify file was actually updated
        assert "v2" in _read(str(vault / "_Config" / "Taxonomy" / "Living" / "docs.md"))

    def test_upgrade_with_ask_preference_applies_safe_updates(self, source_and_vault):
        """artefact_sync: ask (default) → safe updates auto-applied."""
        source, vault = source_and_vault
        result = upgrade.upgrade(str(vault), str(source))
        assert result["status"] == "ok"
        assert "sync_result" in result
        # Safe update (no local changes) should be applied
        assert len(result["sync_result"]["updated"]) > 0
        # File should be updated
        assert "v2" in _read(str(vault / "_Config" / "Taxonomy" / "Living" / "docs.md"))

    def test_upgrade_with_skip_preference_no_sync(self, source_and_vault):
        """artefact_sync: skip → no sync at all."""
        source, vault = source_and_vault
        (vault / ".brain" / "preferences.json").write_text(
            json.dumps({"artefact_sync": "skip"})
        )
        result = upgrade.upgrade(str(vault), str(source))
        assert result["status"] == "ok"
        assert "sync_result" not in result
        assert "sync_preview" not in result


class TestPrecompileDefinitionRemediation:
    def test_upgrade_repairs_blocking_tracked_taxonomy_before_compile(self, tmp_path):
        source = _make_real_compile_source(tmp_path)
        daily_lib = source / "artefact-library" / "living" / "daily-notes"
        daily_lib.mkdir(parents=True)
        canonical = (
            "# Daily Notes\n\n"
            "## Naming\n\n"
            "`yyyy-mm-dd ddd.md` in `Daily Notes/`, date source `date`.\n\n"
            "## Frontmatter\n\n"
            "```yaml\n---\n"
            "type: living/daily-note\n"
            "tags:\n"
            "  - daily-note\n"
            "date:\n"
            "---\n```\n"
        )
        (daily_lib / "taxonomy.md").write_text(canonical)

        vault = _make_minimal_upgrade_vault(tmp_path)
        (vault / "Daily Notes").mkdir()
        old_taxonomy = vault / "_Config" / "Taxonomy" / "Living" / "daily-notes.md"
        old_taxonomy.write_text(
            "# Daily Notes\n\n"
            "## Naming\n\n"
            "`yyyy-mm-dd ddd.md` in `Daily Notes/`.\n\n"
            "## Frontmatter\n\n"
            "```yaml\n---\n"
            "type: living/daily-note\n"
            "tags:\n"
            "  - daily-note\n"
            "---\n```\n"
        )
        old_hash = _seed_tracking(vault, "living/daily-notes", old_taxonomy)

        result = upgrade.upgrade(str(vault), str(source), sync=False)

        assert result["status"] == "ok"
        assert result["precompile_patch_migrations"][0]["version"] == "0.29.0"
        assert result["precompile_patch_migrations"][0]["target"] == "pre_compile_patch"
        assert result["precompile_patch_migrations"][0]["updated"] == [
            {
                "type": "living/daily-notes",
                "target": "_Config/Taxonomy/Living/daily-notes.md",
                "action": "update",
            }
        ]
        assert "date source `date`" in _read(str(old_taxonomy))
        assert "date:" in _read(str(old_taxonomy))

        tracking = json.loads((vault / ".brain" / "tracking.json").read_text())
        assert tracking["installed"]["living/daily-notes"]["files"]["taxonomy"]["source_hash"] != old_hash
        ledger = json.loads((vault / ".brain" / "local" / "migrations.json").read_text())
        assert ledger["migrations"]["0.29.0@pre_compile_patch"]["status"] == "ok"

    def test_upgrade_patches_blocking_customised_taxonomy_before_compile(self, tmp_path):
        source = _make_real_compile_source(tmp_path)
        daily_lib = source / "artefact-library" / "living" / "daily-notes"
        daily_lib.mkdir(parents=True)
        (daily_lib / "taxonomy.md").write_text(
            "# Daily Notes\n\n"
            "## Naming\n\n"
            "`yyyy-mm-dd ddd.md` in `Daily Notes/`, date source `date`.\n\n"
            "## Frontmatter\n\n"
            "```yaml\n---\n"
            "type: living/daily-note\n"
            "tags:\n"
            "  - daily-note\n"
            "date:\n"
            "---\n```\n"
        )

        vault = _make_minimal_upgrade_vault(tmp_path)
        (vault / "Daily Notes").mkdir()
        old_taxonomy = vault / "_Config" / "Taxonomy" / "Living" / "daily-notes.md"
        old_taxonomy.write_text(
            "# Daily Notes\n\n"
            "## Purpose\n\n"
            "Original local note.\n\n"
            "## Naming\n\n"
            "`yyyy-mm-dd ddd.md` in `Daily Notes/`.\n\n"
            "## Frontmatter\n\n"
            "```yaml\n---\n"
            "type: living/daily-note\n"
            "tags:\n"
            "  - daily-note\n"
            "---\n```\n"
        )
        tracked_hash = _seed_tracking(vault, "living/daily-notes", old_taxonomy)

        old_taxonomy.write_text(
            "# Daily Notes\n\n"
            "## Purpose\n\n"
            "Original local note.\n\n"
            "Custom sentence worth preserving.\n\n"
            "## Naming\n\n"
            "`yyyy-mm-dd ddd.md` in `Daily Notes/`.\n\n"
            "## Frontmatter\n\n"
            "```yaml\n---\n"
            "type: living/daily-note\n"
            "tags:\n"
            "  - daily-note\n"
            "---\n```\n"
        )

        result = upgrade.upgrade(str(vault), str(source), sync=False)

        assert result["status"] == "ok"
        assert result["precompile_patch_migrations"][0]["patched"] == [
            {
                "type": "living/daily-notes",
                "target": "_Config/Taxonomy/Living/daily-notes.md",
                "action": "conflict",
            }
        ]
        updated = _read(str(old_taxonomy))
        assert "Custom sentence worth preserving." in updated
        assert "date source `date`" in updated
        assert "date:" in updated

        tracking = json.loads((vault / ".brain" / "tracking.json").read_text())
        assert tracking["installed"]["living/daily-notes"]["files"]["taxonomy"]["source_hash"] == tracked_hash

    def test_rollback_restores_precompile_taxonomy_edits(self, tmp_path):
        source = _make_real_compile_source(tmp_path)
        daily_lib = source / "artefact-library" / "living" / "daily-notes"
        daily_lib.mkdir(parents=True)
        (daily_lib / "taxonomy.md").write_text(
            "# Daily Notes\n\n"
            "## Naming\n\n"
            "`yyyy-mm-dd ddd.md` in `Daily Notes/`, date source `date`.\n\n"
            "## Frontmatter\n\n"
            "```yaml\n---\n"
            "type: living/daily-note\n"
            "tags:\n"
            "  - daily-note\n"
            "date:\n"
            "---\n```\n"
        )

        vault = _make_minimal_upgrade_vault(tmp_path)
        (vault / "Daily Notes").mkdir()
        (vault / "Legacy").mkdir()
        daily_taxonomy = vault / "_Config" / "Taxonomy" / "Living" / "daily-notes.md"
        original_daily = (
            "# Daily Notes\n\n"
            "## Naming\n\n"
            "`yyyy-mm-dd ddd.md` in `Daily Notes/`.\n\n"
            "## Frontmatter\n\n"
            "```yaml\n---\n"
            "type: living/daily-note\n"
            "tags:\n"
            "  - daily-note\n"
            "---\n```\n"
        )
        daily_taxonomy.write_text(original_daily)
        tracked_hash = _seed_tracking(vault, "living/daily-notes", daily_taxonomy)

        legacy_taxonomy = vault / "_Config" / "Taxonomy" / "Living" / "legacy.md"
        legacy_taxonomy.write_text(
            "# Legacy\n\n"
            "## Naming\n\n"
            "`yyyymmdd - {Title}.md` in `Legacy/`.\n\n"
            "## Frontmatter\n\n"
            "```yaml\n---\n"
            "type: living/legacy\n"
            "tags:\n"
            "  - legacy\n"
            "---\n```\n"
        )

        result = upgrade.upgrade(str(vault), str(source), sync=False)

        assert result["status"] == "error"
        assert "legacy" in result["message"]
        assert _read(str(daily_taxonomy)) == original_daily
        assert not (vault / ".brain" / "local" / "migrations.json").exists()

        tracking = json.loads((vault / ".brain" / "tracking.json").read_text())
        assert tracking["installed"]["living/daily-notes"]["files"]["taxonomy"]["source_hash"] == tracked_hash


class TestPostUpgradeSyncOverrides:
    def test_upgrade_sync_flag_overrides_ask(self, source_and_vault):
        """sync=True overrides ask preference → definitions synced."""
        source, vault = source_and_vault
        result = upgrade.upgrade(str(vault), str(source), sync=True)
        assert result["status"] == "ok"
        assert "sync_result" in result
        assert len(result["sync_result"]["updated"]) > 0
        assert "v2" in _read(str(vault / "_Config" / "Taxonomy" / "Living" / "docs.md"))

    def test_upgrade_no_sync_flag_overrides_auto(self, source_and_vault):
        """sync=False overrides auto preference → no sync."""
        source, vault = source_and_vault
        (vault / ".brain" / "preferences.json").write_text(
            json.dumps({"artefact_sync": "auto"})
        )
        result = upgrade.upgrade(str(vault), str(source), sync=False)
        assert result["status"] == "ok"
        assert "sync_result" not in result
        assert "sync_preview" not in result

    def test_upgrade_dry_run_no_sync(self, source_and_vault):
        """Dry-run upgrade never runs sync (even with auto preference)."""
        source, vault = source_and_vault
        (vault / ".brain" / "preferences.json").write_text(
            json.dumps({"artefact_sync": "auto"})
        )
        result = upgrade.upgrade(str(vault), str(source), dry_run=True)
        assert result["dry_run"] is True
        assert "sync_result" not in result
        assert "sync_preview" not in result

    def test_sync_result_includes_warnings_for_customised(self, source_and_vault):
        """Customised definitions appear as warnings in sync result."""
        source, vault = source_and_vault
        (vault / ".brain" / "preferences.json").write_text(
            json.dumps({"artefact_sync": "auto"})
        )
        # Customise the local file so it diverges from both tracking and upstream
        (vault / "_Config" / "Taxonomy" / "Living" / "docs.md").write_text("# My custom docs\n")
        result = upgrade.upgrade(str(vault), str(source))
        assert result["status"] == "ok"
        assert "sync_result" in result
        assert len(result["sync_result"]["warnings"]) > 0

    def test_sync_crash_does_not_fail_upgrade(self, source_and_vault):
        """If sync_definitions crashes, upgrade still succeeds with sync_error."""
        source, vault = source_and_vault
        (vault / ".brain" / "preferences.json").write_text(
            json.dumps({"artefact_sync": "auto"})
        )
        # Sabotage sync_definitions.py in source so the post-upgrade import crashes
        (source / "scripts" / "sync_definitions.py").write_text(
            "raise RuntimeError('sabotaged for test')\n"
        )
        result = upgrade.upgrade(str(vault), str(source))
        assert result["status"] == "ok"
        # Upgrade succeeded — sync error is informational
        assert "sync_error" in result
        assert "sync_result" not in result
        assert "sync_preview" not in result
