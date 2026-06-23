"""Shared test fixtures and helpers for brain-core tests."""

import atexit
import functools
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

import pytest


# ---------------------------------------------------------------------------
# Deterministic timezone
#
# Brain computes artefact dates in *local* time (parse_date_value normalises to
# the host zone via .astimezone()), so many create/edit/reconcile/migrate tests
# assert ISO values and date folders that are only correct under the author's
# zone. Pin the suite's timezone here — before any test imports datetime helpers
# or spawns a subprocess via os.environ.copy() — so the suite is reproducible on
# UTC CI runners and the web container, not just an Australian workstation.
#
# POSIX only: guarded on time.tzset (absent on Windows). Setting an IANA TZ on
# Windows would not take effect — the CRT cannot parse it — and would leak a
# misleading value into every os.environ.copy() subprocess, including the
# Windows smoke's MCP server.
# ---------------------------------------------------------------------------

if hasattr(time, "tzset"):
    os.environ["TZ"] = "Australia/Sydney"
    time.tzset()


# ---------------------------------------------------------------------------
# Import path setup
#
# Tests import brain-core scripts by bare module name (e.g. `import check`,
# `import edit`, `import config`). pytest loads conftest.py before collecting
# tests, so adding the scripts dirs to sys.path here lets individual test
# files skip their own boilerplate sys.path manipulation.
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "src", "brain-core", "scripts")
)
PACKAGE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "brain-core"))
MIGRATIONS_DIR = os.path.join(SCRIPTS_DIR, "migrations")

for _path in (PACKAGE_ROOT, SCRIPTS_DIR, MIGRATIONS_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

# Re-export plain helpers from brain_test_support so existing `from conftest import <helper>`
# call sites keep working; subdirectory tests import them from brain_test_support directly.
from brain_test_support import (  # noqa: E402
    TEMPLATE_VAULT_COPY_IGNORE,
    build_and_persist_index,
    copy_install_source,
    filesystem_is_case_sensitive,
    launcher_discovery_path,
    make_router,
    make_searchable_vault,
    write_executable,
    write_fake_launcher,
    write_md,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

















# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_config_home(tmp_path, monkeypatch):
    """Isolate the machine config home for every test.

    ``config_home()`` (``_common/_paths.py``) honours ``XDG_CONFIG_HOME`` before
    ``HOME``/``Path.home()``, so pointing it at a per-test tmp dir guarantees no
    test reads or writes the real ``~/.config/brain`` registry (vaults list +
    default Brain pointer). The value lives in ``os.environ``, so any subprocess
    spawned with ``os.environ.copy()`` inherits the isolation too; subprocess
    tests that build a *clean* env (e.g. ``env={"PATH": ...}``) must not touch
    the registry, or must propagate ``XDG_CONFIG_HOME`` themselves.

    Using ``tmp_path/.config`` keeps in-process resolution consistent with any
    subprocess that instead isolates via ``HOME=tmp_path`` (both resolve to
    ``tmp_path/.config/brain``).
    """
    cfg = tmp_path / ".config"
    cfg.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    return cfg


@pytest.fixture(autouse=True)
def _isolate_resolution_runtime(tmp_path, monkeypatch):
    """Keep machine-level resolver runtime writes out of the real home dir."""
    runtime = tmp_path / ".brain" / "resolution-runtime"
    monkeypatch.setenv("BRAIN_RESOLUTION_RUNTIME_DIR", str(runtime))
    return runtime


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
def bootstrap_vault(tmp_path):
    """Create a lightweight Brain vault root for launcher-safe bootstrap tests."""
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("0.10.0\n", encoding="utf-8")
    (bc / "brain_mcp").mkdir()
    (bc / "brain_mcp" / "proxy.py").write_text("# stub\n", encoding="utf-8")
    (bc / "brain_mcp" / "server.py").write_text("# stub\n", encoding="utf-8")
    scripts_dir = bc / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "session.py").write_text("# stub\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def project(tmp_path):
    """Create a small external project/workspace directory."""
    project_dir = tmp_path / "my-project"
    project_dir.mkdir()
    return project_dir


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Redirect the user home to ``tmp_path`` so tests can write fake user configs.

    Sets both ``HOME`` (what most brain-core path logic reads, e.g.
    ``config_home()`` and the ``~/.brain`` managed runtime) and ``Path.home()``
    (for the few callers that use it directly). Patching only ``Path.home()`` —
    as this fixture once did — was ineffective for ``HOME``-based resolution.
    Registry isolation is already guaranteed by the autouse ``_isolate_config_home``;
    this fixture additionally redirects ``HOME`` for non-config home paths.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def wrapper_cli():
    """Run a top-level wrapper script as a subprocess against *vault*."""

    def run(vault, script_name, *args, env=None):
        merged_env = os.environ.copy()
        existing = merged_env.get("PYTHONPATH")
        merged_env["PYTHONPATH"] = (
            SCRIPTS_DIR if not existing else f"{SCRIPTS_DIR}{os.pathsep}{existing}"
        )
        # CLI wrapper tests exercise wrapper behaviour without provisioning a
        # real central runtime for every minimal fixture.  The skip-bootstrap
        # seam still verifies that the current interpreter satisfies the
        # wrapper's requested modules.
        merged_env.setdefault("BRAIN_SKIP_BOOTSTRAP", "1")
        if env:
            merged_env.update(env)
        script_path = REPO_ROOT / "src" / "brain-core" / "scripts" / script_name
        return subprocess.run(
            [sys.executable, str(script_path), *map(str, args)],
            cwd=vault,
            capture_output=True,
            text=True,
            env=merged_env,
            timeout=30,
        )

    return run


@pytest.fixture
def non_tmp_vault():
    """Vault directory outside the system temp dir.

    Uses a unique per-run directory under the repo's build artefacts by
    default. Temp-rooted worktrees can override the base path with
    ``BRAIN_TEST_NON_TMP_ROOT`` so the fixture remains meaningfully "non-temp"
    for path validation tests.
    """
    import shutil
    import uuid
    from pathlib import Path

    override = os.environ.get("BRAIN_TEST_NON_TMP_ROOT")
    base = Path(override).expanduser() if override else (Path(__file__).resolve().parents[1] / ".pytest-vaults")
    vault_dir = os.path.join(os.path.realpath(base), f"vault-{uuid.uuid4().hex[:8]}")
    os.makedirs(os.path.join(vault_dir, "Wiki"), exist_ok=True)
    os.makedirs(os.path.join(vault_dir, ".brain-core"), exist_ok=True)
    with open(os.path.join(vault_dir, ".brain-core", "VERSION"), "w") as f:
        f.write("1.0.0\n")
    with open(os.path.join(vault_dir, ".brain-core", "session-core.md"), "w") as f:
        f.write("# Session Core\n")
    yield vault_dir
    shutil.rmtree(vault_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Searchable-vault builders (shared by the retrieval test suites)
# ---------------------------------------------------------------------------



