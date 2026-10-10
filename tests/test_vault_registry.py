"""Unit tests for vault_registry.py."""

import builtins

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import vault_registry  # sys.path is set up by conftest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "src" / "brain-core" / "scripts" / "vault_registry.py"


@pytest.fixture
def registry_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return tmp_path


@pytest.fixture
def registry_dir(registry_home):
    """Parent dir of the registry file — ensures it exists for direct writes."""
    directory = registry_home / ".config" / "brain"
    directory.mkdir(parents=True)
    return directory


def _brain(path: Path) -> Path:
    """An installed Brain (``.brain-core/VERSION``) at ``path``, the only thing registration accepts."""
    (path / ".brain-core").mkdir(parents=True, exist_ok=True)
    (path / ".brain-core" / "VERSION").write_text("0.70.10\n")
    return path.resolve()


def _add_row(brain_id, path):
    """Store a row as an older registry or a later move left it; registration only stores installed Brains."""
    vault_registry._save_registry_entries({
        **vault_registry.load_registry_entries(),
        brain_id: vault_registry.RegistryEntry(brain_id, vault_registry.TYPE_LOCAL, str(path)),
    })


def _local_entries():
    return {
        brain_id: entry.value
        for brain_id, entry in vault_registry.load_registry_entries().items()
        if entry.kind == vault_registry.TYPE_LOCAL
    }


def _save_local_entries(entries):
    vault_registry._save_registry_entries(
        {
            brain_id: vault_registry.RegistryEntry(
                brain_id=brain_id,
                kind=vault_registry.TYPE_LOCAL,
                value=path,
            )
            for brain_id, path in entries.items()
        }
    )


def _run_cli(registry_home, *args, check=True):
    env = os.environ.copy()
    env["HOME"] = str(registry_home)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        env=env,
        capture_output=True,
        text=True,
        check=check,
    )


def test_registry_path_defaults_to_config_brain(registry_home):
    assert vault_registry._registry_path() == str(registry_home / ".config" / "brain" / "vaults")


def test_registry_path_respects_xdg_config_home(registry_home, monkeypatch, tmp_path):
    xdg = tmp_path / "custom-config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    assert vault_registry._registry_path() == str(xdg / "brain" / "vaults")


