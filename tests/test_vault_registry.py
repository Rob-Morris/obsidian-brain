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
        vault_registry.register("/Users/rob/brain")

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
    assert vault_registry.register("/Users/rob/brain") == "brain"
    assert _local_entries() == {"brain": "/Users/rob/brain"}


def test_register_slugifies_basename_with_spaces(registry_home):
    assert vault_registry.register("/Users/rob/My Brain") == "my-brain"


def test_register_same_path_is_idempotent(registry_home):
    vault_registry.register("/Users/rob/brain")
    assert vault_registry.register("/Users/rob/brain") == "brain"
    assert _local_entries() == {"brain": "/Users/rob/brain"}


def test_register_collision_appends_suffix(registry_home, monkeypatch):
    monkeypatch.setattr(vault_registry, "random_short_suffix", lambda: "a3f")
    vault_registry.register("/Users/rob/brain")
    brain_id = vault_registry.register("/Users/rob/work/brain")
    assert brain_id == "brain-a3f"
    assert _local_entries() == {
        "brain": "/Users/rob/brain",
        "brain-a3f": "/Users/rob/work/brain",
    }


def test_backfill_is_noop_when_path_present(registry_home):
    vault_registry.register("/Users/rob/brain")
    assert vault_registry.backfill("/Users/rob/brain") == "brain"
    assert _local_entries() == {"brain": "/Users/rob/brain"}


def test_backfill_registers_new_path(registry_home):
    assert vault_registry.backfill("/Users/rob/brain") == "brain"


def test_unregister_by_path(registry_home):
    vault_registry.register("/Users/rob/brain")
    assert vault_registry.unregister("/Users/rob/brain") is True
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
    vault_registry.register("/Users/rob/brain")
    assert vault_registry.resolve("brain") == "/Users/rob/brain"


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
    vault_registry.register("/Users/rob/missing")
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
        }
    ]
    assert "planetary" in capsys.readouterr().err


def test_prune_removes_stale(registry_home, tmp_path):
    real = tmp_path / "real"
    (real / ".brain-core").mkdir(parents=True)
    (real / ".brain-core" / "VERSION").write_text("0.27.8\n")
    vault_registry.register(str(real))
    vault_registry.register("/Users/rob/missing")
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


def test_register_preserves_non_local_entries(registry_dir):
    registry_file = registry_dir / "vaults"
    registry_file.write_text("team\tremote\thttps://brain.example.com\n")
    brain_id = vault_registry.register("/Users/rob/brain")
    assert brain_id == "brain"
    text = registry_file.read_text(encoding="utf-8")
    assert "team\tremote\thttps://brain.example.com" in text
    assert "brain\tlocal\t/Users/rob/brain" in text


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_register_prints_brain_id(registry_home):
    result = _run_cli(registry_home, "--register", "/Users/rob/brain")
    assert "brain" in result.stdout


def test_cli_list_json(registry_home):
    vault_registry.register("/Users/rob/missing")
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
    vault_registry.register("/Users/rob/brain")
    result = _run_cli(registry_home, "--resolve", "brain")
    assert result.stdout.strip() == "/Users/rob/brain"


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
    paths = [base / f"brain-{index}" for index in range(8)]
    for path in paths:
        path.mkdir()

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
