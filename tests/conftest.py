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
TEMPLATE_VAULT_COPY_IGNORE = (".venv", ".pytest_cache", "local")

for _path in (PACKAGE_ROOT, SCRIPTS_DIR, MIGRATIONS_DIR):
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


def filesystem_is_case_sensitive(tmp_path):
    """Return True when the test filesystem distinguishes path casing.

    Cleans up the probe file so callers can pass a vault root without the probe
    leaking in as a stray content file (e.g. tripping check_root_files).
    """
    probe = tmp_path / "CaseProbe.txt"
    probe.write_text("probe\n")
    try:
        return not (tmp_path / "caseprobe.txt").exists()
    finally:
        probe.unlink(missing_ok=True)


def _masks_versioned_python(name: str) -> bool:
    """Return True for interpreter names launcher discovery prefers over a fake.

    install.sh (``find_python_312``/``find_python_for_script``) and ``cli/brain``
    (``find_launcher_python``) enumerate ``python3.13``, ``python3.12``,
    ``python3``, ``python`` via ``command -v`` and take the first match. To let a
    test's fake interpreter be the one discovered, we hide every *version-tagged*
    ``python3.N`` (the high-precedence names) plus bare ``python``/``python2`` —
    but keep a bare ``python3`` so generic liveness checks (the install.sh
    ``command -v python3`` preflight) still pass.
    """
    if name in ("python", "python2"):
        return True
    # Any version-tagged interpreter (python3.12, python3.13, a future
    # python4.0, ...) — keep only a bare `python3`.
    return bool(re.match(r"^python\d+\.\d", name))


@functools.lru_cache(maxsize=1)
def launcher_discovery_path() -> str:
    """Return a PATH value where the only version-tagged Python is a test's fake.

    Launcher-discovery results otherwise depend on what the host ships (this web
    container has ``python3.13`` on PATH; an Australian workstation may not), so
    discovery tests that install a fake ``python3.12``/``python3`` need the real
    versioned interpreters out of the way. Mirror every executable on the current
    PATH as a symlink, skipping the names :func:`_masks_versioned_python` hides,
    so a test can prepend its fake and get identical discovery everywhere. Built
    once per session; the symlink farm is cheap and read-only.
    """
    bin_dir = Path(tempfile.mkdtemp(prefix="brain-launcher-path-"))
    atexit.register(shutil.rmtree, bin_dir, ignore_errors=True)
    seen: set[str] = set()
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry or not os.path.isdir(entry):
            continue
        for name in sorted(os.listdir(entry)):
            if name in seen or _masks_versioned_python(name):
                continue
            src = os.path.join(entry, name)
            if os.path.isfile(src) and os.access(src, os.X_OK):
                try:
                    os.symlink(src, bin_dir / name)
                except OSError:
                    continue
                seen.add(name)
    return str(bin_dir)


def write_executable(path, content):
    """Write *content* to *path* and chmod +x. Used by install/upgrade integration tests."""
    import stat
    from pathlib import Path

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def write_fake_launcher(path, *, cversion="3.12", venv="none", real_python=None):
    """Write a fake ``python`` launcher stub used by the install.sh tests.

    Centralises the shell stub that install/upgrade tests previously copy-pasted.

    cversion: value printed for ``-c`` version probes (e.g. ``"3.12"``). If
        ``None``, ``-c`` is delegated to the real interpreter — for flows whose
        install path actually runs ``-c`` code rather than only probing.
    venv: behaviour of ``-m venv <dir>``:
        ``"none"``   — not handled (falls through to the real interpreter);
        ``"ok"``     — create a venv whose python records pip args to
                       ``pip-args.txt`` and succeeds;
        ``"fail"``   — like ``"ok"`` but the venv pip prints a failure and exits 1;
        ``"marker"`` — create the venv dir plus a ``should-not-exist.txt`` marker
                       (for tests asserting a flow must NOT provision a venv).
    """
    real_python = real_python or sys.executable
    if cversion is None:
        c_branch = (
            "if [ \"$1\" = \"-c\" ]; then\n"
            f"  exec {real_python} \"$@\"\n"
            "fi\n"
        )
    else:
        c_branch = (
            "if [ \"$1\" = \"-c\" ]; then\n"
            f"  printf '{cversion}\\n'\n"
            "  exit 0\n"
            "fi\n"
        )

    venv_branch = ""
    if venv in ("ok", "fail"):
        pip_tail = (
            "  exit 0\n" if venv == "ok"
            else "  printf 'simulated pip failure\\n' >&2\n  exit 1\n"
        )
        venv_branch = (
            "if [ \"$1\" = \"-m\" ] && [ \"$2\" = \"venv\" ]; then\n"
            "  venv_dir=\"$3\"\n"
            "  mkdir -p \"$venv_dir/bin\"\n"
            "  cat > \"$venv_dir/bin/python\" <<'EOF'\n"
            "#!/bin/sh\n"
            "if [ \"$1\" = \"-m\" ] && [ \"$2\" = \"pip\" ]; then\n"
            "  shift 2\n"
            "  venv_dir=$(cd \"$(dirname \"$0\")/..\" && pwd)\n"
            "  printf '%s\\n' \"$*\" > \"$venv_dir/pip-args.txt\"\n"
            f"{pip_tail}"
            "fi\n"
            "printf 'unexpected venv python args: %s\\n' \"$*\" >&2\n"
            "exit 1\n"
            "EOF\n"
            "  chmod +x \"$venv_dir/bin/python\"\n"
            "  exit 0\n"
            "fi\n"
        )
    elif venv == "marker":
        venv_branch = (
            "if [ \"$1\" = \"-m\" ] && [ \"$2\" = \"venv\" ]; then\n"
            "  venv_dir=\"$3\"\n"
            "  mkdir -p \"$venv_dir\"\n"
            "  printf 'unexpected venv creation\\n' > \"$venv_dir/should-not-exist.txt\"\n"
            "  exit 0\n"
            "fi\n"
        )

    write_executable(
        path,
        "#!/bin/sh\n" + c_branch + venv_branch + f"exec {real_python} \"$@\"\n",
    )


