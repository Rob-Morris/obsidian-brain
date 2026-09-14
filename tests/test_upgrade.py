"""Tests for upgrade.py — post-upgrade definition sync."""

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CORE_VERSION = (REPO_ROOT / "src" / "brain-core" / "VERSION").read_text().strip()
CLI_DIR = REPO_ROOT / "cli"
if str(CLI_DIR) not in sys.path:
    sys.path.insert(0, str(CLI_DIR))

from _distribution import install_distribution, verify_distribution
from _local_cli.runtime import CLI_VERSION
import migrate_to_0_53_0
import upgrade
from _application.registry import current_application_catalogue
from _command_interface.profile_migration import (
    _LEGACY_BUILTIN_ALLOW,
    _REMOVED_GRANULAR_COMMANDS,
)
from _command_interface.profiles import builtin_profile_allow_lists
from _common._yaml import dump_yaml_text, load_mapping_file
from brain_test_support import write_executable as _write_executable


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


class _ReadinessSequence:
    def __init__(self, initial, *subsequent):
        self.initial = initial
        self.subsequent = list(subsequent)

    def ensure_runtime_warmup(self, _vault, *, retry_failed):
        assert retry_failed is True
        return "started", self.initial

    def read_runtime_status(self, _vault):
        return self.subsequent.pop(0)


def _runtime_snapshot(state, *, message=None):
    return {
        "state": state,
        "retry_after_ms": 1 if state == "warming" else None,
        "last_error": None if message is None else {"message": message},
    }


def test_upgrade_readiness_waits_for_the_canonical_ready_state(monkeypatch, tmp_path):
    readiness = _ReadinessSequence(
        _runtime_snapshot("warming"),
        _runtime_snapshot("ready"),
    )
    monkeypatch.setattr(upgrade.time, "sleep", lambda _seconds: None)

    result = upgrade._await_runtime_readiness(readiness, tmp_path, 1)

    assert result["outcome"] == "ok"
    assert result["runtime_status"]["state"] == "ready"


def test_upgrade_readiness_surfaces_failure_and_timeout(tmp_path):
    failed = _ReadinessSequence(_runtime_snapshot("failed", message="router failed"))
    timed_out = _ReadinessSequence(_runtime_snapshot("warming"))

    failure = upgrade._await_runtime_readiness(failed, tmp_path, 1)
    timeout = upgrade._await_runtime_readiness(timed_out, tmp_path, 0)

    assert failure["outcome"] == "error"
    assert failure["message"] == "router failed"
    assert timeout["outcome"] == "error"
    assert "upgrade-completion timeout" in timeout["message"]


def test_upgrade_orphan_guidance_uses_only_the_canonical_launcher_commands():
    none = upgrade._runtime_orphan_guidance({"counts": {"orphan_candidates": 0}})
    candidates = upgrade._runtime_orphan_guidance(
        {"counts": {"orphan_candidates": 2}}
    )

    assert none["outcome"] == "ok"
    assert candidates == {
        "outcome": "follow_up",
        "orphan_candidates": 2,
        "dry_run_command": ["brain", "runtime", "remove-orphans", "--dry-run"],
        "remove_command": ["brain", "runtime", "remove-orphans"],
        "message": "2 orphaned shared runtime(s) are safe cleanup candidates.",
    }


@pytest.fixture(autouse=True)
def _isolate_global_cli_targets(tmp_path, monkeypatch):
    """Never let upgrade unit tests inspect or replace the developer's CLI."""

    monkeypatch.setattr(
        upgrade,
        "CLI_TARGET_LOCATIONS",
        (
            tmp_path / "machine" / "user" / "bin" / "brain",
            tmp_path / "machine" / "system" / "bin" / "brain",
        ),
    )
    monkeypatch.setattr(
        upgrade,
        "_complete_runtime_readiness",
        lambda _vault: {
            "outcome": "ok",
            "message": "Runtime warm-up completed and the selected Brain is ready.",
        },
    )
    monkeypatch.setattr(
        upgrade,
        "_inspect_runtime_orphans",
        lambda _vault: {
            "outcome": "ok",
            "orphan_candidates": 0,
            "message": "No orphaned shared runtimes need follow-up.",
        },
    )


def _make_real_compile_source(tmp_path, version="0.29.1"):
    """Create a source tree with the real compiler and upgrade-time scripts."""
    source = tmp_path / f"source-{version.replace('.', '-')}"
    source.mkdir()
    (source / "VERSION").write_text(version + "\n")
    (source / "session-core.md").write_text("# Session Core\n")
    (source / "index.md").write_text("# Index\n")
    (source / "md-bootstrap.md").write_text("# Markdown Bootstrap\n")
    _copy_real_scripts(source)
    shutil.copytree(REPO_ROOT / "src" / "brain-core" / "defaults", source / "defaults")
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


