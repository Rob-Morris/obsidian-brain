"""Typed machine-global active-Brain skill-adapter owner."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import ClassVar

from .context import LauncherContext
from .contracts import CommittedEffect, ErrorCode, Ok, no_effect_error


class AgentSkillClient(str, Enum):
    CLAUDE = "claude"
    CODEX = "codex"
    ALL = "all"


class AgentSkillAction(str, Enum):
    CONFIGURE = "configure"
    REMOVE = "remove"


class AgentSkillMutationStatus(str, Enum):
    NOOP = "noop"
    PLANNED = "planned"
    CHANGED = "changed"


@dataclass(frozen=True, slots=True)
class AgentSkillStep:
    client: AgentSkillClient
    status: AgentSkillMutationStatus
    message: str
    backup_path: str | None = None

    def __post_init__(self) -> None:
        if self.client is AgentSkillClient.ALL:
            raise ValueError("agent-skill result steps require one concrete client")
        if not isinstance(self.status, AgentSkillMutationStatus):
            raise ValueError("agent-skill result status must be closed and typed")
        if not self.message.strip():
            raise ValueError("agent-skill result messages must be non-empty")
        if self.backup_path is not None and not Path(self.backup_path).is_absolute():
            raise ValueError("agent-skill backup paths must be absolute")


@dataclass(frozen=True, slots=True)
class AgentSkillConfigurePayload:
    action: AgentSkillAction
    steps: tuple[AgentSkillStep, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.action, AgentSkillAction):
            raise ValueError("agent-skill action must be closed and typed")
        if not self.steps or any(not isinstance(step, AgentSkillStep) for step in self.steps):
            raise ValueError("agent-skill payload requires typed client steps")
        clients = tuple(step.client.value for step in self.steps)
        expected = tuple(client for client in ("claude", "codex") if client in clients)
        if clients != expected or len(clients) != len(set(clients)):
            raise ValueError("agent-skill result clients must be ordered and unique")


@dataclass(frozen=True, slots=True)
class AgentSkillConfigureRequest:
    COMMAND_ID: ClassVar[str] = "agent-skill.configure"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = AgentSkillConfigurePayload

    client: AgentSkillClient = AgentSkillClient.ALL
    action: AgentSkillAction = AgentSkillAction.CONFIGURE
    replace: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.client, AgentSkillClient):
            raise ValueError("agent-skill client must be closed and typed")
        if not isinstance(self.action, AgentSkillAction):
            raise ValueError("agent-skill action must be closed and typed")
        if not isinstance(self.replace, bool):
            raise ValueError("agent-skill replace must be a boolean")
        if self.action is AgentSkillAction.REMOVE and self.replace:
            raise ValueError("agent-skill replace applies only to configure")


def _clients(request: AgentSkillConfigureRequest) -> tuple[AgentSkillClient, ...]:
    if request.client is AgentSkillClient.ALL:
        return (AgentSkillClient.CLAUDE, AgentSkillClient.CODEX)
    return (request.client,)


def _load_adapter(action: AgentSkillAction):
    from _bootstrap import agent_skills

    if action is AgentSkillAction.CONFIGURE:
        return agent_skills.load_shaping_adapter()
    try:
        return agent_skills.load_shaping_adapter()
    except OSError:
        return None


def _run_client(
    context: LauncherContext,
    request: AgentSkillConfigureRequest,
    client: AgentSkillClient,
    adapter_content: str | None,
    *,
    dry_run: bool,
) -> AgentSkillStep:
    from _bootstrap import agent_skills

    if request.action is AgentSkillAction.REMOVE:
        status, message, backup_path = agent_skills._remove_client_adapter(
            context.home_dir,
            client.value,
            adapter_content,
            dry_run=dry_run,
        )
    else:
        assert adapter_content is not None
        status, message, backup_path = agent_skills._install_client_adapter(
            context.home_dir,
            client.value,
            adapter_content,
            replace=request.replace,
            dry_run=dry_run,
        )
    return AgentSkillStep(
        client,
        AgentSkillMutationStatus(status),
        message,
        str(backup_path) if backup_path is not None else None,
    )


def _effects(request: AgentSkillConfigureRequest, steps: tuple[AgentSkillStep, ...]):
    effects = []
    for step in steps:
        if step.status is not AgentSkillMutationStatus.CHANGED:
            continue
        effects.append(
            CommittedEffect(
                request.COMMAND_ID,
                f"agent-skill:{step.client.value}:shaping",
            )
        )
        if step.backup_path is not None:
            effects.append(
                CommittedEffect(
                    request.COMMAND_ID,
                    f"agent-skill-backup:{step.backup_path}",
                )
            )
    return tuple(effects)


def execute_configure(
    context: LauncherContext,
    request: AgentSkillConfigureRequest,
):
    from _bootstrap import agent_skills

    try:
        adapter_content = _load_adapter(request.action)
        planned = tuple(
            _run_client(
                context,
                request,
                client,
                adapter_content,
                dry_run=True,
            )
            for client in _clients(request)
        )
    except (agent_skills.AgentSkillConfigError, OSError, ValueError) as exc:
        return no_effect_error(type(request), ErrorCode.CONFLICT, str(exc))

    if context.dry_run:
        return Ok(
            request.COMMAND_ID,
            request.COMMAND_VERSION,
            AgentSkillConfigurePayload(request.action, planned),
        )

    applied = tuple(
        _run_client(
            context,
            request,
            client,
            adapter_content,
            dry_run=False,
        )
        for client in _clients(request)
    )
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        AgentSkillConfigurePayload(request.action, applied),
        _effects(request, applied),
    )


def configure_owner():
    from .owners import LauncherOwner

    return LauncherOwner(
        AgentSkillConfigureRequest,
        AgentSkillConfigurePayload,
        "_launcher.agent_skill:configure",
        execute_configure,
    )
