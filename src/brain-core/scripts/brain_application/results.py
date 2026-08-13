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
)
