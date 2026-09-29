"""Named role interpreters: the launch owner and role-file materialisation in `_common/_venv.py`.

Decision tests use a managed-venv shape (`pyvenv.cfg` plus a `bin/python`
symlink) so nothing starts an interpreter; materialisation tests use a real
`venv` under an isolated central root, because the probe is the claim.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import threading
from types import SimpleNamespace
import venv

import pytest

from _common import _venv

pytestmark = pytest.mark.skipif(os.name != "posix", reason="role files are POSIX-only")

REPO_ROOT = Path(__file__).resolve().parents[1]
FRAMEWORK_BUILD = sys.platform == "darwin" and bool(sysconfig.get_config_var("PYTHONFRAMEWORK"))
needs_supported_build = pytest.mark.skipif(
    FRAMEWORK_BUILD, reason="macOS framework builds re-exec into Python.app and cannot be named"
)


@pytest.fixture
def central_root(tmp_path, monkeypatch):
    """Point `central_venvs_root()` at a fresh home so no real managed venv is touched."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return _venv.central_venvs_root()


def _managed_shape(root: Path, name: str = "py3.12-0123456789abcdef") -> Path:
    venv_dir = root / name
    (venv_dir / "bin").mkdir(parents=True)
    (venv_dir / "pyvenv.cfg").write_text("home = /nowhere\n", encoding="utf-8")
    python = venv_dir / "bin" / "python"
    python.symlink_to(Path(sys.executable).resolve())
    return python


def _usable_role_file(python: Path, role: str) -> Path:
    role_file = _venv.role_interpreter(python, role)
    role_file.symlink_to(os.path.realpath(python))
    return role_file


def _real_managed_venv(root: Path, name: str = "py3.12-0123456789abcdef") -> Path:
    venv_dir = root / name
    venv.EnvBuilder(with_pip=False, symlinks=True).create(venv_dir)
    return venv_dir


def _staging_dirs(python: Path) -> list[str]:
    return sorted(path.name for path in python.parent.glob(".brain-*"))


# ---------------------------------------------------------------------------
# Launch owner
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("role", ["mcp", "cli"])
def test_role_file_names_a_managed_canonical_launch(central_root, role):
    python = _managed_shape(central_root)
    role_file = _usable_role_file(python, role)

    explicit = _venv.managed_command([str(python), "-c", "pass"], role=role)
    inherited = _venv.managed_command([python], env={"BRAIN_RUNTIME_ROLE": role})

    assert explicit.executable == inherited.executable == str(role_file)
    assert explicit.argv == [str(python), "-c", "pass"]
    assert inherited.argv == [str(python)]


def test_launch_is_unchanged_without_a_named_managed_canonical_argv0(central_root, tmp_path):
    python = _managed_shape(central_root)
    _usable_role_file(python, "mcp")
    other = tmp_path / "other"
    (other / "bin").mkdir(parents=True)
    (other / "pyvenv.cfg").write_text("home = /nowhere\n", encoding="utf-8")
    (other / "bin" / "python").symlink_to(Path(sys.executable).resolve())
    _usable_role_file(other / "bin" / "python", "mcp")
    stale = _managed_shape(central_root, "py3.12-fedcba9876543210")
    _venv.role_interpreter(stale, "mcp").symlink_to(Path(__file__).resolve())
    unchanged = {
        "bare": (["python"], "mcp"),
        "relative": ([os.path.relpath(python)], "mcp"),
        "base launcher": ([sys.executable], "mcp"),
        "other venv": ([str(other / "bin" / "python")], "mcp"),
        "non-canonical name": ([str(python.with_name("python3"))], "mcp"),
        "missing role file": ([str(python)], "cli"),
        "stale role file": ([str(stale)], "mcp"),
        "unknown role": ([str(python)], "operator"),
        "no role": ([str(python)], None),
    }
    for case, (argv, role) in unchanged.items():
        assert _venv.managed_command(argv, role=role).executable == argv[0], case


def test_env_enters_only_through_managed_command(central_root, monkeypatch):
    python = _managed_shape(central_root)
    monkeypatch.setenv("BRAIN_TEST_MARKER", "inherited")
    supplied = {"PATH": os.environ["PATH"], "BRAIN_RUNTIME_ROLE": "cli"}

    plain = _venv.managed_command([str(python)])
    explicit = _venv.managed_command([str(python)], role="mcp", env=supplied)
    passthrough = _venv.managed_command([str(python)], env=supplied)

    assert "BRAIN_RUNTIME_ROLE" not in plain.env and plain.env["BRAIN_TEST_MARKER"] == "inherited"
    assert explicit.env == {**supplied, "BRAIN_RUNTIME_ROLE": "mcp"}
    assert supplied["BRAIN_RUNTIME_ROLE"] == "cli", "the caller's environment is copied, not mutated"
    assert passthrough.env == supplied and passthrough.env is not supplied


