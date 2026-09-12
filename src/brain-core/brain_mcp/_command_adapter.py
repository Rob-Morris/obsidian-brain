"""Catalogue-derived granular MCPServer adapter registration."""

from __future__ import annotations

from dataclasses import MISSING, fields
import inspect
import time
from typing import Annotated, Callable, get_type_hints

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.utilities.func_metadata import FuncMetadata
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field

from _application.adapter import (
    AdapterRequestError,
    ApplicationAdapter,
    project_adapter_result,
)
from _application.catalogue import ApplicationCatalogue, ApplicationEntry
from _application.context import InvocationContext
from _application.projection import (
    canonical_wire_value,
    project_identity,
    request_schema,
)
from _application.resolver import RequestResolver
from _application.results import CommandError, Error, ErrorCode
from _application.types import EffectClass, Projection, RetryClass
from _common import _operational_log

from ._interface_protocol import (
    CommandInterfaceHeader,
    InterfaceTool,
    command_interface_header,
)


ContextFactory = Callable[..., InvocationContext]
InvocationGuard = Callable[[], None]


def application_interface_header(
    catalogue: ApplicationCatalogue,
    *,
    allowed_tools: frozenset[str] | None = None,
) -> CommandInterfaceHeader:
    """Project the authoritative MCP mapping into the proxy handshake contract."""

    tools = {
        project_identity(entry.command_id).mcp_tool: InterfaceTool(
            command_id=entry.command_id,
            command_version=entry.command_version,
            mutation_class=entry.effect_class.value,
        )
        for entry in catalogue.entries
        if Projection.MCP in entry.eligible_projections
        and (allowed_tools is None or entry.command_id in allowed_tools)
    }
    return command_interface_header(
        interface_epoch=catalogue.interface_epoch,
        catalogue_schema=catalogue.schema,
        result_schema=catalogue.result_schema,
        catalogue_fingerprint=catalogue.fingerprint,
        tools=tools,
    )


class _CanonicalInvocationMetadata(FuncMetadata):
    """Route raw MCP arguments through the canonical application resolver."""

    async def call_fn_with_arg_validation(
        self,
        fn,
        fn_is_async,
        arguments_to_validate,
        arguments_to_pass_directly,
        pre_validated=None,
    ):
        arguments = dict(arguments_to_validate)
        arguments.update(arguments_to_pass_directly or {})
        if fn_is_async:
            return await fn(**arguments)
        return fn(**arguments)


def register_application_tools(
    mcp: MCPServer,
    *,
    catalogue: ApplicationCatalogue,
    resolver: RequestResolver,
    context_factory: ContextFactory,
    invocation_guard: InvocationGuard,
    allowed_tools: frozenset[str] | None = None,
) -> tuple[str, ...]:
    """Register every MCP-eligible application command exactly once."""

    adapter = ApplicationAdapter(catalogue, resolver)
    names = []
    for entry in catalogue.entries:
        if Projection.MCP not in entry.eligible_projections:
            continue
        name = project_identity(entry.command_id).mcp_tool
        if allowed_tools is not None and entry.command_id not in allowed_tools:
            continue
        handler = _handler(
            entry,
            catalogue,
            adapter,
            context_factory,
            invocation_guard,
        )
        mcp.add_tool(
            handler,
            name=name,
            description=entry.summary,
            annotations=_annotations(entry),
            structured_output=False,
        )
        tool = mcp._tool_manager.get_tool(name)
        if tool is None:
            raise RuntimeError(f"MCPServer did not retain registered tool: {name}")
        generated = tool.fn_metadata
        tool.fn_metadata = _CanonicalInvocationMetadata(
            arg_model=generated.arg_model,
            output_schema=generated.output_schema,
            output_model=generated.output_model,
            wrap_output=generated.wrap_output,
        )
        tool.parameters = request_schema(entry.request_type)
        names.append(name)
    if names != sorted(names) or len(names) != len(set(names)):
        raise RuntimeError("granular MCP registration must be sorted and collision-free")
    return tuple(names)


