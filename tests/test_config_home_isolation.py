"""Guard: the test suite must never touch the real ``~/.config/brain`` registry.

A missing/ineffective isolation fixture previously let tests register vaults and
seed the machine default Brain in the operator's real ``~/.config/brain``,
corrupting live MCP resolution (a dangling default → ``-32000``). The autouse
``_isolate_config_home`` fixture in ``conftest.py`` prevents that; these tests
fail loudly if it regresses.
"""

import os
from pathlib import Path

from _common import config_home


def test_config_home_is_isolated_to_tmp():
    """config_home() must resolve to the per-test XDG dir, never the real one."""
    expected = os.environ["XDG_CONFIG_HOME"]
    assert str(config_home()) == expected


def test_config_home_is_not_the_real_user_config():
    """The resolved registry root must not be the operator's real ~/.config."""
    real = Path(os.path.expanduser("~")) / ".config"
    assert config_home() != real


def test_registry_writes_land_in_the_isolated_home(tmp_path):
    """A registry mutation must write under the isolated config home, not the real one."""
    import vault_registry

    real_registry = Path(os.path.expanduser("~")) / ".config" / "brain" / "vaults"
    real_before = real_registry.read_text() if real_registry.exists() else None

    target = tmp_path / "guard-vault"
    target.mkdir()
    brain_id = vault_registry.register(str(target))

    isolated = Path(os.environ["XDG_CONFIG_HOME"]) / "brain" / "vaults"
    assert isolated.exists(), "registry should have been written under the isolated home"
    assert brain_id in isolated.read_text()

    # The real registry must be untouched by this test.
    real_after = real_registry.read_text() if real_registry.exists() else None
    assert real_after == real_before