def test_upgrade_runner_applies_the_v055_profile_migration(tmp_path):
    vault = tmp_path / "Brain"
    scripts = vault / ".brain-core" / "scripts"
    shutil.copytree(_REAL_SCRIPTS, scripts)
    config_path = vault / ".brain" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        dump_yaml_text(
            {
                "vault": {
                    "profiles": {
                        name: {"allow": list(commands)}
                        for name, commands in _LEGACY_BUILTIN_ALLOW.items()
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    results, ledger = upgrade._run_migrations(
        str(vault),
        "0.54.59",
        "0.55.0",
        raise_on_error=True,
    )

    assert [(item["version"], item["status"]) for item in results] == [
        ("0.55.0", "ok")
    ]
    assert "0.55.0" in ledger["migrations"]
    profiles = load_mapping_file(config_path)["vault"]["profiles"]
    assert {name: tuple(value["allow"]) for name, value in profiles.items()} == (
        builtin_profile_allow_lists(current_application_catalogue())
    )


def _v055_granular_builtins():
    reverse_consolidations = {}
    for old_tool, current_tool in _REMOVED_GRANULAR_COMMANDS.items():
        reverse_consolidations.setdefault(current_tool, set()).add(old_tool)
    profiles = {}
    for profile, tools in builtin_profile_allow_lists(
        current_application_catalogue()
    ).items():
        previous = set(tools) - {
            "access.reduce",
            "access.request",
            "access.status",
            "runtime.status",
            "runtime.warmup",
        }
        for current_tool, old_tools in reverse_consolidations.items():
            if current_tool in previous:
                previous.remove(current_tool)
                previous.update(old_tools)
        profiles[profile] = {"allow": sorted(previous)}
    return profiles


def test_upgrade_runner_applies_the_v056_profile_consolidation(tmp_path):
    vault = tmp_path / "Brain"
    scripts = vault / ".brain-core" / "scripts"
    shutil.copytree(_REAL_SCRIPTS, scripts)
    config_path = vault / ".brain" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        dump_yaml_text({"vault": {"profiles": _v055_granular_builtins()}}),
        encoding="utf-8",
    )

    results, ledger = upgrade._run_migrations(
        str(vault),
        "0.55.8",
        "0.56.0",
        raise_on_error=True,
    )

    assert [(item["version"], item["status"]) for item in results] == [
        ("0.56.0", "ok")
    ]
    assert "0.56.0" in ledger["migrations"]
    profiles = load_mapping_file(config_path)["vault"]["profiles"]
    assert {
        name: tuple(value["allow"]) for name, value in profiles.items()
    } == builtin_profile_allow_lists(current_application_catalogue())


def test_upgrade_runner_applies_the_v057_access_controls(tmp_path):
    vault = tmp_path / "Brain"
    scripts = vault / ".brain-core" / "scripts"
    shutil.copytree(_REAL_SCRIPTS, scripts)
    current = builtin_profile_allow_lists(current_application_catalogue())
    previous = {
        name: {
            "allow": sorted(
                set(commands)
                - {"access.reduce", "access.request", "access.status"}
            )
        }
        for name, commands in current.items()
    }
    config_path = vault / ".brain" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        dump_yaml_text({"vault": {"profiles": previous}}),
        encoding="utf-8",
    )

    results, ledger = upgrade._run_migrations(
        str(vault),
        "0.56.0",
        "0.57.0",
        raise_on_error=True,
    )

    assert [(item["version"], item["status"]) for item in results] == [
        ("0.57.0", "ok")
    ]
    assert "0.57.0" in ledger["migrations"]
    profiles = load_mapping_file(config_path)["vault"]["profiles"]
    assert {
        name: tuple(value["allow"]) for name, value in profiles.items()
    } == current


def test_upgrade_runner_applies_the_v059_document_profile_expansion(tmp_path):
    vault = tmp_path / "Brain"
    scripts = vault / ".brain-core" / "scripts"
    shutil.copytree(_REAL_SCRIPTS, scripts)
    current = builtin_profile_allow_lists(current_application_catalogue())
    previous = {
        name: {
            "allow": sorted(
                set(commands)
                - {
                    "document.replace-text",
                    "document.update-frontmatter",
                    "document.write-body",
                }
            )
        }
        for name, commands in current.items()
    }
    config_path = vault / ".brain" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        dump_yaml_text({"vault": {"profiles": previous}}),
        encoding="utf-8",
    )

    results, ledger = upgrade._run_migrations(
        str(vault),
        "0.58.0",
        "0.59.0",
        raise_on_error=True,
    )

    assert [(item["version"], item["status"]) for item in results] == [
        ("0.59.0", "ok")
    ]
    assert "0.59.0" in ledger["migrations"]
    profiles = load_mapping_file(config_path)["vault"]["profiles"]
    assert {
        name: tuple(value["allow"]) for name, value in profiles.items()
    } == current


class TestShapingLifecycleMigration:
    def test_normalizes_documented_legacy_completion_status(self):
        content = (
            "# Tasks\n\n"
            "## Lifecycle\n\n"
            "| Status | Meaning |\n"
            "|---|---|\n"
            "| `open` | Open. |\n"
            "| `shaping` | Being shaped. |\n\n"
            "## Shaping\n\n"
            "**Flavour:** Convergent\n"
            "**Bar:** Clear and ready to be performed.\n"
            "**Completion status:** The type's normal working status "
            "(e.g. `open`)\n"
        )

        patched, added = migrate_to_0_53_0._patch_taxonomy(content)

        assert added == []
        assert "**Completion status:** `open`" in patched
        assert "normal working status" not in patched
        assert migrate_to_0_53_0.compile_router.parse_taxonomy_content(
            patched
        )["shaping"]["completion_status"] == "open"

    def test_patches_inline_status_comment(self):
        content = (
            "# Designs\n\n"
            "## Frontmatter\n\n```yaml\n---\n"
            "type: living/designs\n"
            "status: draft  # draft | approved\n"
            "---\n```\n\n"
            "## Shaping\n\n"
            "**Flavour:** Convergent\n"
            "**Bar:** Decisions resolved.\n"
            "**Completion status:** `ready`\n"
        )

        patched, added = migrate_to_0_53_0._patch_taxonomy(content)

        assert added == ["shaping", "ready"]
        assert "status: draft  # draft | approved | shaping | ready" in patched
        assert "## Lifecycle" not in patched

    def test_extends_existing_lifecycle_table(self):
        content = (
            "# Designs\n\n"
            "## Lifecycle\n\n"
            "| `draft` | Draft. |\n"
            "| `approved` | Approved. |\n\n"
            "## Frontmatter\n\n```yaml\n---\n"
            "type: living/designs\nstatus: draft\n---\n```\n\n"
            "## Shaping\n\n"
            "**Flavour:** Convergent\n"
            "**Bar:** Decisions resolved.\n"
            "**Completion status:** `ready`\n"
        )

        patched, added = migrate_to_0_53_0._patch_taxonomy(content)

        assert added == ["shaping", "ready"]
        assert patched.count("## Lifecycle") == 1
        assert "| Status | Meaning |\n|---|---|" in patched
        assert "| `shaping` | Added for shaping compatibility. |" in patched
        assert "| `ready` | Added for shaping compatibility. |" in patched

    def test_lifecycle_prose_is_preserved_in_authoritative_table(self):
        content = (
            "# Designs\n\n"
            "## Lifecycle\n\n"
            "Status values: `open`, `done`.\n\n"
            "## Frontmatter\n\n```yaml\n---\n"
            "type: living/designs\nstatus: open\n---\n```\n\n"
            "## Shaping\n\n"
            "**Flavour:** Convergent\n"
            "**Bar:** Decisions resolved.\n"
            "**Completion status:** `done`\n"
        )

        patched, added = migrate_to_0_53_0._patch_taxonomy(content)

        assert added == ["shaping"]
        assert migrate_to_0_53_0.compile_router.parse_status_enum(patched) == [
            "open",
            "done",
            "shaping",
        ]
        assert "| Status | Meaning |\n|---|---|" in patched
        assert "| `open` | Existing lifecycle status. |" in patched
        assert "| `done` | Existing lifecycle status. |" in patched
        parsed = migrate_to_0_53_0.compile_router.parse_taxonomy_content(patched)
        assert parsed["frontmatter"]["status_enum"] == [
            "open",
            "done",
            "shaping",
        ]

    def test_completion_status_equal_to_shaping_is_not_duplicated(self):
        content = (
            "# Designs\n\n"
            "## Frontmatter\n\n```yaml\n---\n"
            "type: living/designs\nstatus: draft  # draft\n---\n```\n\n"
            "## Shaping\n\n"
            "**Flavour:** Convergent\n"
            "**Bar:** Decisions resolved.\n"
            "**Completion status:** `shaping`\n"
        )

        patched, added = migrate_to_0_53_0._patch_taxonomy(content)

        assert added == ["shaping"]
        assert patched.count("draft | shaping") == 1

    def test_preserved_status_contract_still_adds_shaping_compatibility(self):
        content = (
            "# People\n\n"
            "## Frontmatter\n\n```yaml\n---\n"
            "type: living/person\nstatus: active  # active | parked\n---\n```\n\n"
            "## Shaping\n\n"
            "**Flavour:** Discovery\n"
            "**Bar:** The current picture is faithful and clear.\n"
            "**Status behaviour:** `preserve`\n"
        )

        patched, added = migrate_to_0_53_0._patch_taxonomy(content)

        assert added == ["shaping"]
        assert "status: active  # active | parked | shaping" in patched
        assert migrate_to_0_53_0.compile_router.parse_taxonomy_content(
            patched
        )["shaping"]["status_behaviour"] == "preserve"

    def test_postcondition_rejects_a_lossy_lifecycle_patch(self, monkeypatch):
        content = (
            "# Designs\n\n"
            "## Lifecycle\n\n"
            "Status values: `open`, `done`.\n\n"
            "## Frontmatter\n\n```yaml\n---\n"
            "type: living/designs\nstatus: open\n---\n```\n\n"
            "## Shaping\n\n"
            "**Flavour:** Convergent\n"
            "**Bar:** Decisions resolved.\n"
            "**Completion status:** `done`\n"
        )

        def lossy_patch(original, _statuses, _added):
            return original.replace(
                "Status values: `open`, `done`.",
                "| `shaping` | Added for shaping compatibility. |",
            )

        monkeypatch.setattr(
            migrate_to_0_53_0,
            "_append_lifecycle_rows",
            lossy_patch,
        )

        with pytest.raises(ValueError, match="failed its post-condition"):
            migrate_to_0_53_0._patch_taxonomy(content)

    def test_new_rows_stay_attached_to_table_before_trailing_prose(self):
        content = (
            "# Designs\n\n"
            "## Lifecycle\n\n"
            "| Status | Meaning |\n"
            "|---|---|\n"
            "| `open` | Open. |\n\n"
            "Open work remains active.\n\n"
            "## Frontmatter\n\n```yaml\n---\n"
            "type: living/designs\nstatus: open\n---\n```\n\n"
            "## Shaping\n\n"
            "**Flavour:** Convergent\n"
            "**Bar:** Decisions resolved.\n"
            "**Completion status:** `ready`\n"
        )

        patched, _added = migrate_to_0_53_0._patch_taxonomy(content)

        assert patched.index("| `ready` |") < patched.index(
            "Open work remains active."
        )

    @pytest.mark.parametrize(
        "error_code",
        [
            migrate_to_0_53_0.compile_router.SHAPING_METADATA_ERROR_CODE,
            migrate_to_0_53_0.compile_router.SHAPING_LIFECYCLE_ERROR_CODE,
        ],
    )
    def test_gate_uses_stable_compiler_error_codes(self, tmp_path, error_code):
        taxonomy = tmp_path / "_Config" / "Taxonomy" / "Living" / "designs.md"
        taxonomy.parent.mkdir(parents=True)
        taxonomy.write_text(
            "# Designs\n\n"
            "## Frontmatter\n\n```yaml\n---\n"
            "type: living/designs\n"
            "status: draft  # draft | approved\n"
            "---\n```\n\n"
            "## Shaping\n\n"
            "**Flavour:** Convergent\n"
            "**Bar:** Decisions resolved.\n"
            "**Completion status:** `ready`\n"
        )
        validate_calls = []
        result = migrate_to_0_53_0.patch_pre_compile(
            str(tmp_path),
            context={
                "compile_error": (
                    error_code + ": wording may evolve"
                ),
                "validate_compile": lambda: validate_calls.append(True),
            },
        )

        assert result["status"] == "ok"
        assert result["patched"] == [
            {
                "target": "_Config/Taxonomy/Living/designs.md",
                "added_statuses": ["shaping", "ready"],
            }
        ]
        assert validate_calls == []

    def test_malformed_sibling_is_reported_without_abandoning_repairs(
        self, tmp_path
    ):
        folder = tmp_path / "_Config" / "Taxonomy" / "Living"
        folder.mkdir(parents=True)
        (folder / "designs.md").write_text(
            "# Designs\n\n"
            "## Frontmatter\n\n```yaml\n---\n"
            "type: living/designs\nstatus: draft  # draft\n---\n```\n\n"
            "## Shaping\n\n"
            "**Flavour:** Convergent\n"
            "**Bar:** Decisions resolved.\n"
            "**Completion status:** `ready`\n"
        )
        (folder / "invalid.md").write_text(
            "# Invalid\n\n## Shaping\n\n**Flavour:** Convergent\n"
        )

        result = migrate_to_0_53_0.patch_pre_compile(
            str(tmp_path),
            context={
                "compile_error": (
                    migrate_to_0_53_0.compile_router.SHAPING_LIFECYCLE_ERROR_CODE
                )
            },
        )

        assert result["status"] == "ok"
        assert result["patched"][0]["target"].endswith("designs.md")
        assert result["warnings"] == [
            {
                "target": "_Config/Taxonomy/Living/invalid.md",
                "message": (
                    "taxonomy was not auto-repaired: "
                    "SHAPING_METADATA_INVALID: ## Shaping requires "
                    "**Bar:** metadata"
                ),
            }
        ]


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


class TestAgentSkillUpgradeFollowup:
    def test_upgrade_collapses_clean_tracked_override_that_matches_new_core(
        self, source_and_vault
    ):
        from _skill_library.packages import inspect_package, manifest_value

        source, vault = source_and_vault
        bundled = source / "skills" / "shaping"
        bundled.mkdir(parents=True)
        content = (
            "---\nname: shaping\ndescription: Shaping\n---\n\n"
            "# Shaping\n\nUpdated workflow.\n"
        )
        (bundled / "SKILL.md").write_text(content, encoding="utf-8")
        installed_core = vault / ".brain-core" / "skills" / "shaping"
        installed_core.mkdir(parents=True)
        (installed_core / "SKILL.md").write_text(
            content.replace("Updated workflow.", "Old workflow."),
            encoding="utf-8",
        )
        user = vault / "_Config" / "Skills" / "shaping"
        user.mkdir(parents=True)
        (user / "SKILL.md").write_text(content, encoding="utf-8")
        snapshot = inspect_package(user, expected_name="shaping")
        tracking = {
            "schema_version": 1,
            "managed": {
                "shaping": {
                    "repository": "https://example.invalid/skills.git",
                    "skill_path": "skills/shaping",
                    "configured_ref": "main",
                    "resolved_commit": "abc123",
                    "source_package_sha256": snapshot.package_sha256,
                    "installed_baseline_sha256": snapshot.package_sha256,
                    "installed_manifest": manifest_value(snapshot),
                    "installed_at": "2026-08-01T00:00:00+00:00",
                    "last_checked_at": "2026-08-01T00:00:00+00:00",
                    "available_commit": "abc123",
                    "available_package_sha256": snapshot.package_sha256,
                    "source_error": None,
                    "core_lineage": "shaping",
                }
            },
            "core_checks": {},
        }
        (vault / ".brain" / "skill-sources.json").write_text(
            json.dumps(tracking), encoding="utf-8"
        )

        result = upgrade.upgrade(
            str(vault),
            str(source),
            sync=False,
            sync_deps=False,
        )

        assert result["status"] == "ok"
        assert result["skill_reconciliation"][0]["name"] == "shaping"
        assert not user.exists()
        archived = vault / result["skill_reconciliation"][0]["archived_path"]
        assert (archived / "SKILL.md").read_text(encoding="utf-8") == content
        assert "shaping" not in json.loads(
            (vault / ".brain" / "skill-sources.json").read_text(encoding="utf-8")
        )["managed"]

    def test_core_copy_removes_retired_nested_skill_files_without_migration(
        self, source_and_vault
    ):
        source, vault = source_and_vault
        source_skills = source / "skills"
        installed_skills = vault / ".brain-core" / "skills"
        families = {"shaping": ("assess", "brainstorm", "discover", "refine")}
        retired = ("code-review", "swarm-test", "superpowers-brain")

        for family, workflows in families.items():
            family_source = source_skills / family
            references = family_source / "references"
            references.mkdir(parents=True)
            (family_source / "SKILL.md").write_text(f"---\nname: {family}\n---\n")
            for workflow in workflows:
                (references / f"{workflow}.md").write_text(f"# {workflow}\n")
                legacy = installed_skills / family / workflow / "SKILL.md"
                legacy.parent.mkdir(parents=True)
                legacy.write_text(f"---\nname: {family}:{workflow}\n---\n")
        for family in retired:
            legacy = installed_skills / family / "SKILL.md"
            legacy.parent.mkdir(parents=True)
            legacy.write_text(f"---\nname: {family}\n---\n")

        result = upgrade.upgrade(
            str(vault),
            str(source),
            sync=False,
            sync_deps=False,
        )

        assert result["status"] == "ok"
        for family, workflows in families.items():
            installed_family = installed_skills / family
            assert (installed_family / "SKILL.md").is_file()
            for workflow in workflows:
                assert (installed_family / "references" / f"{workflow}.md").is_file()
                assert not (installed_family / workflow).exists()
                assert str(
                    Path("skills") / family / workflow / "SKILL.md"
                ) in result["files_removed"]
        for family in retired:
            assert not (installed_skills / family).exists()
            assert str(Path("skills") / family / "SKILL.md") in result[
                "files_removed"
            ]

    def test_failed_post_reconciliation_compile_restores_user_override(
        self, source_and_vault, monkeypatch
    ):
        from _skill_library.packages import inspect_package, manifest_value

        source, vault = source_and_vault
        content = (
            "---\nname: shaping\ndescription: Shaping\n---\n\n"
            "# Shaping\n\nUpdated workflow.\n"
        )
        bundled = source / "skills" / "shaping"
        bundled.mkdir(parents=True)
        (bundled / "SKILL.md").write_text(content, encoding="utf-8")
        installed_core = vault / ".brain-core" / "skills" / "shaping"
        installed_core.mkdir(parents=True)
        (installed_core / "SKILL.md").write_text(
            content.replace("Updated workflow.", "Old workflow."),
            encoding="utf-8",
        )
        user = vault / "_Config" / "Skills" / "shaping"
        user.mkdir(parents=True)
        (user / "SKILL.md").write_text(content, encoding="utf-8")
        snapshot = inspect_package(user, expected_name="shaping")
        tracking = {
            "schema_version": 1,
            "managed": {
                "shaping": {
                    "repository": "https://example.invalid/shaping.git",
                    "skill_path": "shaping",
                    "configured_ref": "main",
                    "resolved_commit": "abc123",
                    "source_package_sha256": snapshot.package_sha256,
                    "installed_baseline_sha256": snapshot.package_sha256,
                    "installed_manifest": manifest_value(snapshot),
                    "installed_at": "2026-08-01T00:00:00+00:00",
                    "last_checked_at": "2026-08-01T00:00:00+00:00",
                    "available_commit": "abc123",
                    "available_package_sha256": snapshot.package_sha256,
                    "source_error": None,
                    "core_lineage": "shaping",
                }
            },
            "core_overrides": {},
            "core_checks": {},
        }
        tracking_path = vault / ".brain" / "skill-sources.json"
        tracking_path.write_text(json.dumps(tracking), encoding="utf-8")

        real_validate = upgrade._validate_compile
        calls = 0

        def fail_second_compile(vault_root):
            nonlocal calls
            calls += 1
            if calls == 1:
                return real_validate(vault_root)
            return "simulated post-reconciliation compile failure"

        monkeypatch.setattr(upgrade, "_validate_compile", fail_second_compile)

        result = upgrade.upgrade(
            str(vault),
            str(source),
            sync=False,
            sync_deps=False,
        )

        assert result["status"] == "error"
        assert result["rollback_verified"] is True
        assert (user / "SKILL.md").read_text(encoding="utf-8") == content
        restored = json.loads(tracking_path.read_text(encoding="utf-8"))
        assert "shaping" in restored["managed"]

    def test_adapter_introduction_adds_structured_followup_and_log(
        self, source_and_vault
    ):
        source, vault = source_and_vault
        adapter = source / upgrade.AGENT_SKILL_ADAPTER_REL
        adapter.parent.mkdir(parents=True)
        adapter.write_text("active Brain adapter\n")

        result = upgrade.upgrade(
            str(vault),
            str(source),
            sync=False,
            sync_deps=False,
        )

        assert result["status"] == "ok"
        assert result["followups"] == [
            {
                "id": "configure_agent_skills",
                "reason": "shaping_adapter_added",
                "message": (
                    "The Claude/Codex shaping discovery adapter is now available. "
                    "Install it after the upgrade so each client loads shaping from the active Brain."
                ),
                "command": [
                    sys.executable,
                    str(vault / ".brain-core" / "scripts" / "configure.py"),
                    "agent-skills",
                    "--vault",
                    str(vault),
                    "--client",
                    "all",
                ],
            }
        ]
        logged = json.loads(
            (vault / ".brain" / "local" / "last-upgrade.json").read_text()
        )
        assert logged["followups"] == result["followups"]

    def test_adapter_content_update_adds_update_followup(self, tmp_path):
        vault = tmp_path / "vault"
        diff = {
            "files_added": [],
            "files_modified": [upgrade.AGENT_SKILL_ADAPTER_REL],
        }

        followups = upgrade._agent_skill_adapter_followups(str(vault), diff)

        assert followups[0]["reason"] == "shaping_adapter_updated"
        assert followups[0]["command"][-1] == "all"

    def test_ordinary_shaping_workflow_update_needs_no_adapter_followup(
        self, tmp_path
    ):
        diff = {
            "files_added": [],
            "files_modified": [os.path.join("skills", "shaping", "SKILL.md")],
        }

        assert upgrade._agent_skill_adapter_followups(str(tmp_path), diff) == []

    def test_human_output_renders_recommended_command(
        self, tmp_path, monkeypatch, capsys
    ):
        source = _make_real_compile_source(tmp_path)
        vault = _make_minimal_upgrade_vault(tmp_path)
        command = [
            sys.executable,
            str(vault / ".brain-core" / "scripts" / "configure.py"),
            "agent-skills",
            "--vault",
            str(vault),
            "--client",
            "all",
        ]

        monkeypatch.setattr(
            upgrade,
            "upgrade",
            lambda *_args, **_kwargs: {
                "status": "ok",
                "old_version": "0.52.1",
                "new_version": "0.53.0",
                "files_added": [upgrade.AGENT_SKILL_ADAPTER_REL],
                "files_modified": [],
                "files_removed": [],
                "files_unchanged": 1,
                "dry_run": False,
                "message": "Upgraded 0.52.1 → 0.53.0",
                "followups": [
                    {
                        "id": "configure_agent_skills",
                        "reason": "shaping_adapter_added",
                        "message": "Install the shaping discovery adapter.",
                        "command": command,
                    }
                ],
            },
        )
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "upgrade.py",
                "--source",
                str(source),
                "--vault",
                str(vault),
            ],
        )

        upgrade.main()

        err = capsys.readouterr().err
        assert "Recommended follow-up:" in err
        assert "configure.py agent-skills" in err
        assert "--client all" in err

    def test_json_output_preserves_structured_followup(
        self, tmp_path, monkeypatch, capsys
    ):
        source = _make_real_compile_source(tmp_path)
        vault = _make_minimal_upgrade_vault(tmp_path)
        followup = {
            "id": "configure_agent_skills",
            "reason": "shaping_adapter_updated",
            "message": "Update the shaping discovery adapter.",
            "command": ["python", "configure.py", "agent-skills"],
        }
        monkeypatch.setattr(
            upgrade,
            "upgrade",
            lambda *_args, **_kwargs: {
                "status": "ok",
                "old_version": "0.53.0",
                "new_version": "0.53.1",
                "files_added": [],
                "files_modified": [upgrade.AGENT_SKILL_ADAPTER_REL],
                "files_removed": [],
                "files_unchanged": 1,
                "dry_run": False,
                "message": "Upgraded 0.53.0 → 0.53.1",
                "followups": [followup],
            },
        )
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

        payload = json.loads(capsys.readouterr().out)
        assert payload["followups"] == [followup]

    def test_human_error_output_lists_every_recovery_path(
        self,
        tmp_path,
        monkeypatch,
        capsys,
    ):
        source = _make_real_compile_source(tmp_path)
        vault = _make_minimal_upgrade_vault(tmp_path)
        recovery_paths = [
            str((tmp_path / "recovery" / "brain-core").resolve()),
            str((tmp_path / "recovery" / "old-cli.backup").resolve()),
        ]
        monkeypatch.setattr(
            upgrade,
            "upgrade",
            lambda *_args, **_kwargs: {
                "status": "error",
                "message": "Upgrade rollback is incomplete.",
                "rollback_verified": False,
                "recovery_paths": recovery_paths,
            },
        )
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "upgrade.py",
                "--source",
                str(source),
                "--vault",
                str(vault),
            ],
        )

        with pytest.raises(SystemExit):
            upgrade.main()

        error = capsys.readouterr().err
        assert "Recovery paths:" in error
        assert all(path in error for path in recovery_paths)


