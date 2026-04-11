"""Shared test fixtures for brain-core tests."""

import os

import pytest


@pytest.fixture
def vault(tmp_path):
    """Create a minimal vault fixture in a temp directory."""
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("1.2.3\n")

    config = tmp_path / "_Config"
    config.mkdir()

    # Living types
    (tmp_path / "Wiki").mkdir()
    (tmp_path / "Designs").mkdir()
    (tmp_path / "Daily Notes").mkdir()

    # System dirs (should be excluded)
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / "_Plugins").mkdir()

    # Temporal types
    temporal = tmp_path / "_Temporal"
    temporal.mkdir()
    (temporal / "Logs").mkdir()
    (temporal / "Plans").mkdir()
    (temporal / "Research").mkdir()
    (temporal / ".hidden").mkdir()  # should be excluded

    return tmp_path


@pytest.fixture
def non_tmp_vault():
    """Vault directory outside the system temp dir.

    Uses a unique per-run directory under the repo's build artefacts to avoid
    collisions with parallel test runs and permission issues in restricted
    environments.
    """
    import shutil
    import uuid
    base = os.path.join(os.path.dirname(__file__), "..", ".pytest-vaults")
    vault_dir = os.path.join(os.path.realpath(base), f"vault-{uuid.uuid4().hex[:8]}")
    os.makedirs(os.path.join(vault_dir, "Wiki"), exist_ok=True)
    os.makedirs(os.path.join(vault_dir, ".brain-core"), exist_ok=True)
    with open(os.path.join(vault_dir, ".brain-core", "VERSION"), "w") as f:
        f.write("1.0.0\n")
    yield vault_dir
    shutil.rmtree(vault_dir, ignore_errors=True)