def test_methods_reject_an_env_keyword(central_root):
    command = _venv.managed_command([sys.executable, "-c", "pass"])
    with pytest.raises(TypeError):
        command.run(env={})
    with pytest.raises(TypeError):
        command.popen(env={})
    with pytest.raises(TypeError):
        command.exec(env={})


def test_methods_pass_executable_argv_and_env_together(central_root, monkeypatch):
    python = _managed_shape(central_root)
    role_file = _usable_role_file(python, "mcp")
    calls = []
    monkeypatch.setattr(_venv.subprocess, "run", lambda argv, **kw: calls.append(("run", argv, kw)))
    monkeypatch.setattr(_venv.subprocess, "Popen", lambda argv, **kw: calls.append(("popen", argv, kw)))
    monkeypatch.setattr(_venv.os, "execve", lambda *args: calls.append(("exec", *args)))
    command = _venv.managed_command([str(python), "-m", "brain_mcp.server"], role="mcp", env={"KEY": "value"})

    command.run(timeout=5)
    command.popen(start_new_session=True)
    command.exec()

    expected_env = {"KEY": "value", "BRAIN_RUNTIME_ROLE": "mcp"}
    assert calls == [
        ("run", command.argv, {"executable": str(role_file), "env": expected_env, "timeout": 5}),
        ("popen", command.argv, {"executable": str(role_file), "env": expected_env, "start_new_session": True}),
        ("exec", str(role_file), command.argv, expected_env),
    ]


def test_unnamed_launch_is_the_plain_subprocess_call(central_root, monkeypatch):
    python = _managed_shape(central_root)  # no role file, so nothing is named
    calls = []
    monkeypatch.setattr(_venv.subprocess, "run", lambda argv, **kw: calls.append(kw))
    monkeypatch.setattr(_venv.subprocess, "Popen", lambda argv, **kw: calls.append(kw))
    command = _venv.managed_command([str(python), "-c", "pass"], role="mcp", env={"KEY": "value"})

    command.run(timeout=5)
    command.popen()

    assert all("executable" not in kwargs for kwargs in calls)


def test_run_managed_takes_subprocess_run_options():
    completed = _venv.run_managed(
        [sys.executable, "-c", "import os, sys; print(sys.stdin.read() + os.environ['BRAIN_RUNTIME_ROLE'])"],
        role="cli", input="echo:", capture_output=True, text=True, check=True, timeout=30,
    )
    assert completed.stdout == "echo:cli\n"


def test_autouse_isolation_removes_the_role():
    assert "BRAIN_RUNTIME_ROLE" not in os.environ


# ---------------------------------------------------------------------------
# Materialisation trigger
# ---------------------------------------------------------------------------

