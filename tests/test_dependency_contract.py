"""Dependency authority and installed-runtime reproducibility regressions."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

import pytest

import dependencies
import dependency_certification
from _common import _venv
from _repository_contracts.dependencies import validate_dependencies
from check_repository_contracts import WorkingTreeView


ROOT = Path(__file__).resolve().parents[1]


def copy_contract(tmp_path):
    for relative in (*dependencies.INPUTS, *dependencies.EXPORTS, ".gitattributes"):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    return tmp_path


def test_offline_exports_ignore_hostile_uv_environment_and_user_config(tmp_path, monkeypatch):
    root = copy_contract(tmp_path / "repo")
    config = tmp_path / "config" / "uv"
    config.mkdir(parents=True)
    (config / "uv.toml").write_text('index-url = "https://invalid.invalid/simple"\n')
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config.parent))
    for key in ("UV_INDEX_URL", "UV_DEFAULT_INDEX", "UV_EXTRA_INDEX_URL", "UV_CONFIG_FILE",
                "UV_PYTHON", "UV_OVERRIDE", "UV_NO_SOURCES"):
        monkeypatch.setenv(key, "hostile-invalid-value")
    dependencies.run(root)


@pytest.mark.parametrize("relative", dependencies.EXPORTS)
def test_each_generated_export_drift_fails_offline(tmp_path, relative):
    root = copy_contract(tmp_path)
    target = root / relative
    target.write_text(target.read_text() + "unexpected-package==1\n")
    with pytest.raises(ValueError, match="stale generated export"):
        dependencies.run(root)


def test_authored_declaration_drift_fails_offline(tmp_path):
    root = copy_contract(tmp_path)
    manifest = root / dependencies.INPUTS[0]
    manifest.write_text(manifest.read_text().replace('dependencies = ["mcp==2.0.0"]',
                                                   'dependencies = ["mcp==2.0.0", "anyio"]'))
    with pytest.raises(ValueError, match="dependency lock/export check failed"):
        dependencies.run(root)


def test_foreign_registry_is_rejected(tmp_path):
    root = copy_contract(tmp_path)
    lock = root / dependencies.INPUTS[2]
    lock.write_text(lock.read_text().replace(dependencies.INDEX, "https://foreign.invalid/simple"))
    with pytest.raises(ValueError, match="unexpected source"):
        dependencies.validate_sources(root)


def test_runtime_digest_covers_both_exports_but_not_contributor_files(tmp_path):
    root = copy_contract(tmp_path)
    base = root / next(iter(dependencies.EXPORTS))
    original = _venv.requirements_hash(base)
    (root / "dependencies/requirements-dev.txt").write_text("different==1\n")
    assert _venv.requirements_hash(base) == original
    semantic = base.with_name("requirements-semantic.txt")
    semantic.write_text(semantic.read_text() + "different==1\n")
    assert _venv.requirements_hash(base) != original
    semantic.unlink()
    with pytest.raises(FileNotFoundError):
        _venv.requirements_hash(base)


def test_autocrlf_checkout_index_and_archive_have_identical_contract_bytes(tmp_path):
    root = copy_contract(tmp_path / "repo")

    def git(*args):
        return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True).stdout

    git("init")
    git("config", "core.autocrlf", "true")
    git("add", ".")
    git("-c", "user.name=Contract Test", "-c", "user.email=contract@example.invalid", "commit", "-m", "fixture")
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    git("checkout-index", "--all", f"--prefix={checkout}{os.sep}")
    archive = tarfile.open(fileobj=io.BytesIO(git("archive", "HEAD")))
    for relative in (*dependencies.INPUTS, *dependencies.EXPORTS):
        expected = (root / relative).read_bytes()
        assert b"\r" not in expected
        assert (checkout / relative).read_bytes() == git("show", f":{relative}") == archive.extractfile(relative).read() == expected
    base = "src/brain-core/brain_mcp/requirements.txt"
    assert _venv.requirements_hash(root / base) == _venv.requirements_hash(checkout / base)


def test_real_distribution_audit_rejects_importable_wrong_version(tmp_path):
    export = tmp_path / "requirements.txt"
    export.write_text("mcp==0.0.0\n")
    with pytest.raises(subprocess.CalledProcessError) as caught:
        _venv.verify_runtime_versions(sys.executable, export)
    assert "installed 2.0.0" in caught.value.stderr


def test_conformance_repairs_drift_and_preserves_semantic_selection(tmp_path, monkeypatch):
    root = copy_contract(tmp_path / "repo")
    base = root / "src/brain-core/brain_mcp/requirements.txt"
    python = tmp_path / "runtime/bin/python"
    python.parent.mkdir(parents=True)
    python.touch()
    sentinel = python.parent.parent / _venv.DEPS_SENTINEL_NAME
    sentinel.write_text(json.dumps(_venv._readiness(python, base, "py3.12", True)))
    calls = []

    def audit(_python, selected, **_kwargs):
        calls.append(selected.name)
        if len(calls) == 1:
            raise subprocess.CalledProcessError(1, "audit")

    installs = []
    monkeypatch.setattr(_venv, "verify_runtime_versions", audit)
    monkeypatch.setattr(_venv.subprocess, "run", lambda args, **kwargs: installs.append(args))
    assert _venv.conform_runtime(python, base, tag="py3.12")
    assert calls == ["requirements-semantic.txt", "requirements-semantic.txt"]
    assert "--no-deps" in installs[0] and installs[0][-1].endswith("requirements-semantic.txt")
    assert _venv.runtime_is_verified(python, base, "py3.12")


def test_failed_audit_never_leaves_readiness(tmp_path, monkeypatch):
    root = copy_contract(tmp_path / "repo")
    base = root / "src/brain-core/brain_mcp/requirements.txt"
    python = tmp_path / "runtime/bin/python"
    python.parent.mkdir(parents=True)
    python.touch()
    (python.parent.parent / _venv.DEPS_SENTINEL_NAME).write_text(
        json.dumps(_venv._readiness(python, base, "py3.12", True)))
    def failure(*_args, **_kwargs):
        raise subprocess.CalledProcessError(1, "failure")
    monkeypatch.setattr(_venv, "verify_runtime_versions", failure)
    monkeypatch.setattr(_venv.subprocess, "run", failure)
    with pytest.raises(subprocess.CalledProcessError):
        _venv.conform_runtime(python, base, tag="py3.12")
    assert not _venv.runtime_is_verified(python, base, "py3.12")
    assert _venv._read_readiness(python) == {"semantic": True}


@pytest.mark.parametrize(
    ("path", "token", "expected"),
    (
        ("Makefile", "--no-deps", "complete export"),
        (
            "src/scripts/dependency_certification.py",
            "--no-deps",
            "native install boundary",
        ),
        (
            "src/scripts/dependency_certification.py",
            "--only-binary=:all:",
            "native install boundary",
        ),
    ),
)
def test_dependency_consumer_contracts_are_enforced(path, token, expected):
    class View(WorkingTreeView):
        def read_text(self, relative):
            content = super().read_text(relative)
            return content.replace(token, "") if relative == path else content

    assert any(expected in error for error in validate_dependencies(View(ROOT)))


def test_certification_install_command_is_wheel_only_and_non_resolving(tmp_path):
    command = dependency_certification.install_command(
        tmp_path / "python",
        tmp_path / "requirements.txt",
    )

    assert command[1:4] == ["-m", "pip", "install"]
    assert command[4:7] == ["--only-binary=:all:", "--no-deps", "-r"]
    assert command[-1] == str(tmp_path / "requirements.txt")


def test_staged_dependency_checker_uses_staged_bytes_and_code(tmp_path):
    """An unstaged correction cannot hide any of the four staged export drifts."""
    root = tmp_path / "repo"
    root.mkdir()
    sources = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT
    ).decode().split("\0")
    for relative in set(sources) - {""}:
        source = ROOT / relative
        if not source.is_file():
            continue
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)

    def git(*args):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)

    git("init")
    (root / ".gitignore").write_text("")
    git("add", ".")
    git("-c", "user.name=Contract Test", "-c", "user.email=contract@example.invalid", "commit", "-m", "fixture")

    def check():
        return subprocess.run([sys.executable, str(root / "src/scripts/check_repository_contracts.py"), "--staged"],
                              cwd=root, capture_output=True, text=True)

    healthy = check()
    assert healthy.returncode == 0, healthy.stderr
    for relative in dependencies.EXPORTS:
        target = root / relative
        original = target.read_bytes()
        target.write_bytes(original + b"unlisted==1\n")
        git("add", relative)
        target.write_bytes(original)
        drift = check()
        assert drift.returncode != 0 and "stale generated export" in drift.stderr, drift.stderr
        git("add", relative)
