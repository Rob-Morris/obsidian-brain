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
