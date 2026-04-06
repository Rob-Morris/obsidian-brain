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

    def test_upgrade_with_ask_preference_previews(self, source_and_vault):
        """artefact_sync: ask (default) → dry-run preview included, files not changed."""
        source, vault = source_and_vault
        result = upgrade.upgrade(str(vault), str(source))
        assert result["status"] == "ok"
        assert "sync_preview" in result
        # With "ask" preference, pending updates appear as warnings (need approval)
        assert len(result["sync_preview"]["warnings"]) > 0
        # File should NOT be updated
        assert "v1" in _read(str(vault / "_Config" / "Taxonomy" / "Living" / "docs.md"))

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
