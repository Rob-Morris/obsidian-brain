"""Unit tests for vault_registry.py."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import vault_registry  # sys.path is set up by conftest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def registry_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return tmp_path


@pytest.fixture
def registry_dir(registry_home):
    """Parent dir of the registry file — ensures it exists for direct writes."""
    d = registry_home / ".config" / "brain"
    d.mkdir(parents=True)
    return d


def test_registry_path_defaults_to_config_brain(registry_home):
    assert vault_registry._registry_path() == str(registry_home / ".config" / "brain" / "vaults")


def test_registry_path_respects_xdg_config_home(registry_home, monkeypatch, tmp_path):
    xdg = tmp_path / "custom-config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    assert vault_registry._registry_path() == str(xdg / "brain" / "vaults")


def test_load_returns_empty_when_missing(registry_home):
    assert vault_registry.load() == {}


def test_save_then_load_roundtrip(registry_home):
    vault_registry.save({"brain": "/Users/rob/brain", "work-a3f": "/Users/rob/work/brain"})
    assert vault_registry.load() == {
        "brain": "/Users/rob/brain",
        "work-a3f": "/Users/rob/work/brain",
    }


def test_file_format_is_tab_separated_with_header(registry_home):
    vault_registry.save({"brain": "/Users/rob/brain"})
    text = (registry_home / ".config" / "brain" / "vaults").read_text()
    assert text.startswith("#")
    assert "brain\t/Users/rob/brain" in text


def test_load_ignores_comments_and_blank_lines(registry_dir):
    (registry_dir / "vaults").write_text(
        "# a comment\n\nbrain\t/Users/rob/brain\n   \n"
    )
    assert vault_registry.load() == {"brain": "/Users/rob/brain"}


def test_load_malformed_file_returns_empty(registry_dir, capsys):
    (registry_dir / "vaults").write_text("no tab here\n")
    assert vault_registry.load() == {}
    assert "malformed" in capsys.readouterr().err.lower()


def test_register_uses_basename_as_alias(registry_home):
    assert vault_registry.register("/Users/rob/brain") == "brain"
    assert vault_registry.load() == {"brain": "/Users/rob/brain"}


def test_register_slugifies_basename_with_spaces(registry_home):
    assert vault_registry.register("/Users/rob/My Brain") == "my-brain"


def test_register_same_path_is_idempotent(registry_home):
    vault_registry.register("/Users/rob/brain")
    assert vault_registry.register("/Users/rob/brain") == "brain"
    assert vault_registry.load() == {"brain": "/Users/rob/brain"}


def test_register_collision_appends_suffix(registry_home, monkeypatch):
    monkeypatch.setattr(vault_registry, "random_short_suffix", lambda: "a3f")
    vault_registry.register("/Users/rob/brain")
    alias = vault_registry.register("/Users/rob/work/brain")
    assert alias == "brain-a3f"
    assert vault_registry.load() == {
        "brain": "/Users/rob/brain",
        "brain-a3f": "/Users/rob/work/brain",
    }


def test_backfill_is_noop_when_path_present(registry_home):
    vault_registry.register("/Users/rob/brain")
    assert vault_registry.backfill("/Users/rob/brain") == "brain"
    assert vault_registry.load() == {"brain": "/Users/rob/brain"}


def test_backfill_registers_new_path(registry_home):
    assert vault_registry.backfill("/Users/rob/brain") == "brain"


def test_unregister_by_path(registry_home):
    vault_registry.register("/Users/rob/brain")
    assert vault_registry.unregister("/Users/rob/brain") is True
    assert vault_registry.load() == {}


def test_unregister_unknown_path_returns_false(registry_home):
    assert vault_registry.unregister("/Users/rob/nope") is False


def test_resolve_returns_path(registry_home):
    vault_registry.register("/Users/rob/brain")
    assert vault_registry.resolve("brain") == "/Users/rob/brain"


def test_resolve_missing_returns_none(registry_home):
    assert vault_registry.resolve("nope") is None


def test_list_marks_stale_entries(registry_home, tmp_path):
    real = tmp_path / "real"
    (real / ".brain-core").mkdir(parents=True)
    (real / ".brain-core" / "VERSION").write_text("0.27.8\n")
    vault_registry.register(str(real))
    vault_registry.register("/Users/rob/missing")
    entries = {e["path"]: e["stale"] for e in vault_registry.list_entries()}
    assert entries[str(real)] is False
    assert entries["/Users/rob/missing"] is True


def test_prune_removes_stale(registry_home, tmp_path):
    real = tmp_path / "real"
    (real / ".brain-core").mkdir(parents=True)
    (real / ".brain-core" / "VERSION").write_text("0.27.8\n")
    vault_registry.register(str(real))
    vault_registry.register("/Users/rob/missing")
    removed = vault_registry.prune()
    assert len(removed) == 1
    assert list(vault_registry.load().values()) == [str(real)]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

SCRIPT = REPO_ROOT / "src" / "brain-core" / "scripts" / "vault_registry.py"


def _run_cli(registry_home, *args, check=True):
    env = os.environ.copy()
    env["HOME"] = str(registry_home)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        env=env, capture_output=True, text=True, check=check,
    )


def test_cli_register_prints_alias(registry_home):
    r = _run_cli(registry_home, "--register", "/Users/rob/brain")
    assert "brain" in r.stdout


def test_cli_list_json(registry_home):
    vault_registry.register("/Users/rob/missing")
    r = _run_cli(registry_home, "--list", "--json")
    data = json.loads(r.stdout)
    assert data[0]["alias"] == "missing"
    assert data[0]["stale"] is True


def test_cli_unregister_absent_exits_0(registry_home):
    r = _run_cli(registry_home, "--unregister", "/Users/rob/absent", check=False)
    assert r.returncode == 0


def test_cli_resolve_found(registry_home):
    vault_registry.register("/Users/rob/brain")
    r = _run_cli(registry_home, "--resolve", "brain")
    assert r.stdout.strip() == "/Users/rob/brain"


def test_cli_resolve_missing_exits_1(registry_home):
    r = _run_cli(registry_home, "--resolve", "nope", check=False)
    assert r.returncode == 1
