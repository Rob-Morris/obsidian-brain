"""Tests for upgrade.py — post-upgrade definition sync."""

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest

import upgrade
from conftest import write_executable as _write_executable


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


def _replace_vault_scripts_with_real(vault):
    """Install the real repo scripts into an existing vault brain-core tree."""
    scripts_dir = vault / ".brain-core" / "scripts"
    shutil.rmtree(scripts_dir)
    _copy_real_scripts(vault / ".brain-core")


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

    def test_upgrade_dry_run_does_not_apply_sync(self, source_and_vault):
        """Dry-run upgrade never APPLIES sync — sync_result must be absent.

        Previously this test also asserted sync_preview was absent, but Bug B
        showed dry-run was hiding sync side effects entirely. Dry-run now
        produces a sync_preview (without applying anything) so users can see
        what sync would do — but sync_result (the applied-changes key) must
        still be absent because nothing was applied.
        """
        source, vault = source_and_vault
        (vault / ".brain" / "preferences.json").write_text(
            json.dumps({"artefact_sync": "auto"})
        )
        result = upgrade.upgrade(str(vault), str(source), dry_run=True)
        assert result["dry_run"] is True
        assert "sync_result" not in result
        # The vault filesystem must be untouched by a dry run — even if sync
        # would have updated _Config taxonomy files, nothing should change.
        assert "v2" not in _read(str(vault / "_Config" / "Taxonomy" / "Living" / "docs.md"))

    def test_dry_run_populates_sync_preview_when_drift_exists(self, source_and_vault):
        """Dry-run must surface a populated sync_preview when sync would update files.

        sync_definitions reads the *vault* library during dry-run (the limitation
        documented in v0.35.9), so this test pre-applies a library bump in the
        vault to trigger drift — the same condition a real same-version --force
        run would observe accurately.
        """
        source, vault = source_and_vault
        (vault / ".brain" / "preferences.json").write_text(
            json.dumps({"artefact_sync": "auto"})
        )
        # Simulate the library being already bumped in the vault relative to the
        # tracked install hash; _Config/Taxonomy/.../docs.md still matches the
        # old install, so sync would safely update it.
        (vault / ".brain-core" / "artefact-library" / "living" / "docs" / "taxonomy.md").write_text(
            "# Docs v1.5\nUpgraded library, _Config not yet synced.\n"
        )

        result = upgrade.upgrade(str(vault), str(source), dry_run=True)

        assert "sync_result" not in result
        assert "sync_preview" in result
        updated_targets = [u["target"] for u in result["sync_preview"]["updated"]]
        assert "_Config/Taxonomy/Living/docs.md" in updated_targets
        # And nothing was actually written
        assert "v1.5" not in _read(str(vault / "_Config" / "Taxonomy" / "Living" / "docs.md"))

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