def copy_install_source(dest):
    """Copy the repo's install entry points into *dest* for install.sh integration tests.

    Copies install.sh, template-vault/, and src/brain-core/ — the minimum required
    to run ``bash install.sh`` against an isolated target.
    """
    import shutil
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    dest = Path(dest)
    shutil.copy2(repo_root / "install.sh", dest / "install.sh")
    shutil.copytree(
        repo_root / "template-vault",
        dest / "template-vault",
        ignore=shutil.ignore_patterns(*TEMPLATE_VAULT_COPY_IGNORE),
    )
    shutil.copytree(repo_root / "src" / "brain-core", dest / "src" / "brain-core")


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

def make_searchable_vault(tmp_path):
    """Create a vault with searchable Wiki/Designs/Logs content.

    Shared by the retrieval suites (lexical, semantic, build-index, evaluate)
    so the corpus lives in one place instead of being copy-pasted per file.
    """
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("1.0.0\n")
    (bc / "session-core.md").write_text("# Session Core\n")
    (tmp_path / "_Config").mkdir()

    wiki = tmp_path / "Wiki"
    wiki.mkdir()
    (wiki / "python-basics.md").write_text(
        "---\ntype: living/wiki\ntags: [python, programming]\nstatus: active\n---\n\n"
        "# Python Basics\n\nPython is a versatile programming language. "
        "Python supports object-oriented programming and functional programming. "
        "Python is widely used in data science and web development.\n"
    )
    (wiki / "rust-ownership.md").write_text(
        "---\ntype: living/wiki\ntags: [rust, systems]\nstatus: active\n---\n\n"
        "# Rust Ownership\n\nRust uses an ownership system to manage memory. "
        "The borrow checker enforces ownership rules at compile time. "
        "Rust prevents data races through its type system.\n"
    )
    (wiki / "javascript-async.md").write_text(
        "---\ntype: living/wiki\ntags: [javascript, web]\nstatus: draft\n---\n\n"
        "# JavaScript Async\n\nJavaScript uses promises and async/await for asynchronous programming. "
        "The event loop processes callbacks. Node.js is a JavaScript runtime.\n"
    )

    designs = tmp_path / "Designs"
    designs.mkdir()
    (designs / "brain-tooling.md").write_text(
        "---\ntype: living/design\ntags: [brain-core, tooling]\nstatus: active\n---\n\n"
        "# Brain Tooling Design\n\nThe brain-core tooling architecture uses Python scripts. "
        "Each script is self-contained with no external dependencies. "
        "The compiled router is the central configuration interface.\n"
    )

    temporal = tmp_path / "_Temporal"
    temporal.mkdir()
    logs = temporal / "Logs"
    logs.mkdir()
    month = logs / "2026-03"
    month.mkdir()
    (month / "20260315-python-log.md").write_text(
        "---\ntype: temporal/logs\ntags: [python, log]\nstatus: done\n---\n\n"
        "# Python Research Log\n\nResearched Python packaging tools. "
        "Compared pip, poetry, and pdm. Python packaging is evolving rapidly.\n"
    )

    return tmp_path


def build_and_persist_index(vault):
    """Build and persist the retrieval index for a searchable vault."""
    import _search.index as search_index_mod

    index = search_index_mod.build_index(vault).index
    search_index_mod.persist_retrieval_index(vault, index)
    return index