class TestPostUpgradeSync:
    def test_upgrade_with_auto_preference_syncs(self, source_and_vault):
        """artefact_sync: auto → definitions synced after upgrade."""
        source, vault = source_and_vault
        (vault / ".brain" / "preferences.json").write_text(
            json.dumps({"artefact_sync": "auto"})
        )
        result = upgrade.upgrade(str(vault), str(source))
        assert result["status"] == "ok"
        assert result["machine_resolution_runtime"]["outcome"] in {"updated", "noop"}
        assert Path(result["machine_resolution_runtime"]["entry"]).is_file()
        assert "sync_result" in result
        assert len(result["sync_result"]["updated"]) > 0
        # Verify file was actually updated
        assert "v2" in _read(str(vault / "_Config" / "Taxonomy" / "Living" / "docs.md"))

    def test_upgrade_recompiles_after_taxonomy_sync(
        self, source_and_vault, monkeypatch
    ):
        source, vault = source_and_vault
        (vault / ".brain" / "preferences.json").write_text(
            json.dumps({"artefact_sync": "auto"})
        )
        observed_taxonomies = []

        def observe_compile(vault_root):
            observed_taxonomies.append(
                _read(
                    str(
                        Path(vault_root)
                        / "_Config"
                        / "Taxonomy"
                        / "Living"
                        / "docs.md"
                    )
                )
            )
            return None

        monkeypatch.setattr(upgrade, "_validate_compile", observe_compile)

        result = upgrade.upgrade(str(vault), str(source))

        assert result["status"] == "ok"
        assert "v1" in observed_taxonomies[0]
        assert "v2" in observed_taxonomies[-1]
        assert len(observed_taxonomies) >= 2

    def test_upgrade_surfaces_post_sync_compile_failure(
        self, source_and_vault, monkeypatch
    ):
        source, vault = source_and_vault
        (vault / ".brain" / "preferences.json").write_text(
            json.dumps({"artefact_sync": "auto"})
        )

        def fail_only_after_sync(vault_root):
            taxonomy = _read(
                str(
                    Path(vault_root)
                    / "_Config"
                    / "Taxonomy"
                    / "Living"
                    / "docs.md"
                )
            )
            return "new taxonomy is invalid" if "v2" in taxonomy else None

        monkeypatch.setattr(upgrade, "_validate_compile", fail_only_after_sync)

        result = upgrade.upgrade(str(vault), str(source))

        assert result["status"] == "ok"
        assert result["sync_compile_error"] == (
            "Definitions were updated but router recompilation failed: "
            "new taxonomy is invalid"
        )

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


    def test_upgrade_repairs_legacy_shaping_taxonomy_before_compile(self, tmp_path):
        source = _make_real_compile_source(tmp_path, version="0.53.1")
        vault = _make_minimal_upgrade_vault(tmp_path, version="0.52.1")
        (vault / "Designs").mkdir()
        taxonomy = vault / "_Config" / "Taxonomy" / "Living" / "designs.md"
        taxonomy.write_text(
            "# Designs\n\n"
            "## Naming\n\n`{Title}.md` in `Designs/`.\n\n"
            "## Frontmatter\n\n```yaml\n---\n"
            "type: living/designs\ntags: []\nstatus: draft\n"
            "---\n```\n\n"
            "Status values: `draft`, `approved`.\n\n"
            "## Shaping\n\n"
            "**Flavour:** Convergent\n"
            "**Bar:** Decisions resolved.\n"
            "**Completion status:** `ready`\n\n"
            "## Template\n\n[[_Config/Templates/Living/Designs]]\n"
        )

        result = upgrade.upgrade(str(vault), str(source), sync=False)

        assert result["status"] == "ok"
        patch_result = next(
            item
            for item in result["precompile_patch_migrations"]
            if item["version"] == "0.53.0"
        )
        assert patch_result["status"] == "ok"
        assert patch_result["patched"] == [
            {
                "target": "_Config/Taxonomy/Living/designs.md",
                "added_statuses": ["shaping", "ready"],
            }
        ]
        repaired = taxonomy.read_text()
        assert "## Lifecycle" in repaired
        assert "| `shaping` |" in repaired
        assert "| `ready` |" in repaired

        compiled = json.loads(
            (vault / ".brain" / "local" / "compiled-router.json").read_text()
        )
        designs = next(item for item in compiled["artefacts"] if item["key"] == "designs")
        assert designs["frontmatter"]["status_enum"] == [
            "draft",
            "approved",
            "shaping",
            "ready",
        ]
        assert designs["shaping"]["completion_status"] == "ready"

    def test_upgrade_normalizes_legacy_shaping_completion_metadata(
        self, tmp_path
    ):
        source = _make_real_compile_source(tmp_path, version="0.53.1")
        vault = _make_minimal_upgrade_vault(tmp_path, version="0.52.1")
        (vault / "Tasks").mkdir()
        taxonomy = vault / "_Config" / "Taxonomy" / "Living" / "tasks.md"
        taxonomy.write_text(
            "# Tasks\n\n"
            "## Naming\n\n`{Title}.md` in `Tasks/`.\n\n"
            "## Lifecycle\n\n"
            "| Status | Meaning |\n"
            "|---|---|\n"
            "| `open` | Open. |\n"
            "| `shaping` | Being shaped. |\n\n"
            "## Frontmatter\n\n```yaml\n---\n"
            "type: living/task\ntags: []\nstatus: open\n"
            "---\n```\n\n"
            "## Shaping\n\n"
            "**Flavour:** Convergent\n"
            "**Bar:** Clear and ready to be performed.\n"
            "**Completion status:** The type's normal working status "
            "(e.g. `open`)\n\n"
            "## Template\n\n[[_Config/Templates/Living/Tasks]]\n"
        )

        result = upgrade.upgrade(str(vault), str(source), sync=False)

        assert result["status"] == "ok"
        patch_result = next(
            item
            for item in result["precompile_patch_migrations"]
            if item["version"] == "0.53.0"
        )
        assert patch_result["patched"] == [
            {
                "target": "_Config/Taxonomy/Living/tasks.md",
                "added_statuses": [],
            }
        ]
        assert "**Completion status:** `open`" in taxonomy.read_text()
        compiled = json.loads(
            (vault / ".brain" / "local" / "compiled-router.json").read_text()
        )
        tasks = next(
            item for item in compiled["artefacts"] if item["key"] == "tasks"
        )
        assert tasks["shaping"]["completion_status"] == "open"

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

    def test_cutover_callback_failure_proves_the_old_core_was_restored(self, tmp_path):
        source = _make_real_compile_source(tmp_path, version=CORE_VERSION)
        vault = _make_minimal_upgrade_vault(tmp_path, version="0.54.59")
        before = {
            path.relative_to(vault / ".brain-core").as_posix(): path.read_bytes()
            for path in (vault / ".brain-core").rglob("*")
            if path.is_file()
        }

        recovery_path = (tmp_path / "machine" / "old-cli.backup").resolve()

        class CheckedExternalFailure(RuntimeError):
            rollback_verified = True
            recovery_paths = (recovery_path,)

        def fail_commit(_result):
            assert (vault / ".brain-core" / "VERSION").read_text().strip() == CORE_VERSION
            raise CheckedExternalFailure("injected CLI commit failure")

        result = upgrade.upgrade(
            str(vault),
            str(source),
            sync=False,
            sync_deps=False,
            commit_callback=fail_commit,
        )

        after = {
            path.relative_to(vault / ".brain-core").as_posix(): path.read_bytes()
            for path in (vault / ".brain-core").rglob("*")
            if path.is_file()
        }
        assert result["status"] == "error"
        assert result["rollback_verified"] is True
        assert result["cutover_commit"]["external_rollback_verified"] is True
        assert result["cutover_commit"]["recovery_paths"] == [str(recovery_path)]
        assert result["recovery_paths"] == [str(recovery_path)]
        assert before == after

    def test_cutover_failure_restores_declared_migration_effects(self, tmp_path):
        source = _make_real_compile_source(tmp_path, version=CORE_VERSION)
        migration = source / "scripts" / "migrations" / "migrate_to_0_62_9.py"
        migration.write_text(
            "from pathlib import Path\n"
            "def prospective_effects(vault_root):\n"
            "    return [Path(vault_root) / 'AGENTS.md']\n"
            "def migrate(vault_root):\n"
            "    (Path(vault_root) / 'AGENTS.md').write_text('migrated\\n')\n"
            "    return {'status': 'ok'}\n",
            encoding="utf-8",
        )
        vault = _make_minimal_upgrade_vault(tmp_path, version="0.62.8")
        bootstrap = vault / "AGENTS.md"
        bootstrap.write_text("original\n", encoding="utf-8")

        class CheckedExternalFailure(RuntimeError):
            rollback_verified = True

        result = upgrade.upgrade(
            str(vault),
            str(source),
            sync=False,
            sync_deps=False,
            commit_callback=lambda _result: (_ for _ in ()).throw(
                CheckedExternalFailure("injected CLI commit failure")
            ),
        )

        assert result["status"] == "error"
        assert result["rollback_verified"] is True
        assert bootstrap.read_text(encoding="utf-8") == "original\n"

    @pytest.mark.parametrize("existing_report", [False, True])
    def test_cutover_failure_unwinds_overlapping_authorisation_migration_snapshots(
        self, tmp_path, existing_report
    ):
        source = _make_real_compile_source(tmp_path, version=CORE_VERSION)
        vault = _make_minimal_upgrade_vault(tmp_path, version="0.66.0")
        shared = vault / ".brain" / "config.yaml"
        local = vault / ".brain" / "local" / "config.yaml"
        old_defaults = vault / ".brain-core" / "defaults" / "config.yaml"
        old_defaults.parent.mkdir()
        old_defaults.write_text(dump_yaml_text({"vault": {"profiles": {
            "reader": {"allow": ["vault.read-file"]},
            "contributor": {"allow": ["artefact.read"]},
        }}}))
        shared.write_text("defaults:\n  access:\n    initial_profile: reader\n")
        local.write_text("defaults:\n  access:\n    initial_profile: contributor\n")
        history = vault / ".brain" / "local" / "access-state.json"
        history.write_text('{"historical": "preserve bytes, never import consent"}\n')
        report = vault / ".brain" / "local" / "authorisation-migration.json"
        if existing_report:
            report.write_text('{"historical": "previous conversion evidence"}\n')
        watched = (shared, local, history, report)
        before = {path: path.read_bytes() if path.exists() else None for path in watched}
        old_core = upgrade._tree_fingerprint(str(vault / ".brain-core"))

        class CheckedExternalFailure(RuntimeError):
            rollback_verified = True

        observed_at_commit = {}

        def fail_commit(_result):
            observed_at_commit.update(
                shared=load_mapping_file(shared), local=load_mapping_file(local),
                report=json.loads(report.read_text()),
            )
            raise CheckedExternalFailure("reject after both migration stages")

        result = upgrade.upgrade(
            str(vault), str(source), sync=False, sync_deps=False,
            commit_callback=fail_commit,
        )

        assert result["status"] == "error"
        assert result["rollback_verified"] is True
        assert result["rollback"]["recovery_backup"] is None
        assert observed_at_commit["shared"]["defaults"]["access"]["initial"] == {
            "mode": "explicit", "commands": ["vault.read-file"]
        }
        assert observed_at_commit["local"]["defaults"]["access"]["initial"] == {
            "mode": "explicit", "commands": ["artefact.read"]
        }
        assert observed_at_commit["report"]["schema"] == "brain.authorisation-migration/1"
        assert {path: path.read_bytes() if path.exists() else None for path in watched} == before
        assert upgrade._tree_fingerprint(str(vault / ".brain-core")) == old_core

    def test_cutover_interrupt_restores_the_old_core_before_reraising(self, tmp_path):
        source = _make_real_compile_source(tmp_path, version=CORE_VERSION)
        vault = _make_minimal_upgrade_vault(tmp_path, version="0.62.8")
        original = (vault / ".brain-core" / "VERSION").read_text(encoding="utf-8")

        with pytest.raises(KeyboardInterrupt):
            upgrade.upgrade(
                str(vault),
                str(source),
                sync=False,
                sync_deps=False,
                commit_callback=lambda _result: (_ for _ in ()).throw(
                    KeyboardInterrupt()
                ),
            )

        assert (vault / ".brain-core" / "VERSION").read_text(encoding="utf-8") == original

    def test_cutover_commits_matching_core_and_cli_without_touching_other_brain(
        self, tmp_path
    ):
        source = _make_real_compile_source(tmp_path, version=CORE_VERSION)
        selected = _make_minimal_upgrade_vault(tmp_path, version="0.54.59")
        other_root = tmp_path / "other"
        other_root.mkdir()
        other = _make_minimal_upgrade_vault(other_root, version="0.54.40")
        other_before = {
            path.relative_to(other).as_posix(): path.read_bytes()
            for path in other.rglob("*")
            if path.is_file()
        }
        cli_binary = tmp_path / "machine" / "bin" / "brain"

        def commit_cli(_result):
            installed = install_distribution(
                REPO_ROOT,
                cli_binary,
                cli_version=CLI_VERSION,
                expected_brain_core_version=CORE_VERSION,
            )
            return {
                "status": "changed",
                "cli_version": installed.cli_version,
                "brain_core_version": installed.brain_core_version,
                "fingerprint": installed.manifest_fingerprint,
            }

        result = upgrade.upgrade(
            str(selected),
            str(source),
            sync=False,
            sync_deps=False,
            commit_callback=commit_cli,
        )

        other_after = {
            path.relative_to(other).as_posix(): path.read_bytes()
            for path in other.rglob("*")
            if path.is_file()
        }
        manifest = verify_distribution(
            tmp_path / "machine" / "lib" / "brain-cli" / CLI_VERSION
        )
        assert result["status"] == "ok"
        assert (selected / ".brain-core" / "VERSION").read_text().strip() == CORE_VERSION
        assert result["cutover_commit"]["cli_version"] == CLI_VERSION
        assert manifest["brain_core_version"] == CORE_VERSION
        assert other_before == other_after

    def test_cutover_cleanup_interrupt_cannot_roll_back_committed_core(
        self, tmp_path
    ):
        source = _make_real_compile_source(tmp_path, version=CORE_VERSION)
        vault = _make_minimal_upgrade_vault(tmp_path, version="0.54.59")
        cli_binary = tmp_path / "machine" / "bin" / "brain"
        install_distribution(
            REPO_ROOT,
            cli_binary,
            cli_version=CLI_VERSION,
            expected_brain_core_version=CORE_VERSION,
        )

        def commit_cli(_result):
            installed = install_distribution(
                REPO_ROOT,
                cli_binary,
                cli_version=CLI_VERSION,
                expected_brain_core_version=CORE_VERSION,
                failpoint=lambda name: (
                    (_ for _ in ()).throw(KeyboardInterrupt())
                    if name == "cleanup_old_distribution"
                    else None
                ),
            )
            return {
                "status": "changed",
                "cli_version": installed.cli_version,
                "brain_core_version": installed.brain_core_version,
                "fingerprint": installed.manifest_fingerprint,
                "cleanup_recovery_paths": [
                    str(path) for path in installed.cleanup_recovery_paths
                ],
            }

        result = upgrade.upgrade(
            str(vault),
            str(source),
            sync=False,
            sync_deps=False,
            commit_callback=commit_cli,
        )

        assert result["status"] == "ok"
        assert (vault / ".brain-core" / "VERSION").read_text().strip() == CORE_VERSION
        assert result["cutover_commit"]["cleanup_recovery_paths"]

    def test_unverified_core_rollback_retains_recovery_backup(
        self, tmp_path, monkeypatch
    ):
        source = _make_real_compile_source(tmp_path, version="0.55.0")
        vault = _make_minimal_upgrade_vault(tmp_path, version="0.54.59")

        class CheckedExternalFailure(RuntimeError):
            rollback_verified = True

        monkeypatch.setattr(
            upgrade,
            "_restore_brain_core",
            lambda *_args: (_ for _ in ()).throw(OSError("injected restore failure")),
        )
        result = upgrade.upgrade(
            str(vault),
            str(source),
            sync=False,
            sync_deps=False,
            commit_callback=lambda _result: (_ for _ in ()).throw(
                CheckedExternalFailure("injected CLI commit failure")
            ),
        )

        recovery = Path(result["rollback"]["recovery_backup"])
        assert result["status"] == "error"
        assert result["rollback_verified"] is False
        assert result["rollback"]["brain_core"] == "unverified"
        assert recovery.is_dir()
        assert (recovery / ".brain-core" / "VERSION").read_text().strip() == "0.54.59"

    def test_snapshot_restore_attempts_every_path_and_preserves_originals(
        self, tmp_path, monkeypatch
    ):
        vault = tmp_path / "vault"
        vault.mkdir()
        first = vault / "first.md"
        second = vault / "second.md"
        introduced = vault / "introduced.md"
        untracked = vault / "untracked.md"
        for path in (first, second, introduced, untracked):
            path.write_text("changed\n", encoding="utf-8")
        snapshots = {
            str(first): {"exists": True, "content": b"original-first\n"},
            str(second): {"exists": True, "content": b"original-second\n"},
            str(introduced): {"exists": False},
        }
        real_safe_write = upgrade._safe_write
        real_remove = upgrade.os.remove

        def fail_target_restores(path, content):
            if str(path) in {str(first), str(second)}:
                raise OSError(f"cannot restore {Path(path).name}")
            return real_safe_write(path, content)

        def fail_target_removals(path):
            if str(path) in {str(introduced), str(untracked)}:
                raise OSError(f"cannot remove {Path(path).name}")
            return real_remove(path)

        monkeypatch.setattr(upgrade, "_safe_write", fail_target_restores)
        monkeypatch.setattr(upgrade.os, "remove", fail_target_removals)

        report = upgrade._restore_snapshots(
            snapshots,
            roots={str(vault): {str(vault)}},
            recovery_dir=str(tmp_path / "recovery"),
        )

        assert len(report.errors) == 4
        assert {str(first), str(second), str(introduced), str(untracked)} <= set(
            report.recovery_paths
        )
        preserved = [
            Path(path).read_bytes()
            for path in report.recovery_paths
            if path.endswith(".original")
        ]
        assert sorted(preserved) == [b"original-first\n", b"original-second\n"]

    def test_snapshot_restore_reports_introduced_directory_failure(
        self, tmp_path, monkeypatch
    ):
        vault = tmp_path / "vault"
        introduced = vault / "introduced"
        introduced.mkdir(parents=True)
        real_rmdir = upgrade.os.rmdir

        def fail_introduced_directory(path):
            if str(path) == str(introduced):
                raise OSError("cannot remove introduced directory")
            return real_rmdir(path)

        monkeypatch.setattr(upgrade.os, "rmdir", fail_introduced_directory)

        report = upgrade._restore_snapshots(
            {},
            roots={str(vault): {str(vault)}},
            recovery_dir=str(tmp_path / "recovery"),
        )

        assert any(str(introduced) in error for error in report.errors)
        assert str(introduced) in report.recovery_paths
        assert upgrade._snapshots_verified(
            {}, roots={str(vault): {str(vault)}}
        ) is False

    def test_snapshot_restore_treats_cross_drive_paths_as_outside_root(
        self,
        tmp_path,
        monkeypatch,
    ):
        vault = tmp_path / "vault"
        vault.mkdir()
        external = tmp_path / "external.txt"
        external.write_text("changed\n", encoding="utf-8")
        snapshots = {
            str(external): {"exists": True, "content": b"original\n"},
        }
        real_commonpath = upgrade.os.path.commonpath

        def cross_drive_commonpath(paths):
            if str(external) in paths:
                raise ValueError("Paths are on different drives")
            return real_commonpath(paths)

        monkeypatch.setattr(upgrade.os.path, "commonpath", cross_drive_commonpath)

        report = upgrade._restore_snapshots(
            snapshots,
            roots={str(vault): {str(vault)}},
            recovery_dir=str(tmp_path / "recovery"),
        )

        assert report.errors == ()
        assert external.read_bytes() == b"original\n"
        assert upgrade._snapshots_verified(
            snapshots,
            roots={str(vault): {str(vault)}},
        ) is True

    def test_direct_cutover_projection_retains_cleanup_recovery_paths(
        self, tmp_path, monkeypatch
    ):
        import _distribution

        recovery = (tmp_path / "old.backup").resolve()
        installed = _distribution.InstalledDistribution(
            (tmp_path / "bin" / "brain").resolve(),
            (tmp_path / "lib" / "brain-cli" / CLI_VERSION).resolve(),
            CLI_VERSION,
            CORE_VERSION,
            "sha256:abc",
            (recovery,),
        )
        monkeypatch.setattr(
            _distribution,
            "install_distribution",
            lambda *_args, **_kwargs: installed,
        )

        result = upgrade._commit_cli_cutover(
            {
                "repo_root": REPO_ROOT,
                "target": installed.cli_binary,
                "cli_version": CLI_VERSION,
                "source_version": CORE_VERSION,
            }
        )

        assert result["cleanup_recovery_paths"] == [str(recovery)]


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


