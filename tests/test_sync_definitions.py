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
            json.dumps({"artefact_sync_exclude": ["temporal/cookies/taxonomy"]})
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

    def test_preference_manual(self, vault):
        """artefact_sync: manual → all changes go to warnings."""
        _install_type(vault)
        lib_tax = vault / ".brain-core" / "artefact-library" / "temporal" / "cookies" / "taxonomy.md"
        lib_tax.write_text("# Changed\n")
        (vault / ".brain" / "preferences.json").write_text(
            json.dumps({"artefact_sync": "manual"})
        )
        result = sync.sync_definitions(str(vault))
        # The update should be in warnings, not auto-applied
        assert any(w["role"] == "taxonomy" for w in result["warnings"])
        assert not any(u["role"] == "taxonomy" for u in result["updated"])

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
