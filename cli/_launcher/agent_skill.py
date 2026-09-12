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
    GROK = "grok"
    ALL = "all"


class AgentSkillAction(str, Enum):
    CONFIGURE = "configure"
    REMOVE = "remove"


class AgentSkillMutationStatus(str, Enum):
    NOOP = "noop"
    PLANNED = "planned"
    CHANGED = "changed"


class SkillExposureScope(str, Enum):
    GLOBAL = "global"
    PROJECT = "project"


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
        expected = tuple(
            client for client in ("claude", "codex", "grok") if client in clients
        )
        if clients != expected or len(clients) != len(set(clients)):
            raise ValueError("agent-skill result clients must be ordered and unique")


@dataclass(frozen=True, slots=True)
class AgentSkillConfigureRequest:
    COMMAND_ID: ClassVar[str] = "agent-skill.configure"
    COMMAND_VERSION: ClassVar[int] = 2
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


@dataclass(frozen=True, slots=True)
class SkillExposureStep:
    client: AgentSkillClient
    status: AgentSkillMutationStatus
    destination: str
    message: str
    backup_path: str | None = None

    def __post_init__(self) -> None:
        if self.client is AgentSkillClient.ALL:
            raise ValueError("skill exposure steps require a concrete client")
        if not Path(self.destination).is_absolute():
            raise ValueError("skill exposure destinations must be absolute")
        if self.backup_path is not None and not Path(self.backup_path).is_absolute():
            raise ValueError("skill exposure backup paths must be absolute")


@dataclass(frozen=True, slots=True)
class SkillExposurePayload:
    name: str
    scope: SkillExposureScope
    steps: tuple[SkillExposureStep, ...]


@dataclass(frozen=True, slots=True)
class SkillExposeRequest:
    COMMAND_ID: ClassVar[str] = "skill.expose"
    COMMAND_VERSION: ClassVar[int] = 2
    RESULT_TYPE: ClassVar[type] = SkillExposurePayload

    name: str
    client: AgentSkillClient = AgentSkillClient.ALL
    scope: SkillExposureScope = SkillExposureScope.GLOBAL
    replace: bool = False

    def __post_init__(self) -> None:
        _validate_exposure_request(self.name, self.client, self.scope)
        if not isinstance(self.replace, bool):
            raise ValueError("skill exposure replace must be a boolean")


@dataclass(frozen=True, slots=True)
class SkillUnexposeRequest:
    COMMAND_ID: ClassVar[str] = "skill.unexpose"
    COMMAND_VERSION: ClassVar[int] = 2
    RESULT_TYPE: ClassVar[type] = SkillExposurePayload

    name: str
    client: AgentSkillClient = AgentSkillClient.ALL
    scope: SkillExposureScope = SkillExposureScope.GLOBAL

    def __post_init__(self) -> None:
        _validate_exposure_request(self.name, self.client, self.scope)


def _validate_exposure_request(name, client, scope):
    if not isinstance(name, str) or not name.strip():
        raise ValueError("skill exposure name must be non-empty")
    if not isinstance(client, AgentSkillClient):
        raise ValueError("skill exposure client must be closed and typed")
    if not isinstance(scope, SkillExposureScope):
        raise ValueError("skill exposure scope must be closed and typed")