class TestUpgradeRetrievalAssetRepair:
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
        assert result["retrieval_asset_repair"]["scope"] == "semantic"
        assert result["retrieval_asset_repair"]["outcome"] == "ok"
        assert result["retrieval_asset_repair"]["result"]["status"] == "noop"
        assert calls[0][0][0] == sys.executable
        assert calls[0][0][2] == "semantic"
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

    def test_upgrade_runs_lexical_repair_for_lexical_only_vault(self, source_and_vault, monkeypatch):
        source, vault = source_and_vault
        real_run = upgrade.subprocess.run
        calls = []

        def fake_run(args, **kwargs):
            if any(str(arg).endswith("repair.py") for arg in args):
                calls.append((args, kwargs))
                return subprocess.CompletedProcess(
                    args=args,
                    returncode=0,
                    stdout=json.dumps({"status": "ok", "steps": []}),
                    stderr="",
                )
            return real_run(args, **kwargs)

        monkeypatch.setattr(upgrade.subprocess, "run", fake_run)

        result = upgrade.upgrade(str(vault), str(source))

        assert result["status"] == "ok"
        assert result["retrieval_asset_repair"]["scope"] == "lexical"
        assert result["retrieval_asset_repair"]["outcome"] == "ok"
        assert result["retrieval_asset_repair"]["result"]["status"] == "ok"
        assert calls[0][0][2] == "lexical"

    def test_retrieval_asset_repair_returns_structured_error_when_scope_detection_fails(self, source_and_vault, monkeypatch):
        _source, vault = source_and_vault

        monkeypatch.setattr(
            upgrade,
            "_post_upgrade_retrieval_scope",
            lambda _vault: (_ for _ in ()).throw(PermissionError("denied")),
        )

        result = upgrade._repair_retrieval_assets_after_upgrade(vault)

        assert result["scope"] == "retrieval-assets"
        assert result["command"] == []
        assert result["outcome"] == "error"
        assert "denied" in result["message"]

    def test_retrieval_asset_repair_preserves_streams_and_salvages_json_from_noisy_stdout(self, source_and_vault, monkeypatch):
        _source, vault = source_and_vault

        noisy_stdout = "Loading weights\n{\n  \"status\": \"error\",\n  \"message\": \"semantic refresh failed\",\n  \"steps\": []\n}"

        monkeypatch.setattr(upgrade, "_post_upgrade_retrieval_scope", lambda _vault: "semantic")
        monkeypatch.setattr(
            upgrade.subprocess,
            "run",
            lambda args, **kwargs: subprocess.CompletedProcess(
                args=args,
                returncode=7,
                stdout=noisy_stdout,
                stderr="model loader warning",
            ),
        )

        result = upgrade._repair_retrieval_assets_after_upgrade(vault)

        assert result["scope"] == "semantic"
        assert result["outcome"] == "error"
        assert result["returncode"] == 7
        assert result["message"] == "semantic refresh failed"
        assert result["stderr"] == "model loader warning"
        assert result["stdout"] == noisy_stdout
        assert result["result"]["status"] == "error"
        assert result["result"]["message"] == "semantic refresh failed"


