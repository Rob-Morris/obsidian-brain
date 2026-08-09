"""Typed managed ``session.start`` bootstrap owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from ..context import InvocationContext
from ..results import ErrorCode, Ok
from ..types import (
    Authority,
    Availability,
    DependencyTier,
    EffectClass,
    Locality,
    Projection,
    ProjectionEligibility,
    RetryClass,
)
from .._read_support import command_error


@dataclass(frozen=True, slots=True)
class SessionLoadInstruction:
    tool: str
    resource: str
    name: str


@dataclass(frozen=True, slots=True)
class SessionCoreDocument:
    title: str
    path: str
    load_with: SessionLoadInstruction


@dataclass(frozen=True, slots=True)
class SessionCoreDocumentSection:
    section: str
    docs: tuple[SessionCoreDocument, ...]


@dataclass(frozen=True, slots=True)
class SessionTrigger:
    category: str
    condition: str
    detail: str | None
    target: str | None


@dataclass(frozen=True, slots=True)
class SessionArtefactType:
    type: str
    key: str
    path: str
    naming_pattern: str | None
    status_enum: tuple[str, ...]
    configured: bool


@dataclass(frozen=True, slots=True)
class SessionEnvironment:
    vault_root: str
    platform: str
    python_version: str
    cli_available: bool
    obsidian_cli_available: bool


@dataclass(frozen=True, slots=True)
class SessionMemory:
    name: str
    triggers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SessionSkill:
    name: str
    source: str


@dataclass(frozen=True, slots=True)
class SessionWorkspaceConfiguration:
    surface: str
    binding_status: str
    purpose: str
    command: str
    selection: str
    remote_boundary: str
    effect: str


@dataclass(frozen=True, slots=True)
class SessionConfig:
    brain_name: str
    default_profile: str
    profiles: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SessionWorkspace:
    directory: str
    name: str
    location: str


@dataclass(frozen=True, slots=True)
class SessionWorkspaceBinding:
    brain: str
    slug: str


@dataclass(frozen=True, slots=True)
class SessionWorkspaceRecord:
    slug: str
    workspace_mode: str
    hub_path: str | None
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SessionStartPayload:
    version: str
    brain_core_version: str
    compiled_at: str
    core_bootstrap: str
    core_docs: tuple[SessionCoreDocumentSection, ...]
    always_rules: tuple[str, ...]
    preferences: str
    gotchas: str
    triggers: tuple[SessionTrigger, ...]
    artefacts: tuple[SessionArtefactType, ...]
    environment: SessionEnvironment
    memories: tuple[SessionMemory, ...]
    skills: tuple[SessionSkill, ...]
    plugins: tuple[str, ...]
    styles: tuple[str, ...]
    workspace_configuration: SessionWorkspaceConfiguration
    config: SessionConfig | None
    active_profile: str
    workspace: SessionWorkspace | None
    workspace_binding: SessionWorkspaceBinding | None
    workspace_record: SessionWorkspaceRecord | None
    workspace_default_tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SessionStartRequest:
    COMMAND_ID: ClassVar[str] = "session.start"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = SessionStartPayload


def _optional(model, key, factory):
    value = model.get(key)
    return None if value is None else factory(value)


def _payload(model):
    return SessionStartPayload(
        version=model["version"],
        brain_core_version=model["brain_core_version"],
        compiled_at=model["compiled_at"],
        core_bootstrap=model["core_bootstrap"],
        core_docs=tuple(
            SessionCoreDocumentSection(
                section["section"],
                tuple(
                    SessionCoreDocument(
                        doc["title"],
                        doc["path"],
                        SessionLoadInstruction(**doc["load_with"]),
                    )
                    for doc in section["docs"]
                ),
            )
            for section in model["core_docs"]
        ),
        always_rules=tuple(model["always_rules"]),
        preferences=model["preferences"],
        gotchas=model["gotchas"],
        triggers=tuple(
            SessionTrigger(
                item.get("category", "ongoing"),
                item["condition"],
                item.get("detail"),
                item.get("target"),
            )
            for item in model["triggers"]
        ),
        artefacts=tuple(
            SessionArtefactType(
                item["type"],
                item["key"],
                item["path"],
                item.get("naming_pattern"),
                tuple(item.get("status_enum") or ()),
                bool(item["configured"]),
            )
            for item in model["artefacts"]
        ),
        environment=SessionEnvironment(**model["environment"]),
        memories=tuple(
            SessionMemory(item["name"], tuple(item.get("triggers") or ()))
            for item in model["memories"]
        ),
        skills=tuple(
            SessionSkill(item["name"], item["source"])
            for item in model["skills"]
        ),
        plugins=tuple(item["name"] for item in model["plugins"]),
        styles=tuple(model["styles"]),
        workspace_configuration=SessionWorkspaceConfiguration(
            **{
                key: value
                for key, value in model["workspace_configuration"].items()
                if key != "current_binding"
            }
        ),
        config=_optional(
            model,
            "config",
            lambda item: SessionConfig(
                item["brain_name"],
                item["default_profile"],
                tuple(item["profiles"]),
            ),
        ),
        active_profile=model["active_profile"],
        workspace=_optional(model, "workspace", lambda item: SessionWorkspace(**item)),
        workspace_binding=_optional(
            model,
            "workspace_binding",
            lambda item: SessionWorkspaceBinding(**item),
        ),
        workspace_record=_optional(
            model,
            "workspace_record",
            lambda item: SessionWorkspaceRecord(
                item["slug"],
                item["workspace_mode"],
                item.get("hub_path"),
                tuple(item.get("tags") or ()),
            ),
        ),
        workspace_default_tags=tuple(
            model.get("workspace_defaults", {}).get("tags", ())
        ),
    )


def execute(context: InvocationContext, _request: SessionStartRequest):
    import config
    import session
    from _common import load_compiled_router

    router = load_compiled_router(context.selected_brain.vault_root)
    if "error" in router:
        return command_error(
            SessionStartRequest,
            ErrorCode.CONFLICT,
            router["error"],
            None,
        )
    try:
        merged_config = config.load_config(context.selected_brain.vault_root)
        model = session.build_session_model(
            router,
            context.selected_brain.vault_root,
            obsidian_cli_available=(
                context.capabilities.availability_of("obsidian_cli")
                is Availability.AVAILABLE
            ),
            workspace_dir=(
                str(context.workspace_dir) if context.workspace_dir is not None else None
            ),
            config=merged_config,
            active_profile=context.profile,
            load_config_if_missing=False,
        )
        session.persist_session_markdown(model, context.selected_brain.vault_root)
        payload = _payload(model)
    except (OSError, RuntimeError, ValueError) as exc:
        return command_error(SessionStartRequest, ErrorCode.CONFLICT, str(exc), None)
    return Ok(
        SessionStartRequest.COMMAND_ID,
        SessionStartRequest.COMMAND_VERSION,
        payload,
    )


def decode(payload: Mapping[str, object]) -> SessionStartRequest:
    if payload:
        raise ValueError(f"unexpected fields: {', '.join(sorted(payload))}")
    return SessionStartRequest()


def catalogue_entry():
    from ..catalogue import ApplicationEntry

    return ApplicationEntry(
        request_type=SessionStartRequest,
        executor=execute,
        dependency_tier=DependencyTier.MANAGED,
        locality=Locality.SELECTED_BRAIN_LOCAL,
        required_providers=(),
        optional_providers=("obsidian_cli",),
        authority=Authority.READER,
        effect_class=EffectClass.DERIVED_CACHE_WRITE,
        retry_class=RetryClass.SAFE,
        projections=tuple(
            ProjectionEligibility(projection, True)
            for projection in (
                Projection.MCP,
                Projection.CLI,
                Projection.SCRIPT,
                Projection.PYTHON,
            )
        ),
    )


def resolver_entry():
    from ..resolver import ResolverEntry

    return ResolverEntry(SessionStartRequest, decode)
