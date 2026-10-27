import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "brain-core" / "scripts"))

import pytest

import _staging
import stage as stage_cli
from _common import vault_mutation_lock
from _staging import (
    STAGING_TTL_SECONDS,
    read_staged_body,
    resolve_mutation_body,
    stage_body,
    sweep_staged_bodies,
)


def test_stage_sweep_removes_expired_handles(tmp_path):
    result = stage_body(str(tmp_path), "body")
    path = tmp_path / ".brain" / "local" / "staging" / f"{result['handle'][5:]}.body"
    expired = path.stat().st_mtime - STAGING_TTL_SECONDS - 1
    os.utime(path, (expired, expired))

    assert sweep_staged_bodies(str(tmp_path)) == 1
    assert not path.exists()


def test_expired_handle_cannot_be_read_without_a_sweep(tmp_path):
    result = stage_body(str(tmp_path), "body")
    path = tmp_path / ".brain" / "local" / "staging" / f"{result['handle'][5:]}.body"
    expired = path.stat().st_mtime - STAGING_TTL_SECONDS - 1
    os.utime(path, (expired, expired))

    with pytest.raises(ValueError, match="Expired body_handle"):
        read_staged_body(str(tmp_path), result["handle"])
    assert not path.exists()


def test_resolve_mutation_body_rejects_multiple_sources(tmp_path):
    with pytest.raises(ValueError, match="mutually exclusive"):
        resolve_mutation_body(str(tmp_path), body="inline", body_file="body.md")


def test_stage_rejects_oversized_body(tmp_path, monkeypatch):
    monkeypatch.setattr(_staging, "MAX_STAGED_BODY_BYTES", 3)

    with pytest.raises(ValueError, match="maximum is 3"):
        stage_body(str(tmp_path), "four")


def test_stage_enforces_file_count_quota(tmp_path, monkeypatch):
    monkeypatch.setattr(_staging, "MAX_STAGING_FILES", 1)
    stage_body(str(tmp_path), "first")

    with pytest.raises(ValueError, match="staging limit reached"):
        stage_body(str(tmp_path), "second")


def test_stage_quota_ignores_non_handle_files(tmp_path, monkeypatch):
    staging = tmp_path / ".brain" / "local" / "staging"
    staging.mkdir(parents=True)
    (staging / "interrupted-write.tmp").write_text("stray")
    monkeypatch.setattr(_staging, "MAX_STAGING_FILES", 1)

    result = stage_body(str(tmp_path), "body")

    assert read_staged_body(str(tmp_path), result["handle"]) == "body"


def test_stage_enforces_total_byte_quota(tmp_path, monkeypatch):
    monkeypatch.setattr(_staging, "MAX_STAGING_BYTES", 5)
    stage_body(str(tmp_path), "four")

    with pytest.raises(ValueError, match="staging limit reached"):
        stage_body(str(tmp_path), "two")


def test_stage_cli_formats_expected_staging_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(stage_cli, "find_vault_root", lambda _vault: tmp_path)
    monkeypatch.setattr(
        stage_cli, "stage_body", lambda *_args: (_ for _ in ()).throw(ValueError("quota"))
    )

    with pytest.raises(SystemExit) as exc_info:
        stage_cli.main(["--body", "content", "--vault", str(tmp_path)])

    assert exc_info.value.code == 2
    assert "quota" in capsys.readouterr().err