def test_mcp_registration_repair_is_noop_without_existing_registration(
    source_and_vault, monkeypatch
):
    _source, vault = source_and_vault
    monkeypatch.setattr(
        upgrade.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("repair subprocess should not run"),
    )

    result = upgrade._repair_mcp_registration_after_upgrade(vault)

    assert result["outcome"] == "noop"
    assert result["command"] == []


def test_mcp_registration_repair_runs_canonical_scope_for_existing_state(
    source_and_vault, monkeypatch
):
    _source, vault = source_and_vault
    (vault / ".mcp.json").write_text("{}\n")
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=json.dumps({"status": "ok", "steps": []}),
            stderr="",
        )

    monkeypatch.setattr(upgrade.subprocess, "run", fake_run)

    result = upgrade._repair_mcp_registration_after_upgrade(vault)

    assert result["outcome"] == "ok"
    assert result["result"]["status"] == "ok"
    assert calls[0][0][2] == "mcp"
    assert calls[0][1]["timeout"] == upgrade.MCP_REGISTRATION_REPAIR_TIMEOUT


class TestUpgradeProgressLogging:
    def test_upgrade_records_retrieval_asset_repair_stage_before_follow_up(self, source_and_vault, monkeypatch):
        source, vault = source_and_vault
        log_path = vault / ".brain" / "local" / "last-upgrade.json"
        seen = []

        def fake_repair(vault_root):
            entry = json.loads(log_path.read_text())
            seen.append(entry)
            assert vault_root == vault
            return {
                "scope": "lexical",
                "command": [sys.executable, str(vault / ".brain-core" / "scripts" / "repair.py"), "lexical"],
                "outcome": "ok",
                "result": {"status": "noop", "steps": []},
            }

        monkeypatch.setattr(upgrade, "_repair_retrieval_assets_after_upgrade", fake_repair)

        result = upgrade.upgrade(str(vault), str(source), sync=False)

        assert result["status"] == "ok"
        assert seen
        assert seen[0]["status"] == "running"
        assert seen[0]["stage"] == "retrieval_asset_repair"
        assert seen[0]["message"] == "Reconciling retrieval asset state after upgrade"

        final = json.loads(log_path.read_text())
        assert final["status"] == "ok"
        assert final["retrieval_asset_repair"]["scope"] == "lexical"

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

    def test_upgrade_records_dependency_sync_stage_before_retrieval_asset_repair(self, source_and_vault, monkeypatch):
        source, vault = source_and_vault
        log_path = vault / ".brain" / "local" / "last-upgrade.json"
        seen = []

        def fake_runtime(vault_root, *, requirements_changed, sync_deps):
            entry = json.loads(log_path.read_text())
            seen.append(entry)
            assert entry["status"] == "running"
            assert entry["stage"] == "dependency_sync"
            assert entry["message"] == "Provisioning central managed runtime"
            assert vault_root == vault
            assert requirements_changed is False
            assert sync_deps is None
            return {
                "outcome": upgrade.RUNTIME_REUSED,
                "requirements_changed": False,
                "venv_dir": "/fake/venv",
                "python": "/fake/venv/bin/python",
                "python_tag": "py3.12",
                "hash": "deadbeefdeadbeef",
                "venvs_root": "/fake",
            }

        monkeypatch.setattr(upgrade, "_ensure_central_runtime", fake_runtime)
        monkeypatch.setattr(
            upgrade,
            "_repair_retrieval_assets_after_upgrade",
            lambda _vault_root: {
                "scope": "lexical",
                "command": [sys.executable, str(vault / ".brain-core" / "scripts" / "repair.py"), "lexical"],
                "outcome": "ok",
                "result": {"status": "noop", "steps": []},
            },
        )

        result = upgrade.upgrade(str(vault), str(source), sync=False)

        assert result["status"] == "ok"
        assert seen
        final = json.loads(log_path.read_text())
        assert final["status"] == "ok"
        assert final["central_runtime"]["outcome"] == upgrade.RUNTIME_REUSED
        assert final["retrieval_asset_repair"]["scope"] == "lexical"

    def test_cli_passes_sync_deps_through_to_upgrade(self, tmp_path, monkeypatch, capsys):
        source = _make_real_compile_source(tmp_path)
        brain_mcp = source / "brain_mcp"
        brain_mcp.mkdir()
        (brain_mcp / "requirements.txt").write_text("mcp==2.0.0\n")

        vault = _make_minimal_upgrade_vault(tmp_path)
        old_requirements = vault / ".brain-core" / "brain_mcp"
        old_requirements.mkdir(parents=True)
        (old_requirements / "requirements.txt").write_text("mcp==1.0.0\n")
        log_path = vault / ".brain" / "local" / "last-upgrade.json"

        def fake_upgrade(
            vault_root,
            source_arg,
            *,
            force=False,
            dry_run=False,
            sync=None,
            sync_deps=None,
            commit_callback=None,
        ):
            assert commit_callback is None
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
                "central_runtime": {"outcome": upgrade.RUNTIME_REUSED},
                "retrieval_asset_repair": {"scope": "lexical", "outcome": "ok", "command": ["repair.py", "lexical"]},
            }
            assert sync_deps is True
            upgrade._write_upgrade_log(vault_root, result)
            return result

        monkeypatch.setattr(upgrade, "upgrade", fake_upgrade)
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "upgrade.py",
                "--source",
                str(source),
                "--vault",
                str(vault),
                "--sync-deps",
                "--json",
            ],
        )

        upgrade.main()

        result = json.loads(capsys.readouterr().out)
        assert result["central_runtime"]["outcome"] == upgrade.RUNTIME_REUSED
        final = json.loads(log_path.read_text())
        assert final["status"] == "ok"
        assert final["central_runtime"]["outcome"] == upgrade.RUNTIME_REUSED

    def test_main_rewrites_log_after_cli_refresh_probe_without_cli_binary(self, tmp_path, monkeypatch, capsys):
        source = _make_real_compile_source(tmp_path)
        vault = _make_minimal_upgrade_vault(tmp_path)
        log_path = vault / ".brain" / "local" / "last-upgrade.json"

        def fake_upgrade(
            vault_root,
            source_arg,
            *,
            force=False,
            dry_run=False,
            sync=None,
            sync_deps=None,
            commit_callback=None,
        ):
            assert commit_callback is None
            assert sync_deps is None
            return {
                "status": "ok",
                "old_version": "0.35.9",
                "new_version": "0.40.0",
                "files_added": [],
                "files_modified": [],
                "files_removed": [],
                "files_unchanged": 0,
                "dry_run": False,
                "message": "Upgraded 0.35.9 → 0.40.0",
                "central_runtime": {"outcome": upgrade.RUNTIME_REUSED},
                "retrieval_asset_repair": {"scope": "lexical", "outcome": "ok", "command": ["repair.py", "lexical"]},
            }

        monkeypatch.setattr(upgrade, "upgrade", fake_upgrade)
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
        assert result["retrieval_asset_repair"]["scope"] == "lexical"
        final = json.loads(log_path.read_text())
        assert final["status"] == "ok"
        assert final["retrieval_asset_repair"]["scope"] == "lexical"

    def test_human_output_quotes_win32_runtime_guidance_with_spaced_paths(self, tmp_path, monkeypatch, capsys):
        source = _make_real_compile_source(tmp_path)
        spaced_root = tmp_path / "spaced root"
        spaced_root.mkdir()
        vault = _make_minimal_upgrade_vault(spaced_root)
        monkeypatch.setattr(upgrade.sys, "platform", "win32")
        monkeypatch.setattr(upgrade.sys, "executable", r"C:\Program Files\Python312\python.exe")

        def fake_upgrade(
            vault_root,
            source_arg,
            *,
            force=False,
            dry_run=False,
            sync=None,
            sync_deps=None,
            commit_callback=None,
        ):
            assert commit_callback is None
            return {
                "status": "ok",
                "old_version": "0.35.9",
                "new_version": "0.40.0",
                "files_added": [],
                "files_modified": [],
                "files_removed": [],
                "files_unchanged": 0,
                "dry_run": False,
                "message": "Upgraded 0.35.9 → 0.40.0",
                "central_runtime": {
                    "outcome": upgrade.RUNTIME_ERROR,
                    "message": "missing mcp",
                    "legacy_vault_venv": str(vault / ".venv"),
                },
                "retrieval_asset_repair": {
                    "scope": "lexical",
                    "outcome": "error",
                    "command": ["python", "repair.py", "lexical", "--vault", str(vault)],
                    "message": "repair failed",
                },
            }

        monkeypatch.setattr(upgrade, "upgrade", fake_upgrade)
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "upgrade.py",
                "--source",
                str(source),
                "--vault",
                str(vault),
            ],
        )

        upgrade.main()

        err = capsys.readouterr().err
        assert '"C:\\Program Files\\Python312\\python.exe"' in err
        assert f'"{vault}"' in err
        assert "Retry:" in err
        assert "configure.py" in err
        assert "repair.py lexical" in err


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
            "case \"$1\" in\n"
            "  *.py)\n"
            f"    exec {shlex.quote(sys.executable)} \"$@\"\n"
            "    ;;\n"
            "esac\n"
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
            env={
                **os.environ,
                "HOME": str(fake_home),
                "BRAIN_VENV_LAUNCHER": str(launcher),
                "BRAIN_SKIP_BOOTSTRAP": "1",
            },
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
        assert "Lexical retrieval state reconciled after upgrade." in result.stderr
        assert str(vault / ".brain-core" / "scripts" / "build_index.py") not in result.stderr

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

        env = {
            **os.environ,
            "HOME": str(fake_home),
            "BRAIN_VENV_LAUNCHER": str(launcher),
            "BRAIN_SKIP_BOOTSTRAP": "1",
        }

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


