"""Tests for sync_definitions.py — artefact library definition sync."""

import json
import os
import shutil

import pytest

import sync_definitions as sync


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MANIFEST_TAXONOMY_TEMPLATE = """\
files:
  taxonomy:
    source: taxonomy.md
    target: _Config/Taxonomy/Temporal/cookies.md
  template:
    source: template.md
    target: _Config/Templates/Temporal/Cookies.md
folders:
  - _Temporal/Cookies/
"""

MANIFEST_WITH_SKILL = """\
files:
  taxonomy:
    source: taxonomy.md
    target: _Config/Taxonomy/Temporal/cookies.md
  template:
    source: template.md
    target: _Config/Templates/Temporal/Cookies.md
  skill:
    source: SKILL.md
    target: _Config/Skills/cookies/SKILL.md
folders:
  - _Temporal/Cookies/
router_trigger: "After completing work → [[_Config/Taxonomy/Temporal/cookies]]"
"""


def _write(path, content="placeholder\n"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _hash(path):
    from compile_router import hash_file
    return hash_file(path)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def vault(tmp_path):
    """Minimal vault with one library type (temporal/cookies)."""
    # brain-core
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("1.0.0\n")
    (bc / "session-core.md").write_text("# Session Core\n")

    # Library type: temporal/cookies
    lib = bc / "artefact-library" / "temporal" / "cookies"
    lib.mkdir(parents=True)
    (lib / "manifest.yaml").write_text(MANIFEST_TAXONOMY_TEMPLATE)
    (lib / "taxonomy.md").write_text("# Cookies taxonomy\n")
    (lib / "template.md").write_text("# Cookie template\n")

    # .brain directory with seed files
    brain = tmp_path / ".brain"
    brain.mkdir()
    (brain / "tracking.json").write_text(json.dumps(
        {"schema_version": 1, "installed": {}}, indent=2
    ))
    (brain / "preferences.json").write_text("{}")

    # _Config structure
    (tmp_path / "_Config" / "Taxonomy" / "Temporal").mkdir(parents=True)
    (tmp_path / "_Config" / "Templates" / "Temporal").mkdir(parents=True)

    return tmp_path


@pytest.fixture
def vault_two_types(vault):
    """Vault with two library types: temporal/cookies and living/wiki."""
    lib = vault / ".brain-core" / "artefact-library" / "living" / "wiki"
    lib.mkdir(parents=True)
    (lib / "manifest.yaml").write_text(
        "files:\n"
        "  taxonomy:\n"
        "    source: taxonomy.md\n"
        "    target: _Config/Taxonomy/Living/wiki.md\n"
        "  template:\n"
        "    source: template.md\n"
        "    target: _Config/Templates/Living/Wiki.md\n"
        "folders:\n"
        "  - Wiki/\n"
    )
    (lib / "taxonomy.md").write_text("# Wiki taxonomy\n")
    (lib / "template.md").write_text("# Wiki template\n")
    (vault / "_Config" / "Taxonomy" / "Living").mkdir(parents=True, exist_ok=True)
    (vault / "_Config" / "Templates" / "Living").mkdir(parents=True, exist_ok=True)
    return vault


def _install_type(vault, type_key="temporal/cookies"):
    """Simulate a fully-installed type: copy library files to targets and record tracking."""
    types = sync.discover_library_types(str(vault))
    info = next(t for t in types if t["type_key"] == type_key)
    manifest = info["manifest"]
    tracking = sync.load_tracking(str(vault))

    files_tracking = {}
    for role, file_info in manifest["files"].items():
        src = os.path.join(info["library_dir"], file_info["source"])
        dst = os.path.join(str(vault), file_info["target"])
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        files_tracking[role] = {
            "source_hash": _hash(src),
            "target": file_info["target"],
        }

    tracking["installed"][type_key] = {
        "brain_core_version": "1.0.0",
        "installed_at": "2026-01-01T00:00:00+00:00",
        "files": files_tracking,
    }
    sync.save_tracking(str(vault), tracking)


# ---------------------------------------------------------------------------
# parse_manifest
# ---------------------------------------------------------------------------

class TestParseManifest:
    def test_valid_manifest(self, tmp_path):
        path = str(tmp_path / "manifest.yaml")
        _write(path, MANIFEST_TAXONOMY_TEMPLATE)
        result = sync.parse_manifest(path)
        assert result is not None
        assert "taxonomy" in result["files"]
        assert result["files"]["taxonomy"]["source"] == "taxonomy.md"
        assert result["files"]["taxonomy"]["target"] == "_Config/Taxonomy/Temporal/cookies.md"
        assert "_Temporal/Cookies/" in result["folders"]

    def test_manifest_with_skill_and_trigger(self, tmp_path):
        path = str(tmp_path / "manifest.yaml")
        _write(path, MANIFEST_WITH_SKILL)
        result = sync.parse_manifest(path)
        assert "skill" in result["files"]
        assert result["files"]["skill"]["source"] == "SKILL.md"
        assert "router_trigger" in result

    def test_nonexistent_file(self):
        assert sync.parse_manifest("/nonexistent/manifest.yaml") is None

    def test_empty_file(self, tmp_path):
        path = str(tmp_path / "manifest.yaml")
        _write(path, "")
        assert sync.parse_manifest(path) is None

    def test_no_files_section(self, tmp_path):
        path = str(tmp_path / "manifest.yaml")
        _write(path, "folders:\n  - Foo/\n")
        assert sync.parse_manifest(path) is None


# ---------------------------------------------------------------------------
# discover_library_types
# ---------------------------------------------------------------------------

class TestDiscoverLibraryTypes:
    def test_discovers_types(self, vault):
        types = sync.discover_library_types(str(vault))
        keys = [t["type_key"] for t in types]
        assert "temporal/cookies" in keys

    def test_skips_dirs_without_manifest(self, vault):
        # Add a dir with no manifest
        no_manifest = vault / ".brain-core" / "artefact-library" / "temporal" / "empty"
        no_manifest.mkdir(parents=True)
        types = sync.discover_library_types(str(vault))
        keys = [t["type_key"] for t in types]
        assert "temporal/empty" not in keys

    def test_discovers_multiple_classifications(self, vault_two_types):
        types = sync.discover_library_types(str(vault_two_types))
        keys = [t["type_key"] for t in types]
        assert "temporal/cookies" in keys
        assert "living/wiki" in keys


# ---------------------------------------------------------------------------
# Tracking and preferences I/O
# ---------------------------------------------------------------------------

class TestTrackingIO:
    def test_load_seed(self, vault):
        tracking = sync.load_tracking(str(vault))
        assert tracking["schema_version"] == 1
        assert tracking["installed"] == {}

    def test_save_and_load(self, vault):
        tracking = sync.load_tracking(str(vault))
        tracking["installed"]["test/type"] = {"brain_core_version": "1.0.0", "files": {}}
        sync.save_tracking(str(vault), tracking)
        reloaded = sync.load_tracking(str(vault))
        assert "test/type" in reloaded["installed"]

    def test_load_missing_file(self, tmp_path):
        tracking = sync.load_tracking(str(tmp_path))
        assert tracking == {"schema_version": 1, "installed": {}}


class TestPreferences:
    def test_load_empty(self, vault):
        prefs = sync.load_preferences(str(vault))
        assert prefs == {}

    def test_load_with_sync_pref(self, vault):
        path = vault / ".brain" / "preferences.json"
        path.write_text(json.dumps({"artefact_sync": "manual"}))
        prefs = sync.load_preferences(str(vault))
        assert prefs["artefact_sync"] == "manual"

    def test_load_missing(self, tmp_path):
        assert sync.load_preferences(str(tmp_path)) == {}


# ---------------------------------------------------------------------------
# compute_file_status
# ---------------------------------------------------------------------------

class TestComputeFileStatus:
    def test_new_no_target(self, vault):
        src = str(vault / ".brain-core" / "artefact-library" / "temporal" / "cookies" / "taxonomy.md")
        dst = str(vault / "_Config" / "Taxonomy" / "Temporal" / "cookies.md")
        status = sync.compute_file_status(src, None, dst)
        assert status["action"] == "new"

    def test_baseline_match(self, vault):
        src = str(vault / ".brain-core" / "artefact-library" / "temporal" / "cookies" / "taxonomy.md")
        dst = str(vault / "_Config" / "Taxonomy" / "Temporal" / "cookies.md")
        shutil.copy2(src, dst)
        status = sync.compute_file_status(src, None, dst)
        assert status["action"] == "baseline"

    def test_collision_different(self, vault):
        src = str(vault / ".brain-core" / "artefact-library" / "temporal" / "cookies" / "taxonomy.md")
        dst = str(vault / "_Config" / "Taxonomy" / "Temporal" / "cookies.md")
        _write(dst, "different content\n")
        status = sync.compute_file_status(src, None, dst)
        assert status["action"] == "collision"

    def test_skip_in_sync(self, vault):
        src = str(vault / ".brain-core" / "artefact-library" / "temporal" / "cookies" / "taxonomy.md")
        dst = str(vault / "_Config" / "Taxonomy" / "Temporal" / "cookies.md")
        shutil.copy2(src, dst)
        h = _hash(src)
        entry = {"source_hash": h, "target": "..."}
        status = sync.compute_file_status(src, entry, dst)
        assert status["action"] == "skip"

    def test_skip_user_customised(self, vault):
        src = str(vault / ".brain-core" / "artefact-library" / "temporal" / "cookies" / "taxonomy.md")
        dst = str(vault / "_Config" / "Taxonomy" / "Temporal" / "cookies.md")
        _write(dst, "user modified\n")
        h = _hash(src)
        entry = {"source_hash": h, "target": "..."}
        status = sync.compute_file_status(src, entry, dst)
        assert status["action"] == "skip"

    def test_update_upstream_changed(self, vault):
        src = str(vault / ".brain-core" / "artefact-library" / "temporal" / "cookies" / "taxonomy.md")
        dst = str(vault / "_Config" / "Taxonomy" / "Temporal" / "cookies.md")
        # Install original
        _write(src, "original\n")
        shutil.copy2(src, dst)
        old_hash = _hash(src)
        entry = {"source_hash": old_hash, "target": "..."}
        # Upstream changes
        _write(src, "updated upstream\n")
        status = sync.compute_file_status(src, entry, dst)
        assert status["action"] == "update"

    def test_conflict_both_changed(self, vault):
        src = str(vault / ".brain-core" / "artefact-library" / "temporal" / "cookies" / "taxonomy.md")
        dst = str(vault / "_Config" / "Taxonomy" / "Temporal" / "cookies.md")
        _write(src, "original\n")
        shutil.copy2(src, dst)
        old_hash = _hash(src)
        entry = {"source_hash": old_hash, "target": "..."}
        _write(src, "upstream changed\n")
        _write(dst, "locally changed\n")
        status = sync.compute_file_status(src, entry, dst)
        assert status["action"] == "conflict"


# ---------------------------------------------------------------------------
# sync_definitions — full integration
# ---------------------------------------------------------------------------

class TestSyncDefinitions:
    def test_uninstalled_type_silently_skipped(self, vault):
        """Empty tracking, targets don't exist → type silently skipped."""
        result = sync.sync_definitions(str(vault))
        assert result["status"] == "ok"
        assert len(result["warnings"]) == 0
        assert len(result["updated"]) == 0
        assert len(result["skipped"]) == 0

    def test_fresh_install_matching_baseline(self, vault):
        """Targets exist matching upstream → baseline tracking established silently."""
        lib = vault / ".brain-core" / "artefact-library" / "temporal" / "cookies"
        shutil.copy2(
            str(lib / "taxonomy.md"),
            str(vault / "_Config" / "Taxonomy" / "Temporal" / "cookies.md"),
        )
        shutil.copy2(
            str(lib / "template.md"),
            str(vault / "_Config" / "Templates" / "Temporal" / "Cookies.md"),
        )
        result = sync.sync_definitions(str(vault))
        assert result["status"] == "ok"
        assert len(result["warnings"]) == 0
        reasons = {s["role"]: s["reason"] for s in result["skipped"]}
        assert reasons["taxonomy"] == "baseline_established"

        # Tracking should now have entries
        tracking = sync.load_tracking(str(vault))
        assert "temporal/cookies" in tracking["installed"]
        assert "taxonomy" in tracking["installed"]["temporal/cookies"]["files"]

    def test_in_sync_skip(self, vault):
        """Fully installed and unchanged → all skip."""
        _install_type(vault)
        result = sync.sync_definitions(str(vault))
        assert result["status"] == "ok"
        assert len(result["updated"]) == 0
        assert len(result["warnings"]) == 0
        assert all(s["reason"] == "in_sync" for s in result["skipped"])

    def test_user_customised_skip(self, vault):
        """Upstream unchanged, local changed → skip (user customised)."""
        _install_type(vault)
        _write(str(vault / "_Config" / "Taxonomy" / "Temporal" / "cookies.md"), "my custom content\n")
        result = sync.sync_definitions(str(vault))
        assert result["status"] == "ok"
        taxonomy_skip = next(s for s in result["skipped"] if s["role"] == "taxonomy")
        assert taxonomy_skip["reason"] == "user_customised"

    def test_auto_update(self, vault):
        """Upstream changed, local matches installed → auto-update."""
        _install_type(vault)
        (vault / ".brain" / "preferences.json").write_text(
            json.dumps({"artefact_sync": "auto"})
        )
        # Change upstream
        lib_tax = vault / ".brain-core" / "artefact-library" / "temporal" / "cookies" / "taxonomy.md"
        lib_tax.write_text("# Updated cookies taxonomy\n")
        result = sync.sync_definitions(str(vault))
        assert result["status"] == "ok"
        taxonomy_update = next(u for u in result["updated"] if u["role"] == "taxonomy")
        assert taxonomy_update["action"] == "update"
        # Verify file was actually updated
        assert _read(str(vault / "_Config" / "Taxonomy" / "Temporal" / "cookies.md")) == "# Updated cookies taxonomy\n"

    def test_conflict_warning(self, vault):
        """Both upstream and local changed → conflict warning."""
        _install_type(vault)
        lib_tax = vault / ".brain-core" / "artefact-library" / "temporal" / "cookies" / "taxonomy.md"
        lib_tax.write_text("# Upstream change\n")
        _write(str(vault / "_Config" / "Taxonomy" / "Temporal" / "cookies.md"), "# Local change\n")
        result = sync.sync_definitions(str(vault))
        assert result["status"] == "warnings"
        conflict = next(w for w in result["warnings"] if w["role"] == "taxonomy")
        assert conflict["action"] == "conflict"

    def test_collision_warning(self, vault):
        """No tracking, target exists with different content → collision warning."""
        _write(str(vault / "_Config" / "Taxonomy" / "Temporal" / "cookies.md"), "user created\n")
        result = sync.sync_definitions(str(vault))
        collisions = [w for w in result["warnings"] if w["action"] == "collision"]
        assert len(collisions) >= 1

    def test_excluded_skip(self, vault):
        """Entry in artefact_sync_exclude → skipped."""
        _install_type(vault)
        (vault / ".brain" / "preferences.json").write_text(
            json.dumps({"artefact_sync_exclude": ["temporal/cookies/taxonomy"]})
        )
        lib_tax = vault / ".brain-core" / "artefact-library" / "temporal" / "cookies" / "taxonomy.md"
        lib_tax.write_text("# Changed\n")
        result = sync.sync_definitions(str(vault))
        excluded = next(s for s in result["skipped"] if s["role"] == "taxonomy")
        assert excluded["reason"] == "excluded"

    def test_exclude_does_not_affect_other_roles(self, vault):
        """Excluding one role doesn't exclude another in an installed type."""
        _install_type(vault)
        # Change upstream for both files
        lib = vault / ".brain-core" / "artefact-library" / "temporal" / "cookies"
        lib_tax = lib / "taxonomy.md"
        lib_tmpl = lib / "template.md"
        lib_tax.write_text("# Changed taxonomy\n")
        lib_tmpl.write_text("# Changed template\n")
        (vault / ".brain" / "preferences.json").write_text(
            json.dumps({"artefact_sync": "auto", "artefact_sync_exclude": ["temporal/cookies/taxonomy"]})
        )
        result = sync.sync_definitions(str(vault))
        # taxonomy excluded → skipped; template should auto-update
        taxonomy_skip = next(s for s in result["skipped"] if s["role"] == "taxonomy")
        assert taxonomy_skip["reason"] == "excluded"
        template_update = next(u for u in result["updated"] if u["role"] == "template")
        assert template_update["action"] == "update"

    def test_force_overwrites_conflict(self, vault):
        """force=True + conflict → file updated."""
        _install_type(vault)
        lib_tax = vault / ".brain-core" / "artefact-library" / "temporal" / "cookies" / "taxonomy.md"
        lib_tax.write_text("# Upstream change\n")
        _write(str(vault / "_Config" / "Taxonomy" / "Temporal" / "cookies.md"), "# Local change\n")
        result = sync.sync_definitions(str(vault), force=True)
        assert result["status"] == "ok"
        assert len(result["warnings"]) == 0
        tax_update = next(u for u in result["updated"] if u["role"] == "taxonomy")
        assert tax_update["action"] == "conflict"
        assert _read(str(vault / "_Config" / "Taxonomy" / "Temporal" / "cookies.md")) == "# Upstream change\n"

    def test_force_overwrites_collision(self, vault):
        """force=True + collision → file updated."""
        _write(str(vault / "_Config" / "Taxonomy" / "Temporal" / "cookies.md"), "user created\n")
        result = sync.sync_definitions(str(vault), force=True)
        assert result["status"] == "ok"
        assert len(result["warnings"]) == 0
        assert any(u["action"] == "collision" for u in result["updated"])

    def test_preference_skip(self, vault):
        """artefact_sync: skip → return immediately."""
        (vault / ".brain" / "preferences.json").write_text(
            json.dumps({"artefact_sync": "skip"})
        )
        result = sync.sync_definitions(str(vault))
        assert result["status"] == "skipped"

    def test_default_preference_is_ask(self, vault):
        """Default preference (no artefact_sync set) auto-applies safe updates."""
        _install_type(vault)
        lib_tax = vault / ".brain-core" / "artefact-library" / "temporal" / "cookies" / "taxonomy.md"
        lib_tax.write_text("# Changed\n")
        # preferences.json is empty — no artefact_sync key
        result = sync.sync_definitions(str(vault))
        # Safe update (no local changes) should auto-apply
        assert any(u["role"] == "taxonomy" for u in result["updated"])
        assert not any(w["role"] == "taxonomy" for w in result["warnings"])

    def test_preference_ask(self, vault):
        """artefact_sync: ask → safe updates auto-apply, conflicts warn."""
        _install_type(vault)
        lib_tax = vault / ".brain-core" / "artefact-library" / "temporal" / "cookies" / "taxonomy.md"
        lib_tax.write_text("# Changed\n")
        (vault / ".brain" / "preferences.json").write_text(
            json.dumps({"artefact_sync": "ask"})
        )
        result = sync.sync_definitions(str(vault))
        # Safe update (no local changes) should auto-apply
        assert any(u["role"] == "taxonomy" for u in result["updated"])
        assert not any(w["role"] == "taxonomy" for w in result["warnings"])

    def test_preference_non_auto_applies_safe_updates(self, vault):
        """artefact_sync: any non-skip value → safe updates auto-apply."""
        _install_type(vault)
        lib_tax = vault / ".brain-core" / "artefact-library" / "temporal" / "cookies" / "taxonomy.md"
        lib_tax.write_text("# Changed\n")
        (vault / ".brain" / "preferences.json").write_text(
            json.dumps({"artefact_sync": "manual"})
        )
        result = sync.sync_definitions(str(vault))
        # Safe update (no local changes) should auto-apply regardless of preference
        assert any(u["role"] == "taxonomy" for u in result["updated"])
        assert not any(w["role"] == "taxonomy" for w in result["warnings"])

    def test_dry_run(self, vault):
        """dry_run=True → no files or tracking modified."""
        result = sync.sync_definitions(str(vault), dry_run=True)
        assert result["dry_run"] is True
        # Tracking should still be empty
        tracking = sync.load_tracking(str(vault))
        assert tracking["installed"] == {}

    def test_idempotent(self, vault):
        """Running sync twice on an in-sync vault → second run is all skips."""
        _install_type(vault)
        result1 = sync.sync_definitions(str(vault))
        assert result1["status"] == "ok"
        result2 = sync.sync_definitions(str(vault))
        assert result2["status"] == "ok"
        assert len(result2["updated"]) == 0
        assert len(result2["warnings"]) == 0

    def test_type_filter(self, vault_two_types):
        """types= parameter filters to specified types only."""
        # Install both types first so they're not skipped as uninstalled
        _install_type(vault_two_types, "temporal/cookies")
        _install_type(vault_two_types, "living/wiki")
        # Change upstream for wiki
        lib = vault_two_types / ".brain-core" / "artefact-library" / "living" / "wiki"
        (lib / "taxonomy.md").write_text("# Updated wiki taxonomy\n")
        result = sync.sync_definitions(
            str(vault_two_types), types=["living/wiki"]
        )
        all_types = set()
        for item in result["warnings"] + result["updated"] + result["skipped"]:
            all_types.add(item["type"])
        assert all_types == {"living/wiki"}

    def test_folders_created_for_installed_type(self, vault):
        """Manifest folders are created for installed types."""
        _install_type(vault)
        folder = vault / "_Temporal" / "Cookies"
        assert not folder.exists()
        sync.sync_definitions(str(vault))
        assert folder.is_dir()

    def test_folders_not_created_for_uninstalled_type(self, vault):
        """Manifest folders are NOT created for uninstalled types."""
        folder = vault / "_Temporal" / "Cookies"
        assert not folder.exists()
        sync.sync_definitions(str(vault))
        assert not folder.exists()

    def test_preference_override(self, vault):
        """preference parameter overrides file-based preference."""
        _install_type(vault)
        lib_tax = vault / ".brain-core" / "artefact-library" / "temporal" / "cookies" / "taxonomy.md"
        lib_tax.write_text("# Changed\n")
        # File says "skip" but caller overrides to "auto"
        (vault / ".brain" / "preferences.json").write_text(
            json.dumps({"artefact_sync": "skip"})
        )
        result = sync.sync_definitions(str(vault), preference="auto")
        assert result["status"] == "ok"
        assert any(u["role"] == "taxonomy" for u in result["updated"])

    def test_missing_manifest_graceful(self, vault):
        """Type dir without manifest.yaml is silently skipped."""
        no_manifest = vault / ".brain-core" / "artefact-library" / "temporal" / "empty"
        no_manifest.mkdir(parents=True)
        result = sync.sync_definitions(str(vault))
        types_seen = {w["type"] for w in result["warnings"]}
        assert "temporal/empty" not in types_seen


# ---------------------------------------------------------------------------
# Regression: installed_at stability and skip return completeness
# ---------------------------------------------------------------------------

class TestRegressions:
    def test_installed_at_stable_on_skip(self, vault):
        """installed_at should not change when a type is fully in sync."""
        _install_type(vault)
        tracking = sync.load_tracking(str(vault))
        original_at = tracking["installed"]["temporal/cookies"]["installed_at"]
        sync.sync_definitions(str(vault))
        tracking = sync.load_tracking(str(vault))
        assert tracking["installed"]["temporal/cookies"]["installed_at"] == original_at

    def test_skip_return_has_version(self, vault):
        """The skip return should include brain_core_version."""
        (vault / ".brain" / "preferences.json").write_text(
            json.dumps({"artefact_sync": "skip"})
        )
        result = sync.sync_definitions(str(vault))
        assert "brain_core_version" in result
        assert result["brain_core_version"] == "1.0.0"


# ---------------------------------------------------------------------------
# Template vault integration — catches drift between brain-core and template
# ---------------------------------------------------------------------------

TEMPLATE_VAULT = os.path.join(
    os.path.dirname(__file__), "..", "template-vault"
)


class TestTemplateVaultSync:
    """Verify the template vault's definitions match brain-core.

    If this test fails, run ``make sync-template`` to update the template
    vault, then commit the result.
    """

    @pytest.fixture
    def template_vault(self):
        path = os.path.abspath(TEMPLATE_VAULT)
        if not os.path.isdir(path):
            pytest.skip("template-vault not found")
        if not os.path.isdir(os.path.join(path, ".brain-core")):
            pytest.skip(".brain-core not linked — run 'make dev-link'")
        return path

    def test_no_drift(self, template_vault):
        """All installed template-vault types should classify as in_sync."""
        result = sync.status_definitions(template_vault)
        if result.get("not_installable"):
            problems = "\n  ".join(
                f"{entry.get('type', '?')}: {entry.get('reason', 'unknown')}"
                for entry in result["not_installable"]
            )
            pytest.fail(
                "Template vault contains non-installable library types:\n"
                f"  {problems}"
            )

        drift = []
        for state in ("sync_ready", "locally_customised", "conflict"):
            for entry in result.get("types", {}).get(state, []):
                files = ", ".join(
                    f"{role}={file_state}"
                    for role, file_state in sorted(entry.get("files", {}).items())
                    if file_state != "in_sync"
                )
                drift.append({
                    "type": entry.get("type", "?"),
                    "state": state,
                    "files": files,
                })
        if drift:
            files = "\n  ".join(
                f"[{d['state']}] {d['type']}"
                + (f" ({d['files']})" if d["files"] else "")
                for d in drift
            )
            pytest.fail(
                f"Template vault has {len(drift)} drifted definition(s). "
                f"Run `make sync-template` to fix:\n  {files}"
            )

    def test_default_types_installed(self, template_vault):
        """Every default artefact type should have its files in the vault."""
        library_types = sync.discover_library_types(template_vault)
        for t in library_types:
            manifest = t["manifest"]
            type_key = t["type_key"]
            # Check at least one target file exists
            has_file = any(
                os.path.isfile(
                    os.path.join(template_vault, fi["target"])
                )
                for fi in manifest["files"].values()
            )
            if not has_file:
                continue  # non-default, not installed — fine
            # If installed, ALL files must exist
            for role, fi in manifest["files"].items():
                target = os.path.join(template_vault, fi["target"])
                assert os.path.isfile(target), (
                    f"{type_key}/{role} missing: {fi['target']}"
                )

    def test_taxonomies_keep_vault_native_wikilinks_for_standards(self, template_vault):
        """Artefact taxonomies should not switch standards references to relative markdown links."""
        source_root = os.path.join(
            os.path.dirname(__file__), "..", "src", "brain-core", "artefact-library"
        )
        source_hits = []
        for dirpath, _dirnames, filenames in os.walk(source_root):
            for filename in filenames:
                if filename != "taxonomy.md":
                    continue
                path = os.path.join(dirpath, filename)
                text = _read(path)
                if "](../../../standards/" in text:
                    source_hits.append(os.path.relpath(path))

        template_root = os.path.join(template_vault, "_Config", "Taxonomy")
        template_hits = []
        for dirpath, _dirnames, filenames in os.walk(template_root):
            for filename in filenames:
                if not filename.endswith(".md"):
                    continue
                path = os.path.join(dirpath, filename)
                text = _read(path)
                if "](../../../standards/" in text:
                    template_hits.append(os.path.relpath(path, template_vault))

        assert source_hits == []
        assert template_hits == []


# ---------------------------------------------------------------------------
# Install via --types X
# ---------------------------------------------------------------------------

class TestTypesInstall:
    def test_bare_sync_does_not_install_uninstalled(self, vault):
        """Bare sync (no types filter) preserves the 'never install' invariant."""
        result = sync.sync_definitions(str(vault))
        assert result["status"] == "ok"
        assert result["updated"] == []
        # Target file must not exist
        target = vault / "_Config" / "Taxonomy" / "Temporal" / "cookies.md"
        assert not target.exists()

    def test_types_installs_uninstalled(self, vault):
        """sync_definitions(types=[X]) installs X even if uninstalled."""
        result = sync.sync_definitions(
            str(vault), types=["temporal/cookies"],
        )
        assert result["status"] == "ok"
        # Both files should have been written as "new"
        new_items = [u for u in result["updated"] if u["action"] == "new"]
        assert len(new_items) == 2
        target = vault / "_Config" / "Taxonomy" / "Temporal" / "cookies.md"
        assert target.is_file()
        # Tracking should record the install
        tracking = sync.load_tracking(str(vault))
        assert "temporal/cookies" in tracking["installed"]

    def test_types_install_creates_folders(self, vault):
        """Install via --types X should create manifest folders."""
        assert not (vault / "_Temporal" / "Cookies").exists()
        sync.sync_definitions(str(vault), types=["temporal/cookies"])
        assert (vault / "_Temporal" / "Cookies").is_dir()

    def test_types_install_dry_run(self, vault):
        """Dry-run install leaves the filesystem untouched but reports the action."""
        result = sync.sync_definitions(
            str(vault), types=["temporal/cookies"], dry_run=True,
        )
        assert result["dry_run"] is True
        new_items = [u for u in result["updated"] if u["action"] == "new"]
        assert len(new_items) == 2
        target = vault / "_Config" / "Taxonomy" / "Temporal" / "cookies.md"
        assert not target.exists()

    def test_types_install_does_not_require_force(self, vault):
        """Install is additive — no --force needed when nothing to overwrite."""
        result = sync.sync_definitions(
            str(vault), types=["temporal/cookies"], force=False,
        )
        assert result["status"] == "ok"
        assert len(result["updated"]) == 2

    def test_types_install_then_in_sync(self, vault):
        """A type installed via --types is subsequently reported as in_sync."""
        sync.sync_definitions(str(vault), types=["temporal/cookies"])
        result = sync.sync_definitions(str(vault))
        assert result["updated"] == []
        assert all(s["reason"] == "in_sync" for s in result["skipped"])


# ---------------------------------------------------------------------------
# Status classifier
# ---------------------------------------------------------------------------

class TestStatusDefinitions:
    def test_uninstalled(self, vault):
        """A type with no tracking and no target files is uninstalled."""
        result = sync.status_definitions(str(vault))
        assert result["status"] == "ok"
        uninstalled = [t["type"] for t in result["types"]["uninstalled"]]
        assert "temporal/cookies" in uninstalled

    def test_in_sync(self, vault):
        """Installed type with matching hashes classifies as in_sync."""
        _install_type(vault)
        result = sync.status_definitions(str(vault))
        in_sync = [t["type"] for t in result["types"]["in_sync"]]
        assert "temporal/cookies" in in_sync

    def test_sync_ready_upstream_changed(self, vault):
        """Installed type with library changes classifies as sync_ready."""
        _install_type(vault)
        lib_tax = vault / ".brain-core" / "artefact-library" / "temporal" / "cookies" / "taxonomy.md"
        lib_tax.write_text("# Updated\n")
        result = sync.status_definitions(str(vault))
        ready = [t["type"] for t in result["types"]["sync_ready"]]
        assert "temporal/cookies" in ready

    def test_sync_ready_missing_target(self, vault):
        """Tracked type with a missing target file classifies as sync_ready
        (library has content the vault lacks — indistinguishable from
        upstream adding a new file)."""
        _install_type(vault)
        (vault / "_Config" / "Taxonomy" / "Temporal" / "cookies.md").unlink()
        result = sync.status_definitions(str(vault))
        ready = [t["type"] for t in result["types"]["sync_ready"]]
        assert "temporal/cookies" in ready

    def test_locally_customised(self, vault):
        """Installed type with local edits and unchanged library is
        locally_customised."""
        _install_type(vault)
        target = vault / "_Config" / "Taxonomy" / "Temporal" / "cookies.md"
        target.write_text("# Locally edited\n")
        result = sync.status_definitions(str(vault))
        custom = [t["type"] for t in result["types"]["locally_customised"]]
        assert "temporal/cookies" in custom

    def test_conflict(self, vault):
        """Installed type with both upstream and local changes is conflict."""
        _install_type(vault)
        target = vault / "_Config" / "Taxonomy" / "Temporal" / "cookies.md"
        target.write_text("# Locally edited\n")
        lib_tax = vault / ".brain-core" / "artefact-library" / "temporal" / "cookies" / "taxonomy.md"
        lib_tax.write_text("# Upstream edited\n")
        result = sync.status_definitions(str(vault))
        conflicts = [t["type"] for t in result["types"]["conflict"]]
        assert "temporal/cookies" in conflicts

    def test_not_installable_missing_source(self, vault):
        """A library type whose declared source file is missing appears in
        not_installable, not the state groups."""
        lib_tax = vault / ".brain-core" / "artefact-library" / "temporal" / "cookies" / "taxonomy.md"
        lib_tax.unlink()
        result = sync.status_definitions(str(vault))
        reasons = {t["type"]: t["reason"] for t in result["not_installable"]}
        assert "temporal/cookies" in reasons
        assert "taxonomy.md" in reasons["temporal/cookies"]
        for state in result["types"].values():
            assert "temporal/cookies" not in [t["type"] for t in state]

    def test_type_filter(self, vault_two_types):
        """types= parameter restricts status output to named types."""
        result = sync.status_definitions(
            str(vault_two_types), types=["living/wiki"],
        )
        all_listed = []
        for group in result["types"].values():
            all_listed.extend(t["type"] for t in group)
        all_listed.extend(t["type"] for t in result["not_installable"])
        assert all_listed == ["living/wiki"]

    def test_status_is_readonly(self, vault):
        """Calling status_definitions does not modify tracking or files."""
        before_tracking = (vault / ".brain" / "tracking.json").read_text()
        sync.status_definitions(str(vault))
        sync.status_definitions(str(vault), types=["temporal/cookies"])
        after_tracking = (vault / ".brain" / "tracking.json").read_text()
        assert before_tracking == after_tracking
        target = vault / "_Config" / "Taxonomy" / "Temporal" / "cookies.md"
        assert not target.exists()