def test_stage_cli_normalises_real_lock_contention(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(stage_cli, "find_vault_root", lambda _vault: tmp_path)
    monkeypatch.setattr(
        stage_cli,
        "vault_mutation_lock",
        lambda root: vault_mutation_lock(root, timeout=0.05),
    )

    with vault_mutation_lock(tmp_path):
        with pytest.raises(SystemExit) as exc_info:
            stage_cli.main(["--body", "content", "--vault", str(tmp_path)])

    assert exc_info.value.code == 2
    assert "Vault is busy; retry the mutation" in capsys.readouterr().err


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX subprocess lock composition")
def test_stage_cli_waits_for_shared_cross_process_mutation_lock(tmp_path):
    brain_core = tmp_path / ".brain-core"
    brain_core.mkdir()
    (brain_core / "VERSION").write_text("test\n")
    scripts_dir = Path(__file__).resolve().parents[1] / "src" / "brain-core" / "scripts"
    env = dict(os.environ, PYTHONPATH=str(scripts_dir))
    ready = tmp_path / "child-ready"
    entered_stage = tmp_path / "entered-stage-body"
    child_code = (
        "from pathlib import Path\n"
        "import stage\n"
        f"ready=Path({str(ready)!r}); entered=Path({str(entered_stage)!r})\n"
        "real=stage.stage_body\n"
        "def marked(*args, **kwargs):\n"
        "    entered.write_text('yes')\n"
        "    return real(*args, **kwargs)\n"
        "stage.stage_body=marked\n"
        "ready.write_text('yes')\n"
        f"stage.main(['--body', 'from subprocess', '--vault', {str(tmp_path)!r}, '--json'])\n"
    )

    with vault_mutation_lock(tmp_path):
        process = subprocess.Popen(
            [sys.executable, "-c", child_code],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + 2
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        assert process.poll() is None
        assert not entered_stage.exists()

    stdout, stderr = process.communicate(timeout=3)
    assert process.returncode == 0, stderr
    assert entered_stage.exists()
    assert read_staged_body(str(tmp_path), json.loads(stdout)["handle"]) == "from subprocess"


def test_stage_strips_all_leading_marks_before_storage_and_size(tmp_path, monkeypatch):
    monkeypatch.setattr(_staging, "MAX_STAGED_BODY_BYTES", 4)
    monkeypatch.setattr(_staging, "MAX_STAGING_BYTES", 4)
    result = stage_body(tmp_path, "\ufeff\ufeffa\ufeff")
    assert result["bytes"] == 4
    assert Path(_staging._handle_path(tmp_path, result["handle"])).read_bytes() == "a\ufeff".encode()


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "utf-32"])
def test_stage_cli_body_file_uses_lossless_resolution(tmp_path, monkeypatch, capsys, encoding):
    monkeypatch.setattr(stage_cli, "find_vault_root", lambda _vault: tmp_path)
    path = tmp_path / "body.md"
    raw = "\ufeffCafé\r\nbody".encode(encoding)
    path.write_bytes(raw)
    stage_cli.main(["--body-file", str(path), "--json"])
    result = json.loads(capsys.readouterr().out)
    assert read_staged_body(tmp_path, result["handle"]) == "Café\nbody"
    assert path.read_bytes() == raw