def _annotations(entry: ApplicationEntry) -> ToolAnnotations:
    read_only = entry.effect_class is EffectClass.NONE
    destructive = entry.effect_class in {
        EffectClass.SELECTED_BRAIN_MUTATION,
        EffectClass.CALLER_LOCAL_MUTATION,
        EffectClass.MACHINE_MUTATION,
    }
    return ToolAnnotations(
        readOnlyHint=read_only,
        destructiveHint=destructive,
        idempotentHint=entry.retry_class is RetryClass.SAFE,
        openWorldHint=entry.open_world,
    )


def _handler(
    entry: ApplicationEntry,
    catalogue: ApplicationCatalogue,
    adapter: ApplicationAdapter,
    context_factory: ContextFactory,
    invocation_guard: InvocationGuard,
):
    def invoke(*, mcp_context: Context, **arguments) -> CallToolResult:
        # This pre-effect boundary deliberately sits outside the recoverable
        # adapter error path. The server guard exits with code 10 so the proxy
        # can replace stale command code and replay under the new interface.
        invocation_guard()
        logger = _operational_log.current_logger()
        started = time.monotonic()
        try:
            context = context_factory(
                command_id=entry.command_id,
                catalogue=catalogue,
                mcp_context=mcp_context,
            )
        except Exception as exc:
            # Unpaired tool.handled: the invocation identity never composed.
            if logger is not None:
                logger.record(
                    "tool.handled",
                    command_id=entry.command_id,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    outcome="error",
                    error_class=_operational_log.classify_error(exc),
                )
            projection = _error_projection(
                entry,
                ErrorCode.INTERNAL_ERROR,
                "The MCP invocation context could not be composed.",
            )
        else:
            if logger is not None:
                logger.record(
                    "tool.started",
                    command_id=entry.command_id,
                    invocation_id=context.invocation_id,
                )
            try:
                projection = adapter.invoke(
                    context,
                    entry.command_id,
                    canonical_wire_value(arguments),
                )
                if not projection.is_error and context.derived_snapshots is not None:
                    invalidated = {
                        "runtime.refresh-router": ("router",),
                        "retrieval.refresh-lexical": ("lexical",),
                    }.get(entry.command_id)
                    if invalidated is not None:
                        context.derived_snapshots.invalidate(*invalidated)
            except AdapterRequestError as exc:
                projection = _error_projection(
                    entry,
                    ErrorCode.INVALID_REQUEST,
                    str(exc),
                )
            if logger is not None:
                logger.record(
                    "tool.handled",
                    command_id=entry.command_id,
                    invocation_id=context.invocation_id,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    outcome="error" if projection.is_error else "ok",
                )
        return CallToolResult(
            content=[TextContent(type="text", text=projection.concise_text)],
            structuredContent=projection.structured_content,
            isError=projection.is_error,
        )

    invoke.__name__ = project_identity(entry.command_id).mcp_tool
    invoke.__doc__ = entry.summary
    invoke.__signature__ = _signature(entry)
    return invoke


def _signature(entry: ApplicationEntry) -> inspect.Signature:
    schema = request_schema(entry.request_type)
    properties = schema["properties"]
    hints = get_type_hints(entry.request_type)
    parameters = [
        inspect.Parameter(
            "mcp_context",
            inspect.Parameter.KEYWORD_ONLY,
            annotation=Context,
        )
    ]
    for field in fields(entry.request_type):
        if not field.init:
            continue
        default = inspect.Parameter.empty
        if field.default is not MISSING or field.default_factory is not MISSING:
            # Runtime arguments bypass MCPServer's parallel Pydantic coercion and
            # are decoded by the canonical resolver. This default exists only
            # so MCPServer recognises public optionality while constructing the
            # callable metadata that the canonical schema replaces below.
            default = None
        annotation = Annotated[
            hints.get(field.name, field.type),
            Field(description=properties[field.name]["description"]),
        ]
        parameters.append(
            inspect.Parameter(
                field.name,
                inspect.Parameter.KEYWORD_ONLY,
                default=default,
                annotation=annotation,
            )
        )
    return inspect.Signature(parameters, return_annotation=CallToolResult)


def _error_projection(
    entry: ApplicationEntry,
    code: ErrorCode,
    message: str,
):
    return project_adapter_result(
        Error(
            entry.command_id,
            entry.command_version,
            CommandError(code, message),
        )
    )