def _clients(client: AgentSkillClient) -> tuple[AgentSkillClient, ...]:
    if client is AgentSkillClient.ALL:
        return (AgentSkillClient.CLAUDE, AgentSkillClient.CODEX, AgentSkillClient.GROK)
    return (client,)


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
        status, message, backup_path = agent_skills.remove_prepared_skill_adapter(
            context.home_dir,
            client.value,
            adapter_content,
            dry_run=dry_run,
        )
    else:
        assert adapter_content is not None
        status, message, backup_path = agent_skills.install_prepared_skill_adapter(
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
            for client in _clients(request.client)
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
        for client in _clients(request.client)
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


def _exposure_root(context: LauncherContext, scope: SkillExposureScope) -> Path:
    if scope is SkillExposureScope.GLOBAL:
        return context.home_dir
    if context.workspace_dir is None:
        raise ValueError(
            "project skill exposure requires a canonically bound workspace"
        )
    workspace = context.workspace_dir
    if workspace.is_symlink() or not workspace.is_dir():
        raise ValueError(f"project skill exposure workspace is unsafe: {workspace}")
    from _bootstrap.workspace_binding import WorkspaceBindingError, resolve_brain_target

    try:
        target = resolve_brain_target(
            workspace_env=None,
            vault_root_env=None,
            start_dir=workspace,
        )
    except WorkspaceBindingError as exc:
        raise ValueError(str(exc)) from exc
    if Path(target.vault_root).resolve() != context.current_vault.resolve():
        raise ValueError(
            "project workspace binding does not resolve to the selected Brain"
        )
    return workspace


def _exposure_step(root, request, client, outcome):
    from _bootstrap import agent_skills

    status, message, backup = outcome
    destination = root / agent_skills.CLIENT_SKILLS_DIRS[client.value] / request.name
    return SkillExposureStep(
        client,
        AgentSkillMutationStatus(status),
        str(destination),
        message,
        str(backup) if backup is not None else None,
    )


def _exposure_effects(request, steps):
    effects = []
    for step in steps:
        if step.status is not AgentSkillMutationStatus.CHANGED:
            continue
        effects.append(CommittedEffect(request.COMMAND_ID, step.destination))
        if step.backup_path is not None:
            effects.append(CommittedEffect(request.COMMAND_ID, step.backup_path))
    return tuple(effects)


def execute_expose(context: LauncherContext, request: SkillExposeRequest):
    from _bootstrap import agent_skills

    if context.current_vault is None:
        return no_effect_error(
            type(request),
            ErrorCode.NOT_FOUND,
            "Skill exposure requires an active Brain.",
        )
    try:
        root = _exposure_root(context, request.scope)
        content = agent_skills.load_effective_skill_adapter(
            context.current_vault, request.name
        )

        def run(client, *, dry_run):
            return _exposure_step(
                root,
                request,
                client,
                agent_skills.install_prepared_skill_adapter(
                    root,
                    client.value,
                    content,
                    replace=request.replace,
                    dry_run=dry_run,
                    skill_name=request.name,
                ),
            )

        planned = tuple(
            run(client, dry_run=True)
            for client in _clients(request.client)
        )
    except (agent_skills.AgentSkillConfigError, OSError, ValueError) as exc:
        return no_effect_error(type(request), ErrorCode.CONFLICT, str(exc))
    if context.dry_run:
        return Ok(
            request.COMMAND_ID,
            request.COMMAND_VERSION,
            SkillExposurePayload(request.name, request.scope, planned),
        )
    applied = tuple(
        run(client, dry_run=False)
        for client in _clients(request.client)
    )
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        SkillExposurePayload(request.name, request.scope, applied),
        _exposure_effects(request, applied),
    )


def execute_unexpose(context: LauncherContext, request: SkillUnexposeRequest):
    from _bootstrap import agent_skills

    if context.current_vault is None:
        return no_effect_error(
            type(request),
            ErrorCode.NOT_FOUND,
            "Skill exposure requires an active Brain.",
        )
    try:
        root = _exposure_root(context, request.scope)
        try:
            content = agent_skills.load_effective_skill_adapter(
                context.current_vault, request.name
            )
        except agent_skills.AgentSkillConfigError:
            content = None

        def run(client, *, dry_run):
            return _exposure_step(
                root,
                request,
                client,
                agent_skills.remove_prepared_skill_adapter(
                    root,
                    client.value,
                    content,
                    dry_run=dry_run,
                    skill_name=request.name,
                ),
            )

        planned = tuple(
            run(client, dry_run=True)
            for client in _clients(request.client)
        )
    except (agent_skills.AgentSkillConfigError, OSError, ValueError) as exc:
        return no_effect_error(type(request), ErrorCode.CONFLICT, str(exc))
    if context.dry_run:
        return Ok(
            request.COMMAND_ID,
            request.COMMAND_VERSION,
            SkillExposurePayload(request.name, request.scope, planned),
        )
    applied = tuple(
        run(client, dry_run=False)
        for client in _clients(request.client)
    )
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        SkillExposurePayload(request.name, request.scope, applied),
        _exposure_effects(request, applied),
    )


def expose_owner():
    from .owners import LauncherOwner

    return LauncherOwner(
        SkillExposeRequest,
        SkillExposurePayload,
        "_launcher.agent_skill:expose",
        execute_expose,
    )


def unexpose_owner():
    from .owners import LauncherOwner

    return LauncherOwner(
        SkillUnexposeRequest,
        SkillExposurePayload,
        "_launcher.agent_skill:unexpose",
        execute_unexpose,
    )