class TestSyncRenderers:
    def test_updated_entry_names_the_folder_change(self):
        assert upgrade._format_sync_updated({
            "type": "temporal/field-notes", "role": "taxonomy",
            "target": "_Config/Taxonomy/Temporal/field-notes.md",
            "action": "convention", "previous": "_Temporal/Field Notes/yyyy-mm/",
            "folder": "_Temporal/Field Notes/",
        }) == (
            "~ temporal/field-notes / taxonomy → _Config/Taxonomy/Temporal/field-notes.md"
            " (Naming folder _Temporal/Field Notes/yyyy-mm/ → _Temporal/Field Notes/)"
        )

    def test_plain_updated_entry_has_no_detail(self):
        assert upgrade._format_sync_updated({
            "type": "living/docs", "role": "taxonomy",
            "target": "_Config/Taxonomy/Living/docs.md", "action": "update",
        }) == "~ living/docs / taxonomy → _Config/Taxonomy/Living/docs.md"

    def test_warning_entry_prefers_reason_then_action(self):
        base = {"type": "t/x", "role": "taxonomy", "target": "_Config/Taxonomy/Temporal/x.md"}
        assert upgrade._format_sync_warning({**base, "action": "conflict"}).endswith("(conflict)")
        assert upgrade._format_sync_warning(
            {**base, "action": "convention", "reason": "update it by hand"}
        ).endswith("(update it by hand)")

    def test_renderers_match_sync_definitions(self):
        import sync_definitions

        item = {
            "type": "t/x", "role": "taxonomy", "target": "_Config/Taxonomy/Temporal/x.md",
            "action": "convention", "previous": "a/yyyy-mm/", "folder": "a/", "reason": "why",
        }
        assert upgrade._format_sync_updated(item) == sync_definitions.format_sync_updated(item)
        assert upgrade._format_sync_warning(item) == sync_definitions.format_sync_warning(item)
        error = {"type": "t/x", "role": "taxonomy", "error": "boom"}
        assert upgrade._format_sync_error(error) == sync_definitions.format_sync_error(error)
