"""Tests for the attachment-upload script and domain operation."""

from __future__ import annotations

import base64
import json
from pathlib import Path
import subprocess
import sys

import pytest

import upload_attachment


def _upload(vault, *, destination_key="attachment-tests", **kwargs):
    return upload_attachment.upload_attachment(
        vault,
        {"artefact_index": {}},
        destination_key=destination_key,
        **kwargs,
    )


def test_upload_attachment_creates_binary_file_and_embed(tmp_path):
    content = b"\x89PNG\r\n\x1a\nbinary"

    result = _upload(
        tmp_path,
        name="diagram.png",
        content=content,
    )

    assert (tmp_path / result["path"]).read_bytes() == content
    assert result["destination"] == {
        "kind": "folder",
        "key": "attachment-tests",
        "folder": "attachment-tests",
    }
    assert result["path"] == "_Assets/Attachments/attachment-tests/diagram.png"
    assert result["embed"] == "![[_Assets/Attachments/attachment-tests/diagram.png]]"
    assert result["bytes"] == len(content)
    assert result["created"] is True


def test_upload_attachment_retries_identical_content_idempotently(tmp_path):
    first = _upload(
        tmp_path,
        name="diagram.svg",
        content=b"<svg/>",
    )
    second = _upload(
        tmp_path,
        name="diagram.svg",
        content=b"<svg/>",
    )

    assert first["created"] is True
    assert second == {**first, "created": False}


def test_upload_attachment_rejects_different_content_collision(tmp_path):
    _upload(tmp_path, name="diagram.svg", content=b"first")

    with pytest.raises(FileExistsError, match="different content"):
        _upload(
            tmp_path,
            name="diagram.svg",
            content=b"second",
        )

    assert (
        tmp_path / "_Assets" / "Attachments" / "attachment-tests" / "diagram.svg"
    ).read_bytes() == b"first"


def test_upload_attachment_resolves_slash_and_scope_artefact_keys(tmp_path):
    router = {"artefact_index": {"design/brain": {"path": "Designs/Brain.md"}}}

    slash = upload_attachment.upload_attachment(
        tmp_path,
        router,
        destination_key="design/brain",
        name="slash.svg",
        content=b"slash",
    )
    scope = upload_attachment.upload_attachment(
        tmp_path,
        router,
        destination_key="design~brain",
        name="scope.svg",
        content=b"scope",
    )

    assert slash["destination"] == {
        "kind": "artefact",
        "key": "design/brain",
        "folder": "design~brain",
    }
    assert slash["path"] == "_Assets/Attachments/design~brain/slash.svg"
    assert scope["path"] == "_Assets/Attachments/design~brain/scope.svg"


@pytest.mark.parametrize(
    "destination_key",
    ["design/missing", "design~missing", "bad/path/shape", "Bad Folder", "123"],
)
def test_upload_attachment_rejects_invalid_or_unknown_destinations(
    tmp_path, destination_key
):
    with pytest.raises(ValueError):
        _upload(
            tmp_path,
            destination_key=destination_key,
            name="diagram.svg",
            content=b"content",
        )


def test_same_filename_is_independent_across_attachment_folders(tmp_path):
    first = _upload(
        tmp_path,
        destination_key="first-scope",
        name="diagram.svg",
        content=b"first",
    )
    second = _upload(
        tmp_path,
        destination_key="second-scope",
        name="diagram.svg",
        content=b"second",
    )

    assert first["path"] != second["path"]
    assert (tmp_path / first["path"]).read_bytes() == b"first"
    assert (tmp_path / second["path"]).read_bytes() == b"second"


def test_attachment_scope_move_planning_surfaces_enumeration_errors(
    tmp_path, monkeypatch
):
    scope = tmp_path / "_Assets" / "Attachments" / "ideas~old"
    scope.mkdir(parents=True)
    (scope / "diagram.svg").write_text("<svg />")

    def unreadable_walk(_path, *, followlinks, onerror):
        assert followlinks is False
        onerror(PermissionError("scope unreadable"))
        return iter(())

    monkeypatch.setattr(upload_attachment.os, "walk", unreadable_walk)

    with pytest.raises(OSError, match="Cannot enumerate attachment scope"):
        upload_attachment.plan_attachment_scope_moves(
            tmp_path, "ideas/old", "ideas/new"
        )


@pytest.mark.parametrize(
    "name",
    [
        "../escape.png",
        "nested/escape.png",
        r"nested\escape.png",
        ".hidden.png",
        "artefact.md",
        "bad|alias.png",
        "bad#heading.png",
        'bad"name.png',
        "bad:name.png",
        "bad*name.png",
        "bad?name.png",
        "trailing.",
        "CON.png",
        "NUL",
        "LPT1.pdf",
    ],
)
def test_upload_attachment_rejects_unsafe_names(tmp_path, name):
    with pytest.raises(ValueError):
        _upload(tmp_path, name=name, content=b"content")


