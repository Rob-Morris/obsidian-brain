"""Supported structural command result contracts for typed Python consumers."""

from _application.results import (
    CommandError,
    CommandNextAction,
    CommandResult,
    CommandWarning,
    Error,
    ErrorCode,
    Ok,
    Partial,
    WarningCode,
)
from _application.workspace_context import WorkspaceMutationPartial

__all__ = (
    "CommandError",
    "CommandNextAction",
    "CommandResult",
    "CommandWarning",
    "Error",
    "ErrorCode",
    "Ok",
    "Partial",
    "WarningCode",
    "WorkspaceMutationPartial",
)
