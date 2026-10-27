"""Native Windows fixed-namespace guards, without requiring symlink privilege."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

from _bootstrap.file_lock import vault_mutation_lock
from _bootstrap.owned_paths import (
    UnsafeFilesystemLayoutError, owned_directory, owned_file,
)


pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="native Windows filesystem guards")


def _junction(link: Path, target: Path) -> None:
    result = subprocess.run(
        ["cmd.exe", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_native_workspace_lock_and_staging_directory(tmp_path):
    with vault_mutation_lock(tmp_path):
        lock = tmp_path / ".brain/local/mutation.lock"
        assert lock.is_file()
        directory = owned_directory(tmp_path, ".brain/local/staging", create=True)
        body = directory / "record"
        body.write_bytes(b"valid")
        assert owned_file(tmp_path, ".brain/local/staging/record") == body


def test_native_router_producer_and_verifier_share_version_capability(tmp_path):
    import compile_router
    from _lifecycle.derived_cache_state import inspect_router_cache

    core = tmp_path / ".brain-core"
    core.mkdir()
    (core / "VERSION").write_text("1.0.0\n")
    (core / "session-core.md").write_text("Always:\n- Safe source.\n\nConditional:\n")
    config = tmp_path / "_Config"
    config.mkdir()
    (config / "router.md").write_text("Always:\n- Safe source.\n\nConditional:\n")
    compiled = compile_router.compile(tmp_path)
    assert os.path.join(".brain-core", "VERSION") in compiled["meta"]["sources"]
    compile_router.persist_compiled_router(tmp_path, compiled)
    assert not inspect_router_cache(tmp_path, verify_content=True).stale


def test_native_junction_is_refused_before_descending(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    target = tmp_path / "ordinary"
    target.mkdir()
    sentinel = target / "sentinel"
    sentinel.write_bytes(b"unchanged")
    _junction(root / ".brain", target)
    with pytest.raises(UnsafeFilesystemLayoutError):
        with vault_mutation_lock(root):
            pytest.fail("protected owner entered")
    assert sentinel.read_bytes() == b"unchanged"
    assert set(target.iterdir()) == {sentinel}


def test_native_lock_hardlink_is_refused_before_mode_or_content_change(tmp_path):
    directory = owned_directory(tmp_path, ".brain/local", create=True)
    target = tmp_path / "ordinary"
    target.write_bytes(b"unchanged")
    before = target.stat()
    os.link(target, directory / "mutation.lock")
    with pytest.raises(UnsafeFilesystemLayoutError):
        with vault_mutation_lock(tmp_path):
            pytest.fail("protected owner entered")
    assert target.read_bytes() == b"unchanged"
    assert target.stat().st_mode == before.st_mode