def test_upload_attachment_rejects_symlink_escape(tmp_path):
    outside = tmp_path.parent / "outside-attachments"
    outside.mkdir(exist_ok=True)
    assets = tmp_path / "_Assets"
    assets.mkdir()
    (assets / "Attachments").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="must not contain symlinks"):
        _upload(
            tmp_path,
            name="escape.png",
            content=b"content",
        )

    assert not (outside / "escape.png").exists()


def test_upload_attachment_rejects_in_vault_namespace_symlink(tmp_path):
    redirected = tmp_path / "Wiki"
    redirected.mkdir()
    assets = tmp_path / "_Assets"
    assets.mkdir()
    (assets / "Attachments").symlink_to(redirected, target_is_directory=True)

    with pytest.raises(ValueError, match="must not contain symlinks"):
        _upload(
            tmp_path,
            name="redirected.png",
            content=b"content",
        )

    assert not (redirected / "redirected.png").exists()


def test_upload_attachment_rejects_destination_folder_symlink(tmp_path):
    redirected = tmp_path / "redirected"
    redirected.mkdir()
    attachments = tmp_path / "_Assets" / "Attachments"
    attachments.mkdir(parents=True)
    (attachments / "attachment-tests").symlink_to(
        redirected, target_is_directory=True
    )

    with pytest.raises(ValueError, match="must not contain symlinks"):
        _upload(tmp_path, name="redirected.png", content=b"content")

    assert not (redirected / "redirected.png").exists()


def test_decode_attachment_base64_is_strict_and_bounded(monkeypatch):
    assert upload_attachment.decode_attachment_base64(
        base64.b64encode(b"content").decode("ascii")
    ) == b"content"

    with pytest.raises(ValueError, match="not valid base64"):
        upload_attachment.decode_attachment_base64("not base64")

    monkeypatch.setattr(upload_attachment, "MAX_ATTACHMENT_BYTES", 2)
    with pytest.raises(ValueError, match="maximum"):
        upload_attachment.decode_attachment_base64(
            base64.b64encode(b"three").decode("ascii")
        )


def test_decode_attachment_base64_rejects_oversize_before_decode(monkeypatch):
    monkeypatch.setattr(upload_attachment, "MAX_ATTACHMENT_BYTES", 2)

    def unexpected_decode(*args, **kwargs):
        raise AssertionError("oversized input must be rejected before decoding")

    monkeypatch.setattr(upload_attachment.base64, "b64decode", unexpected_decode)

    with pytest.raises(ValueError, match="decoded maximum"):
        upload_attachment.decode_attachment_base64("A" * 8)


def test_upload_attachment_cli_reads_caller_file_and_returns_json(tmp_path):
    vault = tmp_path / "vault"
    (vault / ".brain-core").mkdir(parents=True)
    (vault / ".brain-core" / "VERSION").write_text("test\n")
    source = tmp_path / "source.svg"
    source.write_bytes(b"<svg>corrected</svg>")
    script = (
        Path(__file__).parents[1]
        / "src"
        / "brain-core"
        / "scripts"
        / "upload_attachment.py"
    )

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--file",
            str(source),
            "--destination-key",
            "cli-assets",
            "--vault",
            str(vault),
            "--json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    assert payload["path"] == "_Assets/Attachments/cli-assets/source.svg"
    assert payload["embed"] == "![[_Assets/Attachments/cli-assets/source.svg]]"
    assert (vault / payload["path"]).read_bytes() == source.read_bytes()


def test_upload_attachment_cli_accepts_empty_base64(tmp_path):
    vault = tmp_path / "vault"
    (vault / ".brain-core").mkdir(parents=True)
    (vault / ".brain-core" / "VERSION").write_text("test\n")
    script = (
        Path(__file__).parents[1]
        / "src"
        / "brain-core"
        / "scripts"
        / "upload_attachment.py"
    )

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--content-base64",
            "",
            "--destination-key",
            "cli-assets",
            "--name",
            "empty.bin",
            "--vault",
            str(vault),
            "--json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    assert payload["bytes"] == 0
    assert (vault / payload["path"]).read_bytes() == b""


def test_upload_attachment_cli_requires_destination_key(tmp_path):
    vault = tmp_path / "vault"
    (vault / ".brain-core").mkdir(parents=True)
    (vault / ".brain-core" / "VERSION").write_text("test\n")
    script = (
        Path(__file__).parents[1]
        / "src"
        / "brain-core"
        / "scripts"
        / "upload_attachment.py"
    )

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--content-base64",
            "",
            "--name",
            "empty.bin",
            "--vault",
            str(vault),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "--destination-key" in result.stderr
    assert not (vault / "_Assets").exists()


def test_upload_attachment_cli_validates_destination_before_reading_source(tmp_path):
    vault = tmp_path / "vault"
    (vault / ".brain-core").mkdir(parents=True)
    (vault / ".brain-core" / "VERSION").write_text("test\n")
    script = (
        Path(__file__).parents[1]
        / "src"
        / "brain-core"
        / "scripts"
        / "upload_attachment.py"
    )

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--file",
            str(tmp_path / "missing.svg"),
            "--destination-key",
            "Bad Folder",
            "--vault",
            str(vault),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "INVALID_KEY" in result.stderr
    assert "Cannot read attachment source" not in result.stderr
