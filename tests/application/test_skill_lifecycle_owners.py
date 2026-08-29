"""Typed command ownership for Brain skill lifecycle operations."""

from __future__ import annotations

from pathlib import Path
import subprocess

from _application.registry import current_application_catalogue, current_request_resolver
from _application.skill.add_git import SkillAddGitRequest
from _application.skill.detach import SkillDetachRequest
from _application.skill.list import SkillListRequest
from _application.skill.status import SkillStatusRequest
from _application.skill.update import SkillUpdateRequest
from _application.skill._types import SkillState
from _application.results import ErrorCode
from _application.resource.read import ReadableResource, ResourceReadRequest
from _skill_library import service as skill_service
from command_application import application_for


def _repository(root: Path) -> Path:
    repository = root / "source"
    skill = repository / "skills" / "example"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: example\ndescription: Example\n---\n\nVersion one.\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "--quiet"], cwd=repository, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Brain Tests"], cwd=repository, check=True
    )
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(
        ["git", "commit", "--quiet", "-m", "initial"], cwd=repository, check=True
    )
    return repository


def test_skill_lifecycle_commands_are_registered_with_typed_requests():
    catalogue = current_application_catalogue()
    entries = {entry.command_id: entry for entry in catalogue.entries}
    assert {"skill.add-git", "skill.detach", "skill.list", "skill.status", "skill.update"} <= set(entries)
    assert entries["skill.list"].effect_class.value == "none"
    assert entries["skill.update"].effect_class.value == "selected_brain_mutation"

    resolver = current_request_resolver()
    assert isinstance(resolver.resolve("skill.list", {}), SkillListRequest)
    assert isinstance(
        resolver.resolve(
            "skill.add-git",
            {"repository": "/repo", "skill_path": "skills/example"},
        ),
        SkillAddGitRequest,
    )
    assert isinstance(resolver.resolve("skill.status", {}), SkillStatusRequest)
    assert isinstance(
        resolver.resolve("skill.update", {"name": "example"}), SkillUpdateRequest
    )
    assert isinstance(
        resolver.resolve("skill.detach", {"name": "example"}), SkillDetachRequest
    )


def test_skill_lifecycle_owner_installs_lists_and_detaches(
    command_vault_clone,
    tmp_path,
):
    repository = _repository(tmp_path)
    application = application_for(command_vault_clone.vault_root)

    installed = application.invoke(
        SkillAddGitRequest(str(repository), "skills/example")
    )
    readable = application.invoke(
        ResourceReadRequest(ReadableResource.SKILL, "example")
    )
    listing = application.invoke(SkillListRequest("example"))
    refreshed = application.invoke(SkillStatusRequest("example"))
    detached = application.invoke(SkillDetachRequest("example"))

    assert installed.status == "ok"
    assert installed.result.state is SkillState.IN_SYNC
    assert readable.status == "ok", readable.error.message
    assert "Version one." in readable.result.content
    assert listing.result.items[0].effective is True
    assert refreshed.result.refreshed is True
    assert detached.result.state is SkillState.USER_OWNED
    assert (command_vault_clone.vault_root / "_Config/Skills/example/SKILL.md").is_file()


def test_skill_mutation_reports_unknown_when_rollback_cannot_be_verified(
    command_vault_clone,
    tmp_path,
    monkeypatch,
):
    repository = _repository(tmp_path)
    application = application_for(command_vault_clone.vault_root)

    def fail_tracking(*_args, **_kwargs):
        raise OSError("simulated tracking failure")

    monkeypatch.setattr(skill_service, "write_tracking", fail_tracking)
    result = application.invoke(
        SkillAddGitRequest(str(repository), "skills/example")
    )

    assert result.status == "error"
    assert result.error.code is ErrorCode.COMMAND_OUTCOME_UNKNOWN
