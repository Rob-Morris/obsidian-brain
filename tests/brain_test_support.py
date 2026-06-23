"""Plain (non-fixture) test helpers, importable from anywhere under tests/.

Fixtures live in ``conftest.py`` (they cascade into subdirectories
automatically). Plain helper *functions*, however, cannot be reached from a
subdirectory test via ``from conftest import ...`` — under pytest's default
prepend import mode that name resolves to the subdirectory's own conftest. So
the shared plain helpers live here instead, on the ``pythonpath`` (see
pyproject), and ``conftest`` re-exports them for backwards compatibility.
"""

import atexit
import functools
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

TEMPLATE_VAULT_COPY_IGNORE = (".venv", ".pytest_cache", "local")


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
    repo_root = Path(__file__).resolve().parents[1]
    dest = Path(dest)
    shutil.copy2(repo_root / "install.sh", dest / "install.sh")
    shutil.copytree(
        repo_root / "template-vault",
        dest / "template-vault",
        ignore=shutil.ignore_patterns(*TEMPLATE_VAULT_COPY_IGNORE),
    )
    shutil.copytree(repo_root / "src" / "brain-core", dest / "src" / "brain-core")


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