def test_stage_cli_refuses_truncation_without_storing_body(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(stage_cli, "find_vault_root", lambda _vault: tmp_path)
    path = tmp_path / "body.md"
    path.write_bytes(b"caf\xc3\xa9\xc3")
    with pytest.raises(SystemExit) as caught:
        stage_cli.main(["--body-file", str(path)])
    assert caught.value.code == 2
    error = capsys.readouterr().err
    assert str(path) in error and "truncated_utf8" in error
    assert not (tmp_path / _staging.STAGING_DIR).exists()
    assert path.exists()


def test_stage_create_preparation_measures_and_hashes_normalised_body(tmp_path, monkeypatch):
    from _application.stage.create import StageCreateRequest, prepare
    from _application.preparation import content_digest

    monkeypatch.setattr(_staging, "MAX_STAGED_BODY_BYTES", 4)
    request = StageCreateRequest("\ufeff\ufeffa\ufeff")
    binding = prepare(None, request)
    assert binding.review["bytes"] == 4
    assert binding.review["sha256"] == content_digest("a\ufeff")
    assert binding.review["arguments"]["content"] == "a\ufeff"
    result = stage_body(tmp_path, request.content)
    assert result["bytes"] == binding.review["bytes"]
    assert content_digest(read_staged_body(tmp_path, result["handle"])) == binding.review["sha256"]


@pytest.mark.parametrize("operation", ["create", "sweep", "read", "inspect", "discard"])
@pytest.mark.parametrize("component", [".brain", ".brain/local", ".brain/local/staging"])
def test_staging_refuses_redirected_ancestor_without_effects(tmp_path, operation, component):
    from _bootstrap.owned_paths import UnsafeFilesystemLayoutError

    root = tmp_path / "vault"
    root.mkdir()
    other = tmp_path / "synthetic-other"
    other.mkdir()
    marker = other / ("a" * 32 + ".body")
    marker.write_text("unchanged")
    alias = root / component
    alias.parent.mkdir(parents=True, exist_ok=True)
    alias.symlink_to(other, target_is_directory=True)
    operations = {
        "create": lambda: stage_body(root, "new"),
        "sweep": lambda: sweep_staged_bodies(root),
        "read": lambda: read_staged_body(root, "body:" + "a" * 32),
        "inspect": lambda: _staging.inspect_staged_body(root, "body:" + "a" * 32),
        "discard": lambda: _staging.discard_staged_body(root, "body:" + "a" * 32),
    }
    with pytest.raises(UnsafeFilesystemLayoutError):
        operations[operation]()
    assert marker.read_text() == "unchanged"
    assert list(other.iterdir()) == [marker]


def test_foreign_body_names_never_count_or_expire(tmp_path, monkeypatch):
    directory = tmp_path / _staging.STAGING_DIR
    directory.mkdir(parents=True)
    foreign = directory / "foreign.body"
    foreign.write_text("foreign")
    os.utime(foreign, (1, 1))
    monkeypatch.setattr(_staging, "MAX_STAGING_FILES", 1)
    result = stage_body(tmp_path, "valid")
    assert result["warnings"]
    assert foreign.read_text() == "foreign"
    assert read_staged_body(tmp_path, result["handle"]) == "valid"
    assert sweep_staged_bodies(tmp_path) == 0


@pytest.mark.parametrize("kind", ["symlink", "directory", "hardlink", "fifo"])
def test_unsafe_owned_record_refuses_before_expiry_cleanup(tmp_path, kind):
    from _bootstrap.owned_paths import UnsafeFilesystemLayoutError

    created = stage_body(tmp_path, "keep expired")
    expired = Path(_staging._handle_path(tmp_path, created["handle"]))
    os.utime(expired, (1, 1))
    unsafe = expired.parent / ("b" * 32 + ".body")
    if kind == "symlink":
        unsafe.symlink_to(expired)
    elif kind == "directory":
        unsafe.mkdir()
    elif kind == "hardlink":
        os.link(expired, unsafe)
    else:
        if not hasattr(os, "mkfifo"):
            pytest.skip("FIFO unavailable")
        os.mkfifo(unsafe)
    with pytest.raises(UnsafeFilesystemLayoutError):
        stage_body(tmp_path, "refuse")
    assert expired.read_text() == "keep expired"
    with pytest.raises(UnsafeFilesystemLayoutError):
        _staging.inspect_staged_body_for_discard(tmp_path, "body:" + "b" * 32)


def test_staging_bounds_all_preparation_and_execution_reads(tmp_path, monkeypatch):
    result = stage_body(tmp_path, "oversized")
    monkeypatch.setattr(_staging, "MAX_STAGED_BODY_BYTES", 3)
    for reader in (read_staged_body, _staging.inspect_staged_body,
                   _staging.inspect_staged_body_for_discard):
        with pytest.raises(ValueError, match="maximum"):
            reader(tmp_path, result["handle"])
    assert _staging.consume_staged_body(tmp_path, result["handle"])


def test_missing_selected_root_is_never_created(tmp_path):
    root = tmp_path / "missing"
    with pytest.raises(FileNotFoundError):
        stage_body(root, "no root")
    assert not root.exists()
    assert _staging.inspect_staged_body_for_discard(root, "body:" + "a" * 32) is None
    assert not _staging.discard_staged_body(root, "body:" + "a" * 32)
    assert sweep_staged_bodies(root) == 0


def test_finalisation_refusal_preserves_committed_success_warning(tmp_path):
    result = stage_body(tmp_path, "body")
    path = Path(_staging._handle_path(tmp_path, result["handle"]))
    path.unlink()
    path.mkdir()
    warning = _staging.finalise_staged_body(tmp_path, result["handle"])
    assert "Mutation succeeded" in warning
    assert "Unsafe Brain-owned" in warning
    assert path.is_dir()


def test_preparation_leaves_expired_body_intact(tmp_path):
    result = stage_body(tmp_path, "body")
    path = Path(_staging._handle_path(tmp_path, result["handle"]))
    os.utime(path, (1, 1))
    with pytest.raises(ValueError, match="Expired"):
        _staging.inspect_staged_body(tmp_path, result["handle"])
    assert path.read_text() == "body"
    assert _staging.inspect_staged_body_for_discard(tmp_path, result["handle"]) == b"body"


def test_selected_root_alias_remains_supported(tmp_path):
    root = tmp_path / "canonical"
    root.mkdir()
    alias = tmp_path / "selected"
    alias.symlink_to(root, target_is_directory=True)
    result = stage_body(alias, "body")
    assert _staging.inspect_staged_body(alias, result["handle"]) == "body"
    assert read_staged_body(alias, result["handle"]) == "body"
    assert _staging.discard_staged_body(alias, result["handle"])
    assert not _staging.discard_staged_body(alias, result["handle"])


def test_observing_absent_staging_never_creates_directories(tmp_path):
    handle = "body:" + "a" * 32
    with pytest.raises(ValueError, match="Unknown"):
        _staging.inspect_staged_body(tmp_path, handle)
    assert _staging.inspect_staged_body_for_discard(tmp_path, handle) is None
    assert sweep_staged_bodies(tmp_path) == 0
    assert not _staging.discard_staged_body(tmp_path, handle)
    assert not (tmp_path / ".brain").exists()
