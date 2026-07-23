"""Tests for ownership-safe active-Brain client skill adapters."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path

import pytest

from _bootstrap import agent_skills
import upgrade


ADAPTER_CONTENT = agent_skills.load_shaping_adapter()


def _skill_dir(home, client):
    return home / f".{client}" / "skills" / "shaping"


def _marker_for(content):
    return {
        "schema_version": 1,
        "owner": "obsidian-brain",
        "kind": "active-brain-skill-adapter",
        "skill": "shaping",
        "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
    }


def test_install_configures_both_clients_from_one_adapter(tmp_path):
    steps = agent_skills.configure_agent_skill_adapters(
        home_dir=tmp_path,
        client="all",
    )

    assert [step["status"] for step in steps] == ["changed", "changed"]
    for client in ("claude", "codex"):
        skill_dir = _skill_dir(tmp_path, client)
        content = (skill_dir / "SKILL.md").read_text()
        marker = json.loads((skill_dir / agent_skills.MARKER_FILE).read_text())
        assert content == ADAPTER_CONTENT
        assert "brain_session" in content
        assert '.brain-core/skills/shaping/SKILL.md' in content
        assert "start-shaping" not in content
        assert marker == _marker_for(content)


def test_adapter_content_comes_from_the_checked_in_upgrade_identity():
    assert agent_skills.ADAPTER_TEMPLATE_FILE.is_file()
    assert agent_skills.ADAPTER_TEMPLATE_FILE.read_text() == ADAPTER_CONTENT
    assert upgrade.AGENT_SKILL_ADAPTER_REL == str(agent_skills.ADAPTER_TEMPLATE_REL)


def test_import_does_not_read_adapter_template(monkeypatch):
    original_read_text = Path.read_text

    def guarded_read_text(path, *args, **kwargs):
        if tuple(path.parts[-3:]) == ("client-adapters", "shaping", "SKILL.md"):
            raise AssertionError("adapter template read during module import")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read_text)

    importlib.reload(agent_skills)


def test_install_is_idempotent(tmp_path):
    agent_skills.configure_agent_skill_adapters(home_dir=tmp_path, client="claude")

    steps = agent_skills.configure_agent_skill_adapters(
        home_dir=tmp_path,
        client="claude",
    )

    assert steps[0]["status"] == "noop"


def test_incidental_finder_metadata_does_not_block_management(tmp_path):
    skill_dir = _skill_dir(tmp_path, "claude")
    skill_dir.mkdir(parents=True)
    (skill_dir / ".DS_Store").write_bytes(b"finder metadata")

    steps = agent_skills.configure_agent_skill_adapters(
        home_dir=tmp_path,
        client="claude",
    )

    assert steps[0]["status"] == "changed"
    assert (skill_dir / "SKILL.md").read_text() == ADAPTER_CONTENT
    assert (skill_dir / ".DS_Store").read_bytes() == b"finder metadata"


def test_install_updates_an_unmodified_managed_adapter(tmp_path):
    skill_dir = _skill_dir(tmp_path, "codex")
    skill_dir.mkdir(parents=True)
    old_content = "---\nname: shaping\n---\nOld managed adapter.\n"
    (skill_dir / "SKILL.md").write_text(old_content)
    (skill_dir / agent_skills.MARKER_FILE).write_text(
        json.dumps(_marker_for(old_content))
    )

    steps = agent_skills.configure_agent_skill_adapters(
        home_dir=tmp_path,
        client="codex",
    )

    assert steps[0]["status"] == "changed"
    assert (skill_dir / "SKILL.md").read_text() == ADAPTER_CONTENT


def test_install_repairs_marker_after_interrupted_content_update(tmp_path):
    skill_dir = _skill_dir(tmp_path, "claude")
    skill_dir.mkdir(parents=True)
    old_content = "old adapter"
    (skill_dir / "SKILL.md").write_text(ADAPTER_CONTENT)
    (skill_dir / agent_skills.MARKER_FILE).write_text(
        json.dumps(_marker_for(old_content))
    )

    steps = agent_skills.configure_agent_skill_adapters(
        home_dir=tmp_path,
        client="claude",
    )

    assert steps[0]["status"] == "changed"
    marker = json.loads((skill_dir / agent_skills.MARKER_FILE).read_text())
    assert marker == _marker_for(ADAPTER_CONTENT)


def test_unmanaged_skill_is_preserved_and_reported(tmp_path):
    skill_dir = _skill_dir(tmp_path, "claude")
    skill_dir.mkdir(parents=True)
    original = "---\nname: shaping\n---\nCustom workflow.\n"
    (skill_dir / "SKILL.md").write_text(original)

    steps = agent_skills.configure_agent_skill_adapters(
        home_dir=tmp_path,
        client="claude",
    )

    assert steps[0]["status"] == "error"
    assert "unmanaged skill" in steps[0]["message"]
    assert (skill_dir / "SKILL.md").read_text() == original
    assert not (skill_dir / agent_skills.MARKER_FILE).exists()


def test_replace_archives_unmanaged_skill_before_install(tmp_path):
    skill_dir = _skill_dir(tmp_path, "codex")
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("custom workflow")
    (skill_dir / "refine").mkdir()
    (skill_dir / "refine" / "SKILL.md").write_text("custom sub-skill")

    steps = agent_skills.configure_agent_skill_adapters(
        home_dir=tmp_path,
        client="codex",
        replace=True,
    )

    backup = skill_dir.parent / "shaping.pre-brain-adapter"
    assert steps[0]["status"] == "changed"
    assert (backup / "SKILL.md").read_text() == "custom workflow"
    assert (backup / "refine" / "SKILL.md").read_text() == "custom sub-skill"
    assert (skill_dir / "SKILL.md").read_text() == ADAPTER_CONTENT


def test_missing_template_is_a_scoped_configuration_error(tmp_path, monkeypatch):
    monkeypatch.setattr(
        agent_skills,
        "ADAPTER_TEMPLATE_FILE",
        tmp_path / "missing" / "SKILL.md",
    )

    steps = agent_skills.configure_agent_skill_adapters(
        home_dir=tmp_path,
        client="all",
    )

    assert [step["status"] for step in steps] == ["error", "error"]
    assert all("cannot read shaping adapter template" in step["message"] for step in steps)
    assert not (tmp_path / ".claude").exists()
    assert not (tmp_path / ".codex").exists()


def test_replace_uses_numbered_backup_when_first_backup_exists(tmp_path):
    skill_dir = _skill_dir(tmp_path, "codex")
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("custom workflow")
    first_backup = skill_dir.parent / "shaping.pre-brain-adapter"
    first_backup.mkdir()

    steps = agent_skills.configure_agent_skill_adapters(
        home_dir=tmp_path,
        client="codex",
        replace=True,
    )

    assert steps[0]["status"] == "changed"
    assert (first_backup.parent / "shaping.pre-brain-adapter-2" / "SKILL.md").read_text() == (
        "custom workflow"
    )
    assert first_backup.is_dir()


def test_modified_managed_adapter_is_not_overwritten(tmp_path):
    agent_skills.configure_agent_skill_adapters(home_dir=tmp_path, client="claude")
    skill_file = _skill_dir(tmp_path, "claude") / "SKILL.md"
    skill_file.write_text(skill_file.read_text() + "\nLocal edit.\n")

    steps = agent_skills.configure_agent_skill_adapters(
        home_dir=tmp_path,
        client="claude",
    )

    assert steps[0]["status"] == "error"
    assert "was modified" in steps[0]["message"]
    assert "Local edit." in skill_file.read_text()


def test_invalid_ownership_marker_is_not_treated_as_managed(tmp_path):
    skill_dir = _skill_dir(tmp_path, "claude")
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("custom workflow")
    (skill_dir / agent_skills.MARKER_FILE).write_text('{"owner": "someone-else"}')

    steps = agent_skills.configure_agent_skill_adapters(
        home_dir=tmp_path,
        client="claude",
    )

    assert steps[0]["status"] == "error"
    assert "unrecognised Brain ownership marker" in steps[0]["message"]
    assert (skill_dir / "SKILL.md").read_text() == "custom workflow"


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks are unavailable")
def test_symlinked_skill_directory_is_never_followed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_skill = outside / "SKILL.md"
    outside_skill.write_text("do not replace")
    skill_dir = _skill_dir(tmp_path, "claude")
    skill_dir.parent.mkdir(parents=True)
    skill_dir.symlink_to(outside, target_is_directory=True)

    steps = agent_skills.configure_agent_skill_adapters(
        home_dir=tmp_path,
        client="claude",
        replace=True,
    )

    assert steps[0]["status"] == "error"
    assert "symlinked client skill destination" in steps[0]["message"]
    assert outside_skill.read_text() == "do not replace"


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks are unavailable")
def test_symlinked_client_skills_root_is_never_followed(tmp_path):
    outside = tmp_path / "outside-skills"
    outside.mkdir()
    client_root = tmp_path / ".codex"
    client_root.mkdir()
    (client_root / "skills").symlink_to(outside, target_is_directory=True)

    steps = agent_skills.configure_agent_skill_adapters(
        home_dir=tmp_path,
        client="codex",
    )

    assert steps[0]["status"] == "error"
    assert "symlinked client skill destination" in steps[0]["message"]
    assert not (outside / "shaping").exists()


def test_remove_deletes_only_unmodified_managed_adapter(tmp_path):
    agent_skills.configure_agent_skill_adapters(home_dir=tmp_path, client="codex")

    steps = agent_skills.configure_agent_skill_adapters(
        home_dir=tmp_path,
        client="codex",
        remove=True,
    )

    assert steps[0]["status"] == "changed"
    assert not _skill_dir(tmp_path, "codex").exists()


@pytest.mark.parametrize("incidental_name", [".DS_Store", "Thumbs.db"])
def test_remove_cleans_incidental_metadata_before_managed_files(
    tmp_path, incidental_name
):
    agent_skills.configure_agent_skill_adapters(home_dir=tmp_path, client="codex")
    skill_dir = _skill_dir(tmp_path, "codex")
    (skill_dir / incidental_name).write_bytes(b"incidental metadata")

    steps = agent_skills.configure_agent_skill_adapters(
        home_dir=tmp_path,
        client="codex",
        remove=True,
    )

    assert steps[0]["status"] == "changed"
    assert not skill_dir.exists()


def test_remove_refuses_modified_managed_adapter(tmp_path):
    agent_skills.configure_agent_skill_adapters(home_dir=tmp_path, client="codex")
    skill_file = _skill_dir(tmp_path, "codex") / "SKILL.md"
    skill_file.write_text("locally modified")

    steps = agent_skills.configure_agent_skill_adapters(
        home_dir=tmp_path,
        client="codex",
        remove=True,
    )

    assert steps[0]["status"] == "error"
    assert skill_file.read_text() == "locally modified"


def test_all_clients_report_partial_success_independently(tmp_path):
    skill_dir = _skill_dir(tmp_path, "claude")
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("custom")

    steps = agent_skills.configure_agent_skill_adapters(
        home_dir=tmp_path,
        client="all",
    )

    assert [step["status"] for step in steps] == ["error", "changed"]
    assert (_skill_dir(tmp_path, "codex") / "SKILL.md").is_file()


def test_value_error_is_contained_to_the_failing_client(tmp_path, monkeypatch):
    original_install = agent_skills._install_client_adapter

    def fail_codex(home_dir, client, content, *, replace):
        if client == "codex":
            raise ValueError("path bounds mismatch")
        return original_install(
            home_dir,
            client,
            content,
            replace=replace,
        )

    monkeypatch.setattr(agent_skills, "_install_client_adapter", fail_codex)

    steps = agent_skills.configure_agent_skill_adapters(
        home_dir=tmp_path,
        client="all",
    )

    assert [step["status"] for step in steps] == ["changed", "error"]
    assert steps[1]["message"] == "path bounds mismatch"


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks are unavailable")
def test_symlinked_home_bounds_errors_are_structured_per_client(tmp_path):
    real_home = tmp_path / "real-home"
    real_home.mkdir()
    linked_home = tmp_path / "linked-home"
    linked_home.symlink_to(real_home, target_is_directory=True)

    steps = agent_skills.configure_agent_skill_adapters(
        home_dir=linked_home,
        client="all",
    )

    assert [step["status"] for step in steps] == ["error", "error"]
    assert all("outside allowed boundary" in step["message"] for step in steps)
