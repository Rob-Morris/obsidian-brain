"""Shared test fixtures and helpers for brain-core tests."""

import os
import sys

import pytest


# ---------------------------------------------------------------------------
# Import path setup
#
# Tests import brain-core scripts by bare module name (e.g. `import check`,
# `import edit`, `import config`). pytest loads conftest.py before collecting
# tests, so adding the scripts dirs to sys.path here lets individual test
# files skip their own boilerplate sys.path manipulation.
# ---------------------------------------------------------------------------

SCRIPTS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "src", "brain-core", "scripts")
)
MIGRATIONS_DIR = os.path.join(SCRIPTS_DIR, "migrations")

for _path in (SCRIPTS_DIR, MIGRATIONS_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def write_md(path, frontmatter_fields=None, body=""):
    """Write a markdown file with optional frontmatter.

    Used by tests that build ad-hoc vaults and need a small, readable way to
    drop a markdown file with YAML frontmatter. Kept here so individual test
    files don't need to duplicate the helper.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if frontmatter_fields:
        lines.append("---")
        for k, v in frontmatter_fields.items():
            if isinstance(v, list):
                lines.append(f"{k}:")
                for item in v:
                    lines.append(f"  - {item}")
            else:
                lines.append(f"{k}: {v}")
        lines.append("---")
    lines.append(body)
    path.write_text("\n".join(lines) + "\n")


def make_router(artefacts, meta=None):
    """Build a minimal compiled router dict for tests that pre-seed one."""
    if meta is None:
        meta = {"brain_core_version": "0.9.11"}
    return {"meta": meta, "artefacts": artefacts}


def copy_install_source(dest):
    """Copy the repo's install entry points into *dest* for install.sh integration tests.

    Copies install.sh, template-vault/, and src/brain-core/ — the minimum required
    to run ``bash install.sh`` against an isolated target. Callers typically stub
    ``src/brain-core/scripts/init.py`` afterwards to avoid real MCP registration.
    """
    import shutil
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    dest = Path(dest)
    shutil.copy2(repo_root / "install.sh", dest / "install.sh")
    shutil.copytree(repo_root / "template-vault", dest / "template-vault")
    shutil.copytree(repo_root / "src" / "brain-core", dest / "src" / "brain-core")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def vault(tmp_path):
    """Create a minimal vault fixture in a temp directory."""
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("1.2.3\n")
    (bc / "session-core.md").write_text("# Session Core\n")

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
def fake_home(tmp_path, monkeypatch):
    """Redirect ``Path.home()`` to ``tmp_path`` so tests can write fake user configs."""
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
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
    with open(os.path.join(vault_dir, ".brain-core", "session-core.md"), "w") as f:
        f.write("# Session Core\n")
    yield vault_dir
    shutil.rmtree(vault_dir, ignore_errors=True)