def test_registry_path_falls_back_when_xdg_is_relative(registry_home, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative/path")
    assert vault_registry._registry_path() == str(registry_home / ".config" / "brain" / "vaults")


def test_registry_path_falls_back_when_xdg_is_empty(registry_home, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", "")
    assert vault_registry._registry_path() == str(registry_home / ".config" / "brain" / "vaults")


def test_load_registry_entries_returns_empty_when_missing(registry_home):
    assert vault_registry.load_registry_entries() == {}


def test_load_registry_entries_raises_on_unreadable_registry(registry_dir, monkeypatch):
    registry_path = registry_dir / "vaults"
    registry_path.write_text("brain\tlocal\t/Users/rob/brain\n")
    real_open = builtins.open

    def _fake_open(path, *args, **kwargs):
        if path == str(registry_path) or path == registry_path:
            raise PermissionError("nope")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _fake_open)

    with pytest.raises(vault_registry.RegistryReadError):
        vault_registry.load_registry_entries()


def test_register_aborts_without_clobbering_when_registry_is_unreadable(registry_dir, monkeypatch):
    registry_file = registry_dir / "vaults"
    original = "team\tremote\thttps://brain.example.com\n"
    registry_file.write_text(original)
    monkeypatch.setattr(
        vault_registry,
        "load_registry_entries",
        lambda: (_ for _ in ()).throw(vault_registry.RegistryReadError("broken registry")),
    )
    monkeypatch.setattr(
        vault_registry,
        "_save_registry_entries",
        lambda _entries: (_ for _ in ()).throw(AssertionError("should not save")),
    )

    with pytest.raises(vault_registry.RegistryReadError):
        vault_registry.register(str(_brain(registry_dir.parents[1] / "brain")))

    assert registry_file.read_text() == original


def test_save_then_load_roundtrip(registry_home):
    _save_local_entries({"brain": "/Users/rob/brain", "work-a3f": "/Users/rob/work/brain"})
    assert _local_entries() == {
        "brain": "/Users/rob/brain",
        "work-a3f": "/Users/rob/work/brain",
    }


def test_file_format_is_tab_separated_with_header(registry_home):
    _save_local_entries({"brain": "/Users/rob/brain"})
    text = (registry_home / ".config" / "brain" / "vaults").read_text()
    assert text.startswith("#")
    assert "<brain-id>\\t<kind>\\t<value>" in text.splitlines()[0]
    assert "brain\tlocal\t/Users/rob/brain" in text


def test_load_ignores_comments_and_blank_lines(registry_dir):
    (registry_dir / "vaults").write_text(
        "# a comment\n\nbrain\tlocal\t/Users/rob/brain\n   \n"
    )
    assert _local_entries() == {"brain": "/Users/rob/brain"}


def test_load_malformed_file_returns_empty(registry_dir, capsys):
    (registry_dir / "vaults").write_text("no tab here\n")
    assert vault_registry.load_registry_entries() == {}
    assert "malformed" in capsys.readouterr().err.lower()


def test_load_supports_legacy_two_column_local_entries(registry_dir):
    (registry_dir / "vaults").write_text("brain\t/Users/rob/brain\n")
    assert _local_entries() == {"brain": "/Users/rob/brain"}


def test_load_preserves_unknown_kind_and_warns(registry_dir, capsys):
    (registry_dir / "vaults").write_text("team\tplanetary\thttps://brain.example.com\n")
    entries = vault_registry.load_registry_entries()
    assert entries["team"] == vault_registry.RegistryEntry(
        brain_id="team",
        kind="planetary",
        value="https://brain.example.com",
    )
    err = capsys.readouterr().err
    assert "unrecognised kind" in err.lower()
    assert "planetary" in err


def test_unknown_kind_warning_is_deduplicated_and_sorted(registry_dir, capsys):
    (registry_dir / "vaults").write_text(
        "a\tzelda\thttps://example.com/a\n"
        "b\talpha\thttps://example.com/b\n"
        "c\tzelda\thttps://example.com/c\n"
    )
    vault_registry.load_registry_entries()
    lines = [line for line in capsys.readouterr().err.splitlines() if line.strip()]
    assert lines == [
        f"vault_registry: unrecognised kind(s) in {registry_dir / 'vaults'}: alpha, zelda"
    ]


def test_register_uses_basename_as_brain_id(registry_home):
    brain = _brain(registry_home / "brain")
    assert vault_registry.register(str(brain)) == "brain"
    assert _local_entries() == {"brain": str(brain)}


def test_register_slugifies_basename_with_spaces(registry_home):
    assert vault_registry.register(str(_brain(registry_home / "My Brain"))) == "my-brain"


def test_register_same_path_is_idempotent(registry_home):
    brain = _brain(registry_home / "brain")
    vault_registry.register(str(brain))
    assert vault_registry.register(str(brain)) == "brain"
    assert _local_entries() == {"brain": str(brain)}


def test_structured_registration_action_reports_change_state(registry_home):
    brain = str(_brain(registry_home / "brain"))
    created = vault_registry.register_action(brain)
    repeated = vault_registry.register_action(brain)

    assert created == vault_registry.RegistryRegistrationResult("brain", True)
    assert repeated == vault_registry.RegistryRegistrationResult("brain", False)


def test_register_collision_appends_suffix(registry_home, monkeypatch):
    monkeypatch.setattr(vault_registry, "random_short_suffix", lambda: "a3f")
    first, second = _brain(registry_home / "brain"), _brain(registry_home / "work" / "brain")
    vault_registry.register(str(first))
    brain_id = vault_registry.register(str(second))
    assert brain_id == "brain-a3f"
    assert _local_entries() == {
        "brain": str(first),
        "brain-a3f": str(second),
    }


def test_brain_id_for_path_reads_without_registering(registry_home):
    brain = _brain(registry_home / "brain")
    assert vault_registry.brain_id_for_path(str(brain)) is None
    assert not Path(vault_registry.registry_path()).exists()
    vault_registry.register(str(brain), brain_id="rob")
    assert vault_registry.brain_id_for_path(str(brain)) == "rob"
    assert vault_registry.brain_id_for_path(str(registry_home / "other")) is None


@pytest.mark.parametrize("stored", ["canonical", "symlinked"])
def test_brain_id_for_path_agrees_with_registration_lookup(registry_home, tmp_path, stored):
    vault = _brain(tmp_path / "vault")
    link = tmp_path / "link"
    link.symlink_to(vault)
    row = vault if stored == "canonical" else link
    _save_local_entries({"kept": str(row)})

    found = vault_registry.brain_id_for_path(str(vault))

    assert found == ("kept" if stored == "canonical" else None)
    if stored == "canonical":
        assert vault_registry.preview_register_action(str(vault)).changed is False
    else:
        # A drifted row never matches, and registering refuses to mint a second ID for its Brain.
        with pytest.raises(vault_registry.RegistryConflictError, match="remove-stale"):
            vault_registry.preview_register_action(str(vault))


def test_brain_id_for_path_matches_through_a_symlink(registry_home, tmp_path):
    vault = _brain(tmp_path / "vault")
    alias = tmp_path / "alias"
    alias.symlink_to(vault)
    vault_registry.register(str(vault), brain_id="vault")
    assert vault_registry.brain_id_for_path(str(alias)) == "vault"


def test_unregister_by_path(registry_home):
    brain = str(_brain(registry_home / "brain"))
    vault_registry.register(brain)
    assert vault_registry.unregister(brain) is True
    assert _local_entries() == {}


def test_unregister_unknown_path_returns_false(registry_home):
    assert vault_registry.unregister("/Users/rob/nope") is False


def test_unregister_preserves_non_local_entries(registry_dir):
    registry_file = registry_dir / "vaults"
    registry_file.write_text(
        "team\tremote\thttps://brain.example.com\n"
        "brain\tlocal\t/Users/rob/brain\n"
    )
    assert vault_registry.unregister("/Users/rob/brain") is True
    assert registry_file.read_text(encoding="utf-8") == (
        vault_registry.HEADER + "team\tremote\thttps://brain.example.com\n"
    )


def test_resolve_returns_path(registry_home):
    brain = str(_brain(registry_home / "brain"))
    vault_registry.register(brain)
    assert vault_registry.resolve("brain") == brain


def test_resolve_missing_returns_none(registry_home):
    assert vault_registry.resolve("nope") is None


def test_resolve_ignores_non_local_entries(registry_dir):
    (registry_dir / "vaults").write_text("team\tremote\thttps://brain.example.com\n")
    assert vault_registry.resolve("team") is None


def test_list_marks_stale_entries(registry_home, tmp_path):
    real = tmp_path / "real"
    (real / ".brain-core").mkdir(parents=True)
    (real / ".brain-core" / "VERSION").write_text("0.27.8\n")
    vault_registry.register(str(real))
    _add_row("missing", "/Users/rob/missing")
    entries = {
        entry["value"]: entry["stale"]
        for entry in vault_registry.list_entries()
        if entry["kind"] == vault_registry.TYPE_LOCAL
    }
    assert entries[str(real)] is False
    assert entries["/Users/rob/missing"] is True


def test_list_entries_marks_remote_as_reserved_and_unverified(registry_dir):
    (registry_dir / "vaults").write_text("team\tremote\thttps://brain.example.com\n")
    assert vault_registry.list_entries() == [
        {
            "alias": "team",
            "kind": vault_registry.TYPE_REMOTE,
            "value": "https://brain.example.com",
            "stale": None,
            "status": vault_registry.STATUS_RESERVED,
            "default": False,
        }
    ]


def test_list_entries_marks_unknown_kind_as_unverified(registry_dir, capsys):
    (registry_dir / "vaults").write_text("team\tplanetary\thttps://brain.example.com\n")
    entries = vault_registry.list_entries()
    assert entries == [
        {
            "alias": "team",
            "kind": "planetary",
            "value": "https://brain.example.com",
            "stale": None,
            "status": vault_registry.STATUS_UNKNOWN_KIND,
            "default": False,
        }
    ]
    assert "planetary" in capsys.readouterr().err


def test_prune_removes_stale(registry_home, tmp_path):
    real = tmp_path / "real"
    (real / ".brain-core").mkdir(parents=True)
    (real / ".brain-core" / "VERSION").write_text("0.27.8\n")
    vault_registry.register(str(real))
    _add_row("missing", "/Users/rob/missing")
    removed = vault_registry.prune()
    assert len(removed) == 1
    assert list(_local_entries().values()) == [str(real)]


def test_prune_preserves_non_local_entries(registry_dir, tmp_path, monkeypatch):
    real = tmp_path / "real"
    (real / ".brain-core").mkdir(parents=True)
    (real / ".brain-core" / "VERSION").write_text("0.27.8\n")
    registry_file = registry_dir / "vaults"
    registry_file.write_text(
        "team\tremote\thttps://brain.example.com\n"
        f"brain\tlocal\t{real}\n"
        "missing\tlocal\t/Users/rob/missing\n"
    )
    removed = vault_registry.prune()
    assert removed == ["missing"]
    assert registry_file.read_text(encoding="utf-8") == (
        vault_registry.HEADER
        + f"brain\tlocal\t{real}\n"
        + "team\tremote\thttps://brain.example.com\n"
    )


def test_prune_clears_matching_default(registry_home, tmp_path):
    real = tmp_path / "real"
    (real / ".brain-core").mkdir(parents=True)
    (real / ".brain-core" / "VERSION").write_text("0.27.8\n")
    vault_registry.register(str(real))
    missing_id = "missing"
    _add_row(missing_id, "/Users/rob/missing")
    vault_registry.set_default(missing_id)
    assert vault_registry.get_default() == missing_id
    vault_registry.prune()
    assert vault_registry.get_default() is None


def test_prune_leaves_non_matching_default(registry_home, tmp_path):
    real = tmp_path / "real"
    (real / ".brain-core").mkdir(parents=True)
    (real / ".brain-core" / "VERSION").write_text("0.27.8\n")
    keep_id = vault_registry.register(str(real))
    _add_row("missing", "/Users/rob/missing")
    vault_registry.set_default(keep_id)
    vault_registry.prune()
    assert vault_registry.get_default() == keep_id


def test_register_explicit_id_conflict_with_non_local_id_raises(registry_dir):
    registry_file = registry_dir / "vaults"
    registry_file.write_text("team\tremote\thttps://brain.example.com\n")
    with pytest.raises(vault_registry.RegistryConflictError):
        vault_registry.register(str(_brain(registry_dir.parents[1] / "brain")), brain_id="team")


def test_register_preserves_non_local_entries(registry_dir):
    registry_file = registry_dir / "vaults"
    registry_file.write_text("team\tremote\thttps://brain.example.com\n")
    brain = _brain(registry_dir.parents[1] / "brain")
    brain_id = vault_registry.register(str(brain))
    assert brain_id == "brain"
    text = registry_file.read_text(encoding="utf-8")
    assert "team\tremote\thttps://brain.example.com" in text
    assert f"brain\tlocal\t{brain}" in text


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_register_prints_brain_id(registry_home, tmp_path):
    result = _run_cli(registry_home, "--register", str(_brain(tmp_path / "brain")))
    assert "brain" in result.stdout


def test_cli_list_json(registry_home):
    _add_row("missing", "/Users/rob/missing")
    result = _run_cli(registry_home, "--list", "--json")
    data = json.loads(result.stdout)
    assert data[0]["alias"] == "missing"
    assert data[0]["kind"] == vault_registry.TYPE_LOCAL
    assert data[0]["stale"] is True
    assert data[0]["value"] == "/Users/rob/missing"


def test_cli_list_prints_reserved_remote_entry(registry_dir):
    (registry_dir / "vaults").write_text("team\tremote\thttps://brain.example.com\n")
    result = _run_cli(registry_dir.parents[1], "--list")
    assert "team [remote]: https://brain.example.com (reserved; unresolved here)" in result.stdout


def test_cli_list_prints_unknown_kind_entry_as_unverified(registry_dir):
    (registry_dir / "vaults").write_text("team\tplanetary\thttps://brain.example.com\n")
    result = _run_cli(registry_dir.parents[1], "--list", check=False)
    assert result.returncode == 0
    assert "team [planetary]: https://brain.example.com (unrecognised kind; unresolved here)" in result.stdout
    assert "planetary" in result.stderr


def test_cli_unregister_absent_exits_0(registry_home):
    result = _run_cli(registry_home, "--unregister", "/Users/rob/absent", check=False)
    assert result.returncode == 0


def test_cli_resolve_found(registry_home):
    brain = str(_brain(registry_home / "brain"))
    vault_registry.register(brain)
    result = _run_cli(registry_home, "--resolve", "brain")
    assert result.stdout.strip() == brain


def test_cli_resolve_missing_exits_1(registry_home):
    result = _run_cli(registry_home, "--resolve", "nope", check=False)
    assert result.returncode == 1
    assert "Unknown Brain ID" in result.stderr


def test_cli_read_error_exits_1(registry_dir):
    registry_path = registry_dir / "vaults"
    registry_path.mkdir()

    result = _run_cli(registry_dir.parents[1], "--list", check=False)
    assert result.returncode == 1
    assert "could not read brain registry" in result.stderr


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


def test_concurrent_register_does_not_lose_entries(registry_home, tmp_path):
    """Parallel --register subprocesses must all end up in the registry."""
    env = os.environ.copy()
    env["HOME"] = str(registry_home)
    env.pop("XDG_CONFIG_HOME", None)

    base = tmp_path / "vaults"
    base.mkdir()
    paths = [_brain(base / f"brain-{index}") for index in range(8)]

    procs = [
        subprocess.Popen(
            [sys.executable, str(SCRIPT), "--register", str(path)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for path in paths
    ]
    for proc in procs:
        assert proc.wait(timeout=10) == 0

    registry = _local_entries()
    assert len(registry) == 8
    expected = sorted(os.path.realpath(str(path)) for path in paths)
    assert sorted(registry.values()) == expected


# ---------------------------------------------------------------------------
# Default Brain pointer
# ---------------------------------------------------------------------------


def test_get_default_returns_none_when_absent(registry_home):
    assert vault_registry.get_default() is None


def test_set_and_get_default(registry_home):
    vault_registry.register(str(_brain(registry_home / "brain")))
    vault_registry.set_default("brain")
    assert vault_registry.get_default() == "brain"


def test_clear_default(registry_home):
    vault_registry.register(str(_brain(registry_home / "brain")))
    vault_registry.set_default("brain")
    vault_registry.clear_default()
    assert vault_registry.get_default() is None


def test_clear_default_tolerates_absent(registry_home):
    # No default set — should not raise.
    vault_registry.clear_default()
    assert vault_registry.get_default() is None


def test_structured_default_actions_report_change_state(registry_home):
    vault_registry.register(str(_brain(registry_home / "brain")))

    selected = vault_registry.set_default_action("brain")
    repeated = vault_registry.set_default_action("brain")
    cleared = vault_registry.clear_default_action()
    absent = vault_registry.clear_default_action()

    assert selected == vault_registry.RegistryDefaultResult("brain", True)
    assert repeated == vault_registry.RegistryDefaultResult("brain", False)
    assert cleared == vault_registry.RegistryDefaultResult("brain", True)
    assert absent == vault_registry.RegistryDefaultResult(None, False)


def test_set_default_rejects_unknown_id(registry_home):
    with pytest.raises(vault_registry.RegistryConflictError, match="not a registered local Brain"):
        vault_registry.set_default("unknown-id")


def test_set_default_rejects_remote_id(registry_dir):
    (registry_dir / "vaults").write_text("team\tremote\thttps://brain.example.com\n")
    with pytest.raises(vault_registry.RegistryConflictError, match="not a registered local Brain"):
        vault_registry.set_default("team")


def test_unregister_clears_matching_default(registry_home):
    vault_registry.register(str(_brain(registry_home / "brain")))
    vault_registry.set_default("brain")
    vault_registry.unregister(str(registry_home / "brain"))
    assert vault_registry.get_default() is None


def test_unregister_leaves_non_matching_default(registry_home):
    vault_registry.register(str(_brain(registry_home / "brain")))
    vault_registry.register(str(_brain(registry_home / "work")))
    vault_registry.set_default("work")
    vault_registry.unregister(str(registry_home / "brain"))
    assert vault_registry.get_default() == "work"


def test_structured_unregister_action_reports_removed_ids(registry_home):
    brain = str(_brain(registry_home / "brain"))
    vault_registry.register(brain, brain_id="brain")

    removed = vault_registry.unregister_action(brain)
    absent = vault_registry.unregister_action(brain)

    assert removed == vault_registry.RegistryRemovalResult(("brain",), True, False)
    assert absent == vault_registry.RegistryRemovalResult((), False, False)


def test_default_file_is_separate_from_vaults_file(registry_home, registry_dir):
    vault_registry.register(str(_brain(registry_home / "brain")))
    vault_registry.set_default("brain")
    vaults_text = (registry_dir / "vaults").read_text()
    assert "default" not in [line.split("\t")[0] for line in vaults_text.splitlines()]
    default_text = (registry_dir / "default").read_text()
    assert default_text.strip() == "brain"


def test_list_entries_marks_default_entry(registry_home):
    vault_registry.register(str(_brain(registry_home / "brain")))
    vault_registry.register(str(_brain(registry_home / "work")))
    vault_registry.set_default("work")
    entries = {e["alias"]: e["default"] for e in vault_registry.list_entries()}
    assert entries["brain"] is False
    assert entries["work"] is True


# ---------------------------------------------------------------------------
# Explicit brain-id on register
# ---------------------------------------------------------------------------


def test_register_explicit_id_creates_entry(registry_home):
    brain = str(_brain(registry_home / "brain"))
    brain_id = vault_registry.register(brain, brain_id="my-brain")
    assert brain_id == "my-brain"
    assert _local_entries() == {"my-brain": brain}


def test_register_explicit_id_same_path_is_noop(registry_home):
    brain = str(_brain(registry_home / "brain"))
    vault_registry.register(brain, brain_id="my-brain")
    result = vault_registry.register(brain, brain_id="my-brain")
    assert result == "my-brain"
    assert _local_entries() == {"my-brain": brain}


def test_register_explicit_id_conflict_different_path_raises(registry_home, tmp_path):
    vault_registry.register(str(_brain(tmp_path / "brain")), brain_id="my-brain")
    with pytest.raises(vault_registry.RegistryConflictError, match="already registered to a different path"):
        vault_registry.register(str(_brain(tmp_path / "other")), brain_id="my-brain")


def test_register_explicit_id_path_already_registered_raises(registry_home):
    vault_registry.register(str(_brain(registry_home / "brain")))  # auto → "brain"
    with pytest.raises(vault_registry.RegistryConflictError, match="already registered as 'brain'"):
        vault_registry.register(str(registry_home / "brain"), brain_id="new-name")


def test_register_explicit_id_invalid_slug_raises(registry_home):
    with pytest.raises(ValueError, match="invalid Brain ID"):
        vault_registry.register(str(_brain(registry_home / "brain")), brain_id="My Brain")


def test_register_explicit_id_uppercase_raises(registry_home):
    with pytest.raises(ValueError, match="invalid Brain ID"):
        vault_registry.register(str(_brain(registry_home / "brain")), brain_id="MyBrain")


def test_register_explicit_id_empty_raises(registry_home):
    with pytest.raises(ValueError, match="invalid Brain ID"):
        vault_registry.register(str(_brain(registry_home / "brain")), brain_id="")


def test_cli_has_no_backfill_alias(registry_home):
    result = _run_cli(registry_home, "--backfill", "/Users/rob/brain", check=False)
    assert result.returncode == 2
    assert not Path(vault_registry.registry_path()).exists()


# ---------------------------------------------------------------------------
# CLI — default flags
# ---------------------------------------------------------------------------


def test_cli_set_default(registry_home):
    vault_registry.register(str(_brain(registry_home / "brain")))
    result = _run_cli(registry_home, "--set-default", "brain")
    assert result.returncode == 0
    assert vault_registry.get_default() == "brain"


def test_cli_get_default_prints_id(registry_home):
    vault_registry.register(str(_brain(registry_home / "brain")))
    vault_registry.set_default("brain")
    result = _run_cli(registry_home, "--get-default")
    assert result.returncode == 0
    assert result.stdout.strip() == "brain"


def test_cli_get_default_absent_exits_0_prints_nothing(registry_home):
    result = _run_cli(registry_home, "--get-default")
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_cli_clear_default(registry_home):
    vault_registry.register(str(_brain(registry_home / "brain")))
    vault_registry.set_default("brain")
    result = _run_cli(registry_home, "--clear-default")
    assert result.returncode == 0
    assert vault_registry.get_default() is None


def test_cli_set_default_unknown_id_exits_1(registry_home):
    result = _run_cli(registry_home, "--set-default", "nope", check=False)
    assert result.returncode == 1
    assert "not a registered local Brain" in result.stderr


def test_cli_list_shows_default_tag(registry_home):
    vault_registry.register(str(_brain(registry_home / "brain")))
    vault_registry.set_default("brain")
    result = _run_cli(registry_home, "--list")
    assert "(default)" in result.stdout


def test_cli_list_json_includes_default_flag(registry_home):
    vault_registry.register(str(_brain(registry_home / "brain")))
    vault_registry.set_default("brain")
    result = _run_cli(registry_home, "--list", "--json")
    data = json.loads(result.stdout)
    assert data[0]["default"] is True


def test_cli_register_with_id(registry_home, tmp_path):
    brain = _brain(tmp_path / "brain")
    result = _run_cli(registry_home, "--register", str(brain), "--id", "my-brain")
    assert result.returncode == 0
    assert result.stdout.strip() == "my-brain"
    assert _local_entries() == {"my-brain": str(brain)}


def test_cli_register_with_id_conflict_exits_1(registry_home, tmp_path):
    vault_registry.register(str(_brain(tmp_path / "brain")), brain_id="my-brain")
    result = _run_cli(
        registry_home, "--register", str(_brain(tmp_path / "other")), "--id", "my-brain", check=False
    )
    assert result.returncode == 1
    assert "already registered" in result.stderr


@pytest.mark.parametrize("reader", ["load_registry_entries", "get_default"])
def test_undecodable_registry_files_are_registry_read_errors(registry_dir, reader):
    (registry_dir / "vaults").write_bytes(b"\xff\xfe broken\n")
    (registry_dir / "default").write_bytes(b"\xff\xfe broken\n")
    with pytest.raises(vault_registry.RegistryReadError):
        getattr(vault_registry, reader)()


def _drifted(tmp_path, brain_id="moved"):
    """Register /a/Brain, move it to /d/Brain and leave a symlink at the old path."""
    old = tmp_path / "a" / "Brain"
    _brain(old)
    vault_registry.register(str(old), brain_id)
    moved = (tmp_path / "d" / "Brain").resolve()
    moved.parent.mkdir()
    old.rename(moved)
    old.symlink_to(moved)
    return old, moved


def _follow_register_step(explanation):
    """Run the ``brain register`` command a stale row's explanation names after remove-stale."""
    import shlex

    argv = shlex.split(explanation[explanation.index("brain register --request-json"):])
    request = json.loads(argv[3].rstrip(";"))  # a further step may follow after "; "
    return vault_registry.register_action(request["vault_root"], request.get("brain_id"))


def test_a_symlinked_stored_row_is_stale_everywhere_and_never_mints_a_second_id(registry_home, tmp_path):
    """DD-083 item 2: a row whose stored path became a symlink is stale to every reader."""
    from _bootstrap import mcp_inventory
    from _bootstrap.file_transaction import FilePlan
    from _bootstrap.workspace_binding import resolve_local_brain_alias, resolve_local_brain_vault
    from _command_interface.direct import resolve_direct_brain_id
    from _machine.discovery import discover_brains

    old, moved = _drifted(tmp_path)

    assert vault_registry.brain_id_for_path(str(moved)) is None
    assert resolve_local_brain_alias(moved) is None
    assert resolve_local_brain_vault("moved") is None, "the drifted row is never followed through the symlink"
    assert resolve_direct_brain_id(moved).startswith("local-path-"), "one answer, never 'multiple identities'"
    discovery = discover_brains(current_vault=str(moved))
    [stale] = discovery["stale_registry_entries"]
    assert (stale["alias"], stale["reason"], stale["guidance"]) == (
        "moved", "not_canonical", "brain registry remove-stale")
    assert str(old) in stale["explanation"] and str(moved) in stale["explanation"]
    assert discovery["unregistered_brains"] == [str(moved)]
    collected = []
    assert mcp_inventory.local_brains(FilePlan(), unreachable=collected) == ()
    assert [item.path for item in collected] == [old]

    for brain_id in (None, "moved", "fresh"):
        with pytest.raises(vault_registry.RegistryConflictError, match="remove-stale"):
            vault_registry.register(str(moved), brain_id)
    assert set(_local_entries()) == {"moved"}, "no second ID was minted"

    assert vault_registry.prune_action().removed_brain_ids == ("moved",)
    assert _follow_register_step(stale["explanation"]).brain_id == "moved"
    assert _local_entries() == {"moved": str(moved)}
    assert resolve_local_brain_vault("moved") == moved


def test_the_other_half_of_an_alias_pair_still_registers_as_a_no_op(registry_home, tmp_path):
    """A canonical row that already matches is not a second ID, so re-registering it (install.sh does) no-ops."""
    old, moved = _alias_pair(tmp_path)

    assert vault_registry.register_action(str(moved)) == vault_registry.RegistryRegistrationResult("b", False)
    assert vault_registry.register_action(str(moved), "b").changed is False
    assert _local_entries() == {"a": str(old), "b": str(moved)}


def _alias_pair(tmp_path):
    """Old registries can hold this pair: A stored at a path that became a symlink, B registered at the target."""
    old, moved = _drifted(tmp_path, "a")
    _save_local_entries({"a": str(old), "b": str(moved)})
    return old, moved


def test_an_alias_pair_explains_the_duplicate_and_prune_skips_the_integration_check(registry_home, tmp_path, monkeypatch):
    old, moved = _alias_pair(tmp_path)
    entries = vault_registry.load_registry_entries()
    checked = []
    monkeypatch.setattr(vault_registry, "_require_no_mcp_integrations", lambda path, plan=None: checked.append(path))

    assert "which is registered as 'b'" in vault_registry.stale_explanation(entries["a"], entries)
    with pytest.raises(vault_registry.RegistryConflictError, match="registered as 'b'"):
        vault_registry.register_action(str(moved), "a")
    vault_registry.prune_action()

    assert checked == [], "B keeps its row and its integrations, so nothing is checked for A"
    assert _local_entries() == {"b": str(moved)}


@pytest.mark.parametrize("query", ["stored-drifted-path", "symlink-ambiguous-with-a-drifted-row"])
def test_unregister_refuses_a_path_through_a_symlink_that_would_remove_the_wrong_row(registry_home, tmp_path, query):
    """The displayed stale path must never unregister the healthy Brain it now resolves to."""
    old, moved = _alias_pair(tmp_path)
    path = old
    if query == "symlink-ambiguous-with-a-drifted-row":
        path = tmp_path / "another-link"
        path.symlink_to(moved)
    before = _local_entries()

    with pytest.raises(vault_registry.RegistryConflictError, match="not a canonical path|stored path of the stale row"):
        vault_registry.unregister_action(str(path))

    assert _local_entries() == before


@pytest.mark.parametrize("spelling", ["symlinked-parent", "symlink-to-the-brain"])
def test_unregister_accepts_an_ordinary_spelling_through_a_symlink(registry_home, tmp_path, spelling):
    """With no drifted row involved, /tmp, /var or a symlinked parent unregisters the Brain it resolves to."""
    brain = _brain(tmp_path / "real" / "Brain")
    vault_registry.register(str(brain), "b")
    if spelling == "symlinked-parent":
        (tmp_path / "parent-link").symlink_to(tmp_path / "real")
        path = tmp_path / "parent-link" / "Brain"
    else:
        path = tmp_path / "brain-link"
        path.symlink_to(brain)

    assert vault_registry.unregister_action(str(path)).removed_brain_ids == ("b",)
    assert _local_entries() == {}


def test_list_entries_reports_each_stale_reason_and_its_recovery(registry_home, tmp_path):
    old, moved = _drifted(tmp_path, "moved")
    _add_row("gone", tmp_path / "Missing")

    rows = {row["alias"]: row for row in vault_registry.list_entries()}

    assert (rows["moved"]["stale"], rows["moved"]["stale_reason"]) == (True, vault_registry.STALE_NOT_CANONICAL)
    assert rows["moved"]["stale_guidance"] == "brain registry remove-stale"
    assert vault_registry.register_guidance(moved, brain_id="moved") in rows["moved"]["stale_explanation"]
    assert (rows["gone"]["stale_reason"], rows["gone"]["stale_guidance"]) == (
        vault_registry.STALE_NOT_A_BRAIN, "brain registry remove-stale")


def test_selecting_a_drifted_row_by_id_refuses_with_its_recovery(registry_home, tmp_path):
    _drifted(tmp_path, "moved")

    with pytest.raises(vault_registry.StaleRowError, match="remove-stale"):
        vault_registry.require_live("moved")
    assert vault_registry.require_live("absent") is None


def test_only_an_installed_brain_registers(registry_home, tmp_path):
    folder = tmp_path / "agents-only"
    folder.mkdir()
    (folder / "AGENTS.md").write_text("bootstrap\n")

    with pytest.raises(vault_registry.NotABrainError, match="is not an installed Brain"):
        vault_registry.register_action(str(folder))
    assert vault_registry.preview_register_action(str(folder), installing=True).changed
    assert _local_entries() == {}


@pytest.mark.parametrize("query", ["old", "moved"])
def test_cli_unregister_with_approvals_forwards_the_literal_path(registry_home, tmp_path, monkeypatch, capsys, query):
    """With managed approvals the CLI forwards the path as passed, so the owner can refuse the stale alias path."""
    from types import SimpleNamespace

    sys.path.insert(0, str(REPO_ROOT / "cli"))
    from _bootstrap import machine_cli
    from _launcher.registry import BrainUnregisterRequest, execute_unregister

    old, moved = _alias_pair(tmp_path)
    forwarded = []

    def launcher(command, request):
        assert command == "brain.unregister"
        forwarded.append(request["vault_root"])
        result = execute_unregister(SimpleNamespace(dry_run=False), BrainUnregisterRequest(Path(request["vault_root"])))
        return {"status": result.status}

    monkeypatch.setattr(machine_cli, "approvals_present", lambda home=None: True)
    monkeypatch.setattr(machine_cli, "invoke", launcher)
    monkeypatch.setattr(sys, "argv", ["vault_registry.py", "--unregister", str(old if query == "old" else moved)])

    with pytest.raises(SystemExit) as exited:
        vault_registry.main()

    if query == "old":
        assert forwarded == [str(old)], "the literal path, never its realpath"
        assert exited.value.code == 1
        assert _local_entries() == {"a": str(old), "b": str(moved)}, "the healthy alias Brain is kept"
    else:
        assert exited.value.code == 0
        assert _local_entries() == {"a": str(old)}, "the canonical path removes the Brain it names"


def test_a_stale_row_whose_state_cannot_be_read_is_reported_not_raised(registry_home, tmp_path):
    """Listing (and so Doctor and discovery) never fails for the machine because one stale row is unreadable."""
    from _machine.discovery import discover_brains

    if sys.platform == "win32" or os.geteuid() == 0:
        pytest.skip("POSIX permission bits that bind the test user")
    folder = tmp_path / "Locked"
    locked = folder / ".brain" / "local"
    locked.mkdir(parents=True)
    _add_row("locked", folder)
    locked.chmod(0)  # the row is plainly stale (no .brain-core), but its MCP state cannot be read
    try:
        [row] = vault_registry.list_entries()
        [stale] = discover_brains()["stale_registry_entries"]
        with pytest.raises(vault_registry.RegistryConflictError, match="manual recovery"):
            vault_registry.prune_action()
    finally:
        locked.chmod(0o755)

    assert (row["stale"], row["stale_guidance"]) == (True, None)
    assert "could not be inspected" in row["stale_explanation"]
    assert (stale["alias"], stale["guidance"]) == ("locked", None)
    assert set(_local_entries()) == {"locked"}


def test_recovering_a_drifted_default_row_names_restoring_the_default(registry_home, tmp_path):
    """remove-stale clears a default it removes, so the explanation names setting it again; following it works."""
    import shlex

    _old, moved = _drifted(tmp_path, "moved")
    vault_registry.set_default("moved")
    [row] = vault_registry.list_entries()
    explanation = row["stale_explanation"]

    assert "brain set-default --request-json" in explanation
    vault_registry.prune_action()
    _follow_register_step(explanation)
    argv = shlex.split(explanation[explanation.index("brain set-default --request-json"):])
    vault_registry.set_default_action(json.loads(argv[3])["brain_id"])
    assert vault_registry.get_default() == "moved"
    assert vault_registry.resolve("moved") == str(moved)