class TestUpgradeSemanticRepair:
    @pytest.mark.parametrize(
        "config_text",
        [
            "defaults:\n  flags:\n    semantic_retrieval: true\n",
            "defaults:\n  flags:\n    semantic_processing: true\n",
            "defaults:\n  local_runtime:\n    semantic_engine_installed: true\n",
        ],
    )
    def test_upgrade_runs_semantic_repair_for_intent_active_vault(self, source_and_vault, monkeypatch, config_text):
        source, vault = source_and_vault
        local_config = vault / ".brain" / "local" / "config.yaml"
        local_config.write_text(config_text)
        real_run = upgrade.subprocess.run
        calls = []

        def fake_run(args, **kwargs):
            if not any(str(arg).endswith("repair.py") for arg in args):
                return real_run(args, **kwargs)
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=json.dumps({"status": "noop", "steps": []}),
                stderr="",
            )

        monkeypatch.setattr(upgrade.subprocess, "run", fake_run)

        result = upgrade.upgrade(str(vault), str(source))

        assert result["status"] == "ok"
        assert result["semantic_repair"]["outcome"] == "ok"
        assert result["semantic_repair"]["result"]["status"] == "noop"
        assert calls[0][0][0] == sys.executable
        assert calls[0][0][-1] == "--json"
        assert "repair.py" in calls[0][0][1]

    def test_semantic_intent_active_matches_canonical_reader_for_quoted_boolean_strings(self, source_and_vault):
        _source, vault = source_and_vault
        _replace_vault_scripts_with_real(vault)
        local_config = vault / ".brain" / "local" / "config.yaml"
        local_config.write_text(
            'defaults:\n  flags:\n    semantic_retrieval: "true"\n'
        )

        semantic_config = upgrade._load_post_upgrade_semantic_config(vault)
        expected = bool(
            semantic_config.embeddings_enabled(vault)
            or semantic_config.semantic_engine_installed(vault)
        )

        assert expected is True
        assert upgrade._semantic_intent_active(vault) is expected

    def test_semantic_intent_active_ignores_nested_metadata_flags(self, source_and_vault):
        _source, vault = source_and_vault
        _replace_vault_scripts_with_real(vault)
        local_config = vault / ".brain" / "local" / "config.yaml"
        local_config.write_text(
            "meta:\n  semantic_retrieval: true\n"
        )

        assert upgrade._semantic_intent_active(vault) is False

    def test_semantic_intent_active_ignores_missing_file_race(self, source_and_vault, monkeypatch):
        _source, vault = source_and_vault
        local_config = vault / ".brain" / "local" / "config.yaml"
        local_config.write_text("defaults:\n  flags:\n    semantic_retrieval: true\n")
        real_read_text = Path.read_text

        monkeypatch.setattr(
            upgrade,
            "_load_post_upgrade_semantic_config",
            lambda _vault: (_ for _ in ()).throw(ImportError("fallback")),
        )

        def fake_read_text(path, *args, **kwargs):
            if path == local_config:
                raise FileNotFoundError("gone")
            return real_read_text(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", fake_read_text)

        assert upgrade._semantic_intent_active(vault) is False

    def test_semantic_intent_active_propagates_non_missing_read_errors(self, source_and_vault, monkeypatch):
        _source, vault = source_and_vault
        local_config = vault / ".brain" / "local" / "config.yaml"
        local_config.write_text("defaults:\n  flags:\n    semantic_retrieval: true\n")
        real_read_text = Path.read_text

        monkeypatch.setattr(
            upgrade,
            "_load_post_upgrade_semantic_config",
            lambda _vault: (_ for _ in ()).throw(ImportError("fallback")),
        )

        def fake_read_text(path, *args, **kwargs):
            if path == local_config:
                raise PermissionError("denied")
            return real_read_text(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", fake_read_text)

        with pytest.raises(PermissionError, match="denied"):
            upgrade._semantic_intent_active(vault)

    def test_upgrade_skips_semantic_repair_for_lexical_only_vault(self, source_and_vault, monkeypatch):
        source, vault = source_and_vault
        real_run = upgrade.subprocess.run

        def fake_run(args, **kwargs):
            if any(str(arg).endswith("repair.py") for arg in args):
                raise AssertionError("semantic repair should not run without semantic intent")
            return real_run(args, **kwargs)

        monkeypatch.setattr(upgrade.subprocess, "run", fake_run)

        result = upgrade.upgrade(str(vault), str(source))

        assert result["status"] == "ok"
        assert "semantic_repair" not in result


class TestUpgradeProgressLogging:
    def test_upgrade_records_running_stage_before_compile_validation(self, tmp_path, monkeypatch):
        source = _make_real_compile_source(tmp_path)
        vault = _make_minimal_upgrade_vault(tmp_path)
        log_path = vault / ".brain" / "local" / "last-upgrade.json"
        seen = []

        def fake_validate(_vault_root):
            entry = json.loads(log_path.read_text())
            seen.append(entry)
            return "compile failed for test"

        monkeypatch.setattr(upgrade, "_validate_compile", fake_validate)

        result = upgrade.upgrade(str(vault), str(source), sync=False)

        assert result["status"] == "error"
        assert "compile failed for test" in result["message"]
        assert seen
        assert seen[0]["status"] == "running"
        assert seen[0]["stage"] == "validate_compile"
        assert seen[0]["message"] == "Validating the upgraded router/compiler state"

        final = json.loads(log_path.read_text())
        assert final["status"] == "error"
        assert "compile failed for test" in final["message"]

    def test_cli_records_dependency_sync_stage_before_follow_up(self, tmp_path, monkeypatch, capsys):
        source = _make_real_compile_source(tmp_path)
        brain_mcp = source / "brain_mcp"
        brain_mcp.mkdir()
        (brain_mcp / "requirements.txt").write_text("mcp==2.0.0\n")

        vault = _make_minimal_upgrade_vault(tmp_path)
        old_requirements = vault / ".brain-core" / "brain_mcp"
        old_requirements.mkdir(parents=True)
        (old_requirements / "requirements.txt").write_text("mcp==1.0.0\n")
        log_path = vault / ".brain" / "local" / "last-upgrade.json"

        def fake_upgrade(vault_root, source_arg, *, force=False, dry_run=False, sync=None):
            result = {
                "status": "ok",
                "old_version": "0.35.9",
                "new_version": "0.36.7",
                "files_added": [],
                "files_modified": [upgrade.REQ_FILE_REL],
                "files_removed": [],
                "files_unchanged": 0,
                "dry_run": False,
                "message": "Upgraded 0.35.9 → 0.36.7",
            }
            upgrade._write_upgrade_log(vault_root, result)
            return result

        def fake_runtime(vault_root, *, requirements_changed, sync_deps):
            entry = json.loads(log_path.read_text())
            assert entry["status"] == "running"
            assert entry["stage"] == "dependency_sync"
            assert entry["message"] == "Provisioning central managed runtime"
            assert requirements_changed is True
            return {
                "outcome": upgrade.RUNTIME_REUSED,
                "requirements_changed": True,
                "venv_dir": "/fake/venv",
                "python": "/fake/venv/bin/python",
                "python_tag": "py3.12",
                "hash": "deadbeefdeadbeef",
                "venvs_root": "/fake",
            }

        monkeypatch.setattr(upgrade, "upgrade", fake_upgrade)
        monkeypatch.setattr(upgrade, "_ensure_central_runtime", fake_runtime)
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "upgrade.py",
                "--source",
                str(source),
                "--vault",
                str(vault),
                "--json",
            ],
        )

        upgrade.main()

        result = json.loads(capsys.readouterr().out)
        assert result["central_runtime"]["outcome"] == upgrade.RUNTIME_REUSED
        final = json.loads(log_path.read_text())
        assert final["status"] == "ok"
        assert final["central_runtime"]["outcome"] == upgrade.RUNTIME_REUSED