def _vault_with_requirements(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    core = vault / ".brain-core" / "brain_mcp"
    core.mkdir(parents=True)
    for name in _venv.RUNTIME_EXPORT_NAMES:
        (core / name).write_text("mcp==1.0.0\n", encoding="utf-8")
    return vault


def test_conform_runtime_publishes_after_writing_the_sentinel(tmp_path, monkeypatch):
    vault = _vault_with_requirements(tmp_path)
    requirements = _venv.vault_requirements_path(vault)
    python = tmp_path / "runtime" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.touch()
    seen = []
    monkeypatch.setattr(_venv, "verify_runtime_versions", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(_venv, "ensure_role_interpreters", lambda venv_dir: seen.append(
        (venv_dir, _venv.runtime_is_verified(python, requirements, "py3.12"))))

    changed = _venv.conform_runtime(python, requirements, tag="py3.12")

    assert changed is True
    assert seen == [(python.parent.parent, True)]


def test_plain_launch_and_dry_run_publish_nothing(central_root, tmp_path, monkeypatch):
    vault = _vault_with_requirements(tmp_path)
    requirements = _venv.vault_requirements_path(vault)
    python = _venv.resolve_vault_venv_python(vault, launcher=Path(sys.executable))
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr(_venv, "runtime_is_verified", lambda *_args: True)
    monkeypatch.setattr(_venv, "_probe_runtime", lambda *_args, **_kwargs: {"compatible": True, "missing": []})
    monkeypatch.setattr(_venv, "ensure_role_interpreters",
                        lambda venv_dir: (_ for _ in ()).throw(AssertionError("published")))

    reused = _venv.ensure_central_venv(requirements, launcher=Path(sys.executable))
    planned = _venv.resolve_or_provision_central_venv(
        vault, launcher=Path(sys.executable), required_modules=("mcp",), full_conformance=True, dry_run=True)

    assert reused["conformance_changed"] is False
    assert (planned["outcome"], planned["planned_action"]) == (_venv.RUNTIME_PLANNED, "sync")


# ---------------------------------------------------------------------------
# Materialisation mechanism
# ---------------------------------------------------------------------------

@needs_supported_build
def test_publishes_usable_role_files_with_the_platform_link(central_root):
    venv_dir = _real_managed_venv(central_root)
    python = _venv.venv_python(venv_dir)

    _venv.ensure_role_interpreters(venv_dir)

    for role in ("mcp", "cli"):
        role_file = _venv.role_interpreter(python, role)
        assert _venv.role_interpreter_usable(role_file, python), role
        if sys.platform == "darwin":
            assert not role_file.is_symlink() and role_file.stat().st_nlink >= 2
        else:
            assert role_file.is_symlink() and os.readlink(role_file) == os.path.realpath(python)
    assert _staging_dirs(python) == []


@needs_supported_build
def test_repeated_runs_change_nothing(central_root, monkeypatch):
    venv_dir = _real_managed_venv(central_root)
    python = _venv.venv_python(venv_dir)
    _venv.ensure_role_interpreters(venv_dir)
    before = {role: _venv.role_interpreter(python, role).stat() for role in ("mcp", "cli")}
    monkeypatch.setattr(_venv.subprocess, "run",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("re-probed")))

    _venv.ensure_role_interpreters(venv_dir)

    for role, stat in before.items():
        after = _venv.role_interpreter(python, role).stat()
        assert (after.st_ino, after.st_mtime_ns) == (stat.st_ino, stat.st_mtime_ns)


@pytest.mark.skipif(FRAMEWORK_BUILD is False, reason="needs a macOS framework build (Homebrew, python.org)")
def test_framework_build_publishes_nothing(central_root):
    venv_dir = _real_managed_venv(central_root)
    python = _venv.venv_python(venv_dir)

    _venv.ensure_role_interpreters(venv_dir)

    assert not any(_venv.role_interpreter(python, role).exists() for role in ("mcp", "cli"))
    assert _staging_dirs(python) == []


def test_non_managed_venv_is_unsupported(central_root, tmp_path, monkeypatch):
    outside = _real_managed_venv(tmp_path, "elsewhere")
    unconfigured = central_root / "py3.12-0123456789abcdef"
    (unconfigured / "bin").mkdir(parents=True)
    (unconfigured / "bin" / "python").symlink_to(Path(sys.executable).resolve())
    monkeypatch.setattr(_venv.subprocess, "run",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("probed")))

    _venv.ensure_role_interpreters(outside)
    _venv.ensure_role_interpreters(unconfigured)

    for venv_dir in (outside, unconfigured):
        assert sorted(path.name for path in (venv_dir / "bin").iterdir() if "brain" in path.name) == []


def test_windows_is_unsupported(central_root, monkeypatch):
    python = _managed_shape(central_root)
    monkeypatch.setattr(_venv.sys, "platform", "win32")
    monkeypatch.setattr(_venv.subprocess, "run",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("probed")))

    _venv.ensure_role_interpreters(python.parent.parent)

    assert sorted(path.name for path in python.parent.iterdir()) == ["python"]


def test_failed_link_publishes_nothing(central_root, monkeypatch):
    venv_dir = _real_managed_venv(central_root)
    python = _venv.venv_python(venv_dir)

    def refuse(*_args):
        raise PermissionError("read-only venv")

    monkeypatch.setattr(_venv.os, "link", refuse)
    monkeypatch.setattr(_venv.os, "symlink", refuse)

    _venv.ensure_role_interpreters(venv_dir)

    assert not any(_venv.role_interpreter(python, role).exists() for role in ("mcp", "cli"))
    assert _staging_dirs(python) == []


@pytest.mark.parametrize("stage", ["managed venv check", "dead staging cleanup"])
def test_unexpected_failure_never_escapes_materialisation(central_root, monkeypatch, stage):
    venv_dir = _real_managed_venv(central_root)
    python = _venv.venv_python(venv_dir)

    def fail(*_args):
        raise OverflowError(stage)

    target = "is_managed_venv" if stage == "managed venv check" else "_remove_dead_staging"
    monkeypatch.setattr(_venv, target, fail)

    _venv.ensure_role_interpreters(venv_dir)

    assert not any(_venv.role_interpreter(python, role).exists() for role in ("mcp", "cli"))


