"""Typed managed ``session.start`` bootstrap owner."""

from __future__ import annotations

from ..types import InitialAuthorisationClass

from .._decoding import reject_unexpected
from .._response_budget import (ContentRange, TextCursor, bounded_text_result,
    decode_text_cursor, validate_text_window, encoded_result_size,
    MODEL_TEXT_BUDGET, DEFAULT_TEXT_CHARACTERS)
from dataclasses import dataclass, asdict
import json

from ..access_contracts import AccessSummary, AuthorisationSummary, PermissionsSummary
from typing import ClassVar, Mapping

from ..context import InvocationContext
from ..results import CommandError, Error, ErrorCode, InstructionNextAction, Ok
from ..runtime._snapshot import typed_snapshot
from ..runtime_status import RuntimeProgressDetails, RuntimeState
from ..types import (
    Authority,
    Availability,
    DependencyTier,
    EffectClass,
    Locality,
    RetryClass,
)
from .._read_support import command_error


@dataclass(frozen=True, slots=True)
class SessionCoreDocument:
    title: str
    path: str


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
class SessionResourceDiscovery:
    artefact_types: str
    core_documents: str


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
class SessionCommandCatalogue:
    schema: str
    interface_epoch: int
    static_fingerprint: str
    installed_application_command_count: int
    brain_core_version: str
    list: str
    describe: str


@dataclass(frozen=True, slots=True)
class SessionStartPayload:
    bootstrap_complete: bool
    version: str
    brain_core_version: str
    compiled_at: str
    core_bootstrap: str
    core_docs: tuple[SessionCoreDocumentSection, ...]
    always_rules: tuple[str, ...]
    preferences: str
    gotchas: str
    triggers: tuple[SessionTrigger, ...]
    artefact_type_count: int
    resource_discovery: SessionResourceDiscovery
    environment: SessionEnvironment
    memories: tuple[SessionMemory, ...]
    skills: tuple[SessionSkill, ...]
    plugins: tuple[str, ...]
    styles: tuple[str, ...]
    workspace_configuration: SessionWorkspaceConfiguration
    command_catalogue: SessionCommandCatalogue
    config: SessionConfig | None
    access: AccessSummary
    workspace: SessionWorkspace | None
    workspace_binding: SessionWorkspaceBinding | None
    workspace_record: SessionWorkspaceRecord | None
    workspace_default_tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SessionBootstrapPage:
    brain_core_version: str
    bootstrap_complete: bool
    content: str
    revision: str
    range: ContentRange
    instruction: str


@dataclass(frozen=True, slots=True)
class SessionStartRequest:
    COMMAND_ID: ClassVar[str] = "session.start"
    COMMAND_VERSION: ClassVar[int] = 6
    RESULT_TYPE: ClassVar = SessionStartPayload | SessionBootstrapPage

    cursor: TextCursor | None = None

    def __post_init__(self):
        validate_text_window(self.cursor, DEFAULT_TEXT_CHARACTERS)


def _optional(model, key, factory):
    value = model.get(key)
    return None if value is None else factory(value)


def _payload(model):
    return SessionStartPayload(
        bootstrap_complete=True,
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
        artefact_type_count=model["artefact_type_count"],
        resource_discovery=SessionResourceDiscovery(**model["resource_discovery"]),
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
        command_catalogue=SessionCommandCatalogue(**model["command_catalogue"]),
        config=_optional(
            model,
            "config",
            lambda item: SessionConfig(
                item["brain_name"],
                item["default_profile"],
                tuple(item["profiles"]),
            ),
        ),
        access=AccessSummary(PermissionsSummary(**model["access"]["permissions"]),
            AuthorisationSummary(**model["access"]["authorisation"])),
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


def execute(context: InvocationContext, request: SessionStartRequest):
    from _bootstrap.readiness import ensure_runtime_warmup, read_runtime_status

    status = typed_snapshot(read_runtime_status(context.selected_brain.vault_root))
    if status.state is not RuntimeState.READY:
        outcome, value = ensure_runtime_warmup(
            context.selected_brain.vault_root,
            retry_failed=False,
        )
        status = typed_snapshot(value)
        if status.state is RuntimeState.FAILED or outcome == "failed":
            message = "Brain runtime warm-up failed; request an explicit retry."
            guidance = (
                "Call runtime.warmup to retry warm-up, then poll runtime.status "
                "and retry session.start when state is ready."
            )
            retryable = False
        else:
            message = "Brain runtime warm-up is still in progress."
            guidance = (
                "Poll runtime.status, then retry session.start when state is ready."
            )
            retryable = True
        return Error(
            SessionStartRequest.COMMAND_ID,
            SessionStartRequest.COMMAND_VERSION,
            CommandError(
                ErrorCode.CONFLICT,
                message,
                RuntimeProgressDetails(status),
                next_action=InstructionNextAction(guidance),
            ),
            retryable=retryable,
        )

    import config
    import session
    if context.derived_snapshots is not None:
        router = context.derived_snapshots.load_router()
    else:
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
            access_summary=asdict(context.access.summary()),
            load_config_if_missing=False,
            include_command_catalogue=True,
        )
        if context.session_mirror is None:
            raise RuntimeError("session mirror publisher is not available")
        context.session_mirror.publish(model)
        payload = _payload(model)
        if len(json.dumps(asdict(payload.access), ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > 768:
            raise ValueError("Authorisation bootstrap exceeds its 768-byte budget")
    except (OSError, RuntimeError, ValueError) as exc:
        return command_error(SessionStartRequest, ErrorCode.CONFLICT, str(exc), None)
    complete = Ok(request.COMMAND_ID, request.COMMAND_VERSION, payload)
    if request.cursor is None and encoded_result_size(complete) < MODEL_TEXT_BUDGET:
        return complete
    # The same canonical markdown is published as the fallback mirror. Use it
    # for overflow instead of dropping required instructions from the model.
    from _common import document_revision

    content = session.render_session_markdown(model)
    revision = document_revision(content)
    return bounded_text_result(
        SessionStartRequest, content, revision, cursor=request.cursor,
        max_characters=DEFAULT_TEXT_CHARACTERS, byte_budget=MODEL_TEXT_BUDGET - 1,
        payload=lambda text, window: SessionBootstrapPage(
            model["brain_core_version"], window.next_cursor is None,
            text, revision, window,
            ("Read every bootstrap page before ordinary work. Continue with "
             "session.start(cursor=range.next_cursor) until bootstrap_complete is true; "
             "a source conflict requires restarting session.start without a cursor."),
        ),
    )


def decode(payload: Mapping[str, object]) -> SessionStartRequest:
    reject_unexpected(payload, {"cursor"})
    return SessionStartRequest(decode_text_cursor(payload.get("cursor")))


def catalogue_entry():
    from ..catalogue import ALL_APPLICATION_PROJECTIONS, ApplicationEntry

    return ApplicationEntry(
        initial_class=InitialAuthorisationClass.CONTROL,
        request_type=SessionStartRequest,
        executor=execute,
        dependency_tier=DependencyTier.MANAGED,
        locality=Locality.SELECTED_BRAIN_LOCAL,
        required_providers=(),
        optional_providers=("obsidian_cli",),
        authority=Authority.READER,
        effect_class=EffectClass.DERIVED_CACHE_WRITE,
        retry_class=RetryClass.SAFE,
        projections=ALL_APPLICATION_PROJECTIONS,
    )