class TestUpgradeCliCentralRuntime:
    """Upgrade CLI ensures the central managed runtime when requirements change.

    `ensure_central_venv` is exercised end-to-end against a fake launcher that
    fabricates the venv layout deterministically — no real `python -m venv`
    or `pip install` runs in tests.
    """

    @staticmethod
    def _fake_launcher(path: Path) -> None:
        """Stub Python launcher: simulates `-m venv DIR` and `-m pip install ...`.

        The `-c` branch delegates to `sys.executable` rather than `/usr/bin/env
        python3` so the launcher works on machines where `python3` is shimmed
        (e.g. asdf without a configured version) — `python_tag` only needs the
        version-info probe to succeed.
        """
        _write_executable(
            path,
            "#!/bin/sh\n"
            "if [ \"$1\" = \"-c\" ]; then\n"
            f"  exec {shlex.quote(sys.executable)} \"$@\"\n"
            "fi\n"
            "if [ \"$1\" = \"-m\" ] && [ \"$2\" = \"venv\" ]; then\n"
            "  mkdir -p \"$3/bin\"\n"
            "  cp \"$0\" \"$3/bin/python\"\n"
            "  exit 0\n"
            "fi\n"
            "if [ \"$1\" = \"-m\" ] && [ \"$2\" = \"pip\" ]; then\n"
            "  shift 2\n"
            "  venv_dir=$(cd \"$(dirname \"$0\")/..\" && pwd)\n"
            "  printf '%s\\n' \"$*\" >> \"$venv_dir/pip-args.txt\"\n"
            "  exit 0\n"
            "fi\n"
            "printf 'unexpected fake-launcher args: %s\\n' \"$*\" >&2\n"
            "exit 1\n",
        )

    def test_cli_creates_central_runtime_when_requirements_change(self, tmp_path, monkeypatch):
        source = _make_real_compile_source(tmp_path)
        brain_mcp = source / "brain_mcp"
        brain_mcp.mkdir()
        (brain_mcp / "requirements.txt").write_text("mcp==2.0.0\n")

        vault = _make_minimal_upgrade_vault(tmp_path)
        old_requirements = vault / ".brain-core" / "brain_mcp"
        old_requirements.mkdir(parents=True)
        (old_requirements / "requirements.txt").write_text("mcp==1.0.0\n")

        fake_home = tmp_path / "home"
        fake_home.mkdir()
        launcher = tmp_path / "launcher" / "python"
        self._fake_launcher(launcher)

        script = Path(__file__).resolve().parents[1] / "src" / "brain-core" / "scripts" / "upgrade.py"
        result = subprocess.run(
            [sys.executable, str(script), "--source", str(source), "--vault", str(vault)],
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "HOME": str(fake_home), "BRAIN_VENV_LAUNCHER": str(launcher)},
        )

        assert result.returncode == 0, result.stderr
        venvs_root = fake_home / ".brain" / "venvs"
        assert venvs_root.is_dir()
        venv_dirs = list(venvs_root.iterdir())
        assert len(venv_dirs) == 1, f"expected exactly one central venv, got {venv_dirs}"
        venv_dir = venv_dirs[0]
        assert (venv_dir / "bin" / "python").is_file()
        # The fake-launcher records pip args; we should see the install of the new requirements
        pip_args = (venv_dir / "pip-args.txt").read_text()
        assert "install --quiet --upgrade pip -r" in pip_args
        assert str(vault / ".brain-core" / "brain_mcp" / "requirements.txt") in pip_args
        assert f"Created central runtime at {venv_dir}" in result.stderr
        assert str(vault / ".brain-core" / "scripts" / "build_index.py") in result.stderr

    def test_cli_forced_sync_deps_reuses_existing_central_runtime(self, tmp_path):
        source = _make_real_compile_source(tmp_path)
        brain_mcp = source / "brain_mcp"
        brain_mcp.mkdir()
        (brain_mcp / "requirements.txt").write_text("mcp==1.0.0\n")

        vault = _make_minimal_upgrade_vault(tmp_path)
        old_requirements = vault / ".brain-core" / "brain_mcp"
        old_requirements.mkdir(parents=True)
        (old_requirements / "requirements.txt").write_text("mcp==1.0.0\n")

        fake_home = tmp_path / "home"
        fake_home.mkdir()
        launcher = tmp_path / "launcher" / "python"
        self._fake_launcher(launcher)

        script = Path(__file__).resolve().parents[1] / "src" / "brain-core" / "scripts" / "upgrade.py"

        env = {**os.environ, "HOME": str(fake_home), "BRAIN_VENV_LAUNCHER": str(launcher)}

        # First run: --sync-deps creates the venv (requirements unchanged but forced)
        first = subprocess.run(
            [sys.executable, str(script), "--source", str(source), "--vault", str(vault), "--sync-deps"],
            capture_output=True, text=True, timeout=60, env=env,
        )
        assert first.returncode == 0, first.stderr
        assert "Created central runtime" in first.stderr

        # Second run: same requirements → reused, not recreated
        second = subprocess.run(
            [sys.executable, str(script), "--source", str(source), "--vault", str(vault), "--sync-deps", "--force"],
            capture_output=True, text=True, timeout=60, env=env,
        )
        assert second.returncode == 0, second.stderr
        assert "Reused central runtime" in second.stderr