@pytest.mark.parametrize("failure", ["rejected", "timed out", "crashed"])
def test_failed_probe_or_crash_leaves_no_role_file(central_root, monkeypatch, failure):
    venv_dir = _real_managed_venv(central_root)
    python = _venv.venv_python(venv_dir)

    def probe(argv, **kwargs):
        assert kwargs["executable"] != argv[0], "the probe runs the staged candidate"
        if failure == "timed out":
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        return subprocess.CompletedProcess(argv, 1)

    monkeypatch.setattr(_venv.subprocess, "run", probe)
    if failure == "crashed":
        monkeypatch.setattr(_venv.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0))
        monkeypatch.setattr(_venv.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("disk full")))

    _venv.ensure_role_interpreters(venv_dir)

    assert not any(_venv.role_interpreter(python, role).exists() for role in ("mcp", "cli"))
    assert _staging_dirs(python) == []


@needs_supported_build
def test_probe_rejects_a_candidate_whose_kernel_name_is_wrong(central_root):
    venv_dir = _real_managed_venv(central_root)
    python = _venv.venv_python(venv_dir)
    staging = python.parent / ".staging"
    staging.mkdir()
    right = staging / "brain-mcp-python"
    wrong = staging / "other-name"
    for candidate in (right, wrong):
        if sys.platform == "darwin":
            os.link(os.path.realpath(python), candidate)
        else:
            os.symlink(os.path.realpath(python), candidate)

    assert _venv._role_probe_passes(python, right, "brain-mcp-python") is True
    assert _venv._role_probe_passes(python, wrong, "brain-mcp-python") is False


def test_dead_staging_is_removed_and_live_staging_kept(central_root, monkeypatch):
    venv_dir = _real_managed_venv(central_root)
    python = _venv.venv_python(venv_dir)
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait(timeout=30)
    dead = python.parent / f".brain-mcp-python.{finished.pid}.dead"
    live = python.parent / f".brain-mcp-python.{os.getpid()}.live"
    for staging in (dead, live):
        staging.mkdir()
        (staging / "brain-mcp-python").touch()
    monkeypatch.setattr(_venv.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 1))

    _venv.ensure_role_interpreters(venv_dir)

    assert _staging_dirs(python) == [live.name]


@needs_supported_build
def test_concurrent_runs_do_not_collide(central_root):
    venv_dir = _real_managed_venv(central_root)
    python = _venv.venv_python(venv_dir)
    errors = []

    def publish():
        try:
            _venv.ensure_role_interpreters(venv_dir)
        except BaseException as exc:  # a thread failure must fail the test, not vanish
            errors.append(exc)

    workers = [threading.Thread(target=publish) for _ in range(4)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=60)

    assert errors == []
    assert all(_venv.role_interpreter_usable(_venv.role_interpreter(python, role), python) for role in ("mcp", "cli"))
    assert _staging_dirs(python) == []


@needs_supported_build
def test_stale_role_file_falls_back_then_republishes(central_root, tmp_path):
    venv_dir = _real_managed_venv(central_root)
    python = _venv.venv_python(venv_dir)
    _venv.ensure_role_interpreters(venv_dir)
    role_file = _venv.role_interpreter(python, "mcp")
    # A base interpreter replaced in place leaves the role file on the old inode.
    replaced = tmp_path / "old-python3.12"
    shutil.copy2(os.path.realpath(python), replaced)
    role_file.unlink()
    os.link(replaced, role_file)

    assert _venv.role_interpreter_usable(role_file, python) is False
    assert _venv.managed_command([str(python)], role="mcp").executable == str(python)

    _venv.ensure_role_interpreters(venv_dir)

    assert _venv.role_interpreter_usable(role_file, python) is True
    assert _venv.managed_command([str(python)], role="mcp").executable == str(role_file)


# ---------------------------------------------------------------------------
# Entry roles
# ---------------------------------------------------------------------------

def test_proxy_names_its_server_child_and_handoff_probe_under_the_mcp_role(central_root, monkeypatch):
    from brain_mcp import proxy as proxy_mod
    from proxy_test_support import _fake_proxy_threads

    python = _managed_shape(central_root)
    role_file = _usable_role_file(python, "mcp")
    monkeypatch.setenv("BRAIN_RUNTIME_ROLE", "cli")  # e.g. `brain session run -- <program>`
    launches = []

    class Process:
        pid = 4321
        stderr = ()
        stdout = SimpleNamespace(fileno=lambda: 123)

        def wait(self, timeout):
            return 0

    def popen(argv, **kwargs):
        launches.append((argv, kwargs["executable"], kwargs["env"]["BRAIN_RUNTIME_ROLE"]))
        return Process()

    monkeypatch.setattr(proxy_mod.subprocess, "Popen", popen)
    monkeypatch.setattr(proxy_mod.os, "killpg", lambda *_args: None)
    _fake_proxy_threads(monkeypatch)

    proxy_mod.ChildProcess(str(python), "brain_mcp.server").start()
    proxy_mod.Proxy(str(python), "brain_mcp.server", str(central_root))._preflight_handoff(9, str(python))

    assert launches == [
        ([str(python), "-m", "brain_mcp.server"], str(role_file), "mcp"),
        ([str(python), "-m", "brain_mcp.proxy", "--check-handoff", "9"], str(role_file), "mcp"),
    ]


