"""The 0.71.0 configuration migration drops the retired workspace grants (DD-083).

The upgrade runner skips this migration on ``dev`` while ``VERSION`` is below
0.71.0, so these tests apply the module directly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import config as brain_config
from _application.registry import current_application_catalogue
from _command_interface.authorisation_config import resolve_authorisation_config
from _command_interface.profiles import builtin_profile_allow_lists
from _common._yaml import dump_yaml_text, load_mapping_file
import migrate_to_0_71_0

RETIRED = ["workspace.bind", "workspace.register"]
CONFIGURATION_MIGRATIONS = {f"migrate_to_{version}" for version in (
    "0_55_0", "0_56_0", "0_57_0", "0_59_0", "0_62_9", "0_68_0", "0_71_0")}


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_yaml_text(data), encoding="utf-8")


def _resolve(vault: Path):
    return resolve_authorisation_config(vault_root=vault, catalogue=current_application_catalogue(), operator_key=None)


@pytest.fixture
def builtins():
    return builtin_profile_allow_lists(current_application_catalogue())


def test_retired_grants_are_removed_from_every_setting_in_both_files(tmp_path, builtins):
    shared = tmp_path / ".brain" / "config.yaml"
    local = tmp_path / ".brain" / "local" / "config.yaml"
    _write(shared, {
        "vault": {"profiles": {
            "operator": {"allow": sorted([*builtins["operator"], *RETIRED])},
            "administrator": {"label": "administrator profile", "allow": sorted([*builtins["administrator"], *RETIRED])},
        }},
        "defaults": {"access": {"overrides": {"workspace.register": True, "workspace.read": False}}},
    })
    local_profiles = {"operator": {"allow": ["workspace.bind"]}}
    _write(local, {"vault": {"profiles": local_profiles},
                   "defaults": {"access": {"initial": {"mode": "explicit", "commands": ["workspace.bind", "workspace.list"]},
                                           "overrides": {"workspace.bind": False}}}})
    with pytest.warns(UserWarning, match=r"unknown tool 'workspace\.(bind|register)'|vault' keys which are ignored"), \
            pytest.raises(brain_config.ConfigError, match="unknown commands"):
        _resolve(tmp_path)

    result = migrate_to_0_71_0.migrate(str(tmp_path))

    assert result == {
        "status": "ok",
        "profiles": ["administrator", "operator"],
        "settings": [
            ".brain/config.yaml: vault.profiles.operator.allow",
            ".brain/config.yaml: vault.profiles.administrator.allow",
            ".brain/config.yaml: defaults.access.overrides",
            ".brain/local/config.yaml: defaults.access.initial.commands",
            ".brain/local/config.yaml: defaults.access.overrides",
        ],
    }
    migrated = load_mapping_file(shared)
    assert migrated["vault"]["profiles"]["operator"]["allow"] == list(builtins["operator"])
    assert migrated["vault"]["profiles"]["administrator"] == {"label": "administrator profile",
                                                              "allow": list(builtins["administrator"])}
    assert migrated["defaults"]["access"] == {"overrides": {"workspace.read": False}}
    # The local vault zone is never merged, so its profiles are left as written.
    assert load_mapping_file(local) == {"vault": {"profiles": local_profiles},
                                        "defaults": {"access": {"initial": {"mode": "explicit", "commands": ["workspace.list"]},
                                                                "overrides": {}}}}
    with pytest.warns(UserWarning, match="vault' keys which are ignored"):
        assert _resolve(tmp_path).profile == "operator"
    assert migrate_to_0_71_0.migrate(str(tmp_path)) == {"status": "skipped", "profiles": [], "settings": []}


def test_a_non_string_grant_is_left_for_the_loader_to_diagnose(tmp_path):
    shared = tmp_path / ".brain" / "config.yaml"
    _write(shared, {"vault": {"profiles": {"custom": {"allow": [{"odd": 1}, "workspace.bind"]}}}})

    assert migrate_to_0_71_0.migrate(str(tmp_path))["profiles"] == ["custom"]
    assert load_mapping_file(shared)["vault"]["profiles"]["custom"]["allow"] == [{"odd": 1}]


def test_a_configuration_without_retired_grants_is_left_byte_identical(tmp_path):
    shared = tmp_path / ".brain" / "config.yaml"
    _write(shared, {"vault": {"profiles": {"reader": {"allow": ["artefact.read"]}}},
                    "defaults": {"access": {"overrides": {"workspace.list": True}}}})
    before = shared.read_bytes()

    assert migrate_to_0_71_0.migrate(str(tmp_path)) == {"status": "skipped", "profiles": [], "settings": []}
    assert shared.read_bytes() == before


def test_a_vault_without_configuration_files_is_skipped(tmp_path):
    assert migrate_to_0_71_0.migrate(str(tmp_path)) == {"status": "skipped", "profiles": [], "settings": []}
    assert not (tmp_path / ".brain").exists()


def test_the_migration_is_versioned_for_the_release_that_retires_the_commands():
    assert migrate_to_0_71_0.VERSION == "0.71.0"
    assert Path(migrate_to_0_71_0.__file__).name == "migrate_to_0_71_0.py"


def _upgrade_fixture(tmp_path, old_version, *, labelled_administrator=False):
    """A vault at ``old_version`` whose stored profiles still grant the retired IDs.

    The source carries, at 0.71.0, every real migration that reads or writes the
    authorisation configuration, so the chain under test is the one that decides
    whether a stored grant survives. ``HOME`` is isolated by the caller.
    """
    import shutil

    from test_upgrade_migrations import _REAL_SCRIPTS, _make_source, _make_vault

    migrations = {path.name: path.read_text(encoding="utf-8")
                  for path in sorted((_REAL_SCRIPTS / "migrations").glob("migrate_to_*.py"))
                  if path.stem in CONFIGURATION_MIGRATIONS}
    source = _make_source(tmp_path, "0.71.0", migrations=migrations)
    shutil.copytree(_REAL_SCRIPTS.parent / "defaults", source / "defaults")
    vault = _make_vault(tmp_path, old_version)
    builtins = builtin_profile_allow_lists(current_application_catalogue())
    profiles = {"operator": {"allow": sorted([*builtins["operator"], *RETIRED])}}
    if labelled_administrator:
        # 0.55.0 labels the profiles it writes, so 0.68.0's exact-template replacement skips them.
        profiles["administrator"] = {"label": "administrator profile",
                                     "allow": sorted([*builtins["administrator"], *RETIRED])}
    _write(vault / ".brain" / "config.yaml", {"vault": {"profiles": profiles}})
    return source, vault, builtins


@pytest.mark.parametrize("old_version", ["0.62.5", "0.58.0", "0.70.10"])
def test_upgrades_through_the_real_migration_chain_drop_the_retired_grants(tmp_path, fake_home, old_version):
    import upgrade

    source, vault, builtins = _upgrade_fixture(tmp_path, old_version, labelled_administrator=True)

    result = upgrade.upgrade(str(vault), str(source), sync=False, sync_deps=False)

    assert result["status"] == "ok", result.get("message", result)
    assert "0.71.0" in [entry["version"] for entry in result["migrations"]]
    profiles = load_mapping_file(vault / ".brain" / "config.yaml")["vault"]["profiles"]
    for name in ("operator", "administrator"):
        assert not set(profiles[name]["allow"]) & set(RETIRED), name
        assert set(profiles[name]["allow"]) <= set(builtins[name]), name
    assert _resolve(vault).profile == "operator"


def test_a_forced_same_version_re_apply_runs_no_migration(tmp_path, fake_home):
    """Force re-applies the core; it never replays a recorded migration (DD-084)."""
    import upgrade

    source, vault, _builtins = _upgrade_fixture(tmp_path, "0.70.10", labelled_administrator=True)
    first = upgrade.upgrade(str(vault), str(source), sync=False, sync_deps=False)
    assert first["status"] == "ok", first.get("message", first)
    ledger_before = (vault / ".brain" / "local" / "migrations.json").read_bytes()
    config_before = (vault / ".brain" / "config.yaml").read_bytes()

    again = upgrade.upgrade(str(vault), str(source), force=True, sync=False, sync_deps=False)

    assert again["status"] == "ok", again.get("message", again)
    assert again["old_version"] == again["new_version"] == "0.71.0"
    assert "migrations" not in again
    assert "precompile_patch_migrations" not in again
    assert (vault / ".brain" / "local" / "migrations.json").read_bytes() == ledger_before
    assert (vault / ".brain" / "config.yaml").read_bytes() == config_before


def test_a_direct_migration_records_nothing_in_the_ledger(tmp_path):
    """Only upgrade.py writes the ledger, so a dev or lab application leaves no record (D21)."""
    shared = tmp_path / ".brain" / "config.yaml"
    _write(shared, {"vault": {"profiles": {"custom": {"allow": ["artefact.read", "workspace.bind"]}}}})
    ledger = tmp_path / ".brain" / "local" / "migrations.json"
    ledger.parent.mkdir(parents=True)
    ledger.write_text('{"schema_version": 1, "migrations": {}}\n', encoding="utf-8")

    assert migrate_to_0_71_0.migrate(str(tmp_path))["status"] == "ok"

    assert ledger.read_text(encoding="utf-8") == '{"schema_version": 1, "migrations": {}}\n'


def test_the_chain_fixture_carries_every_configuration_migration():
    """A migration that touches profiles or authorisation must join the chain under test."""
    from test_upgrade_migrations import _REAL_SCRIPTS

    touching = {path.stem for path in (_REAL_SCRIPTS / "migrations").glob("migrate_to_*.py")
                if any(name in path.read_text(encoding="utf-8")
                       for name in ("profile_migration", "authorisation_migration", "RETIRED_COMMANDS"))}
    assert touching <= CONFIGURATION_MIGRATIONS