@pytest.mark.filterwarnings("ignore:.*found in sys.modules:RuntimeWarning")
def test_proxy_continues_unnamed_when_its_self_exec_fails(central_root, monkeypatch):
    import runpy

    python = _managed_shape(central_root)
    role_file = _usable_role_file(python, "mcp")
    monkeypatch.setattr(sys, "executable", str(python))
    monkeypatch.setattr(sys, "orig_argv", [str(python), "-m", "brain_mcp.proxy"])
    monkeypatch.setattr(sys, "argv", ["proxy.py"])
    attempts = []

    def failing_execve(*args):
        attempts.append(args[0])
        raise OSError("exec refused")

    monkeypatch.setattr(os, "execve", failing_execve)

    with pytest.raises(SystemExit) as exited:
        runpy.run_module("brain_mcp.proxy", run_name="__main__")

    assert attempts == [str(role_file)]
    assert exited.value.code == 1  # reached main()'s usage exit, unnamed


def test_mcp_serve_arrives_named_through_exec(central_root, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "path", [str(REPO_ROOT / "cli"), *sys.path])  # serve() extends it too
    import _mcp_stdio
    import _bootstrap.runtime as bootstrap_runtime
    import _bootstrap.workspace_binding as workspace_binding
    import _distribution

    python = _managed_shape(central_root)
    role_file = _usable_role_file(python, "mcp")
    monkeypatch.setattr(sys, "argv", ["brain", "mcp", "serve"])
    monkeypatch.setenv("PYTHONPATH", "restored-by-monkeypatch")
    monkeypatch.setattr(_distribution, "verify_distribution", lambda _root: {})
    monkeypatch.setattr(workspace_binding, "resolve_brain_target",
                        lambda **_kw: SimpleNamespace(vault_root=str(tmp_path), workspace_dir=None))
    monkeypatch.setattr(bootstrap_runtime, "target_managed_python", lambda _vault, launcher: python)
    launches = []
    monkeypatch.setattr(os, "execve", lambda *args: launches.append(args))

    assert _mcp_stdio.serve() == 0

    (executable, argv, env), = launches
    assert executable == str(role_file)
    assert argv[:5] == [str(python), "-s", "-P", "-m", "brain_mcp.proxy"]
    assert env["BRAIN_RUNTIME_ROLE"] == "mcp"


def test_cli_command_tier_sets_the_cli_role(central_root, tmp_path, monkeypatch):
    sys.path.insert(0, str(REPO_ROOT / "cli"))
    try:
        from _local_cli.execution import ApplicationProcessInvoker, SelectedBrainProcess
        from _local_cli.discovery import ComposedCommandEntry
    finally:
        sys.path.pop(0)

    python = _managed_shape(central_root)
    role_file = _usable_role_file(python, "cli")
    vault = tmp_path / "Brain"
    script = vault / ".brain-core" / "scripts" / "command.py"
    script.parent.mkdir(parents=True)
    script.write_text("# selected Brain command owner\n", encoding="utf-8")
    envelope = ('{"schema": "brain.command-result/1", "command": "artefact.read", "command_version": 1,'
                ' "status": "ok", "warnings": [], "result": {}, "committed_effects": []}')
    launches = []

    def run(argv, **kwargs):
        launches.append((argv[0], kwargs["executable"], kwargs["env"]["BRAIN_RUNTIME_ROLE"]))
        return subprocess.CompletedProcess(argv, 0, envelope, "")

    monkeypatch.setattr(_venv.subprocess, "run", run)
    entry = ComposedCommandEntry("application", "brain.command-catalogue/1", "sha256:application",
                                 "artefact.read", 1, "Read one thing.",
                                 {"command_id": "artefact.read", "command_version": 1})

    ApplicationProcessInvoker(SelectedBrainProcess(vault, python)).invoke(entry, {"path": "README.md"})

    assert launches == [(str(python), str(role_file), "cli")]
