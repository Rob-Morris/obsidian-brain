"""Shared retrieval-state error types used across lifecycle boundaries."""

from __future__ import annotations


def _format_retrieval_message(prefix: str, rel_path: str, operation: str, cause: BaseException) -> str:
    """Return the shared retrieval error message shape with optional cause detail."""
    detail = str(cause).strip()
    suffix = f": {detail}" if detail else ""
    return f"{prefix} '{rel_path}' while {operation}{suffix}"


def _format_operation_message(message: str, operation: str | None) -> str:
    """Return the shared message shape for operation-scoped retrieval failures."""
    suffix = f" while {operation}" if operation else ""
    return f"{message}{suffix}"


class UnreadableRetrievalSourceError(OSError):
    """Raised when Brain cannot read a source file needed for retrieval work."""

    def __init__(self, rel_path: str, operation: str, cause: BaseException):
        self.rel_path = rel_path
        self.operation = operation
        super().__init__(
            _format_retrieval_message(
                "unreadable retrieval source",
                rel_path,
                operation,
                cause,
            )
        )


class CompiledRouterUnavailableError(RuntimeError):
    """Raised when retrieval outputs need a compiled router that is unavailable.

    Unlike the file-level retrieval errors, this is a system-level readiness
    failure, so it carries an operation label but no per-file path.
    """

    def __init__(self, message: str, *, operation: str | None = None):
        self.operation = operation
        super().__init__(_format_operation_message(message, operation))


class RetrievalPersistenceError(RuntimeError):
    """Raised when Brain cannot persist derived retrieval state to disk."""

    def __init__(self, rel_path: str, operation: str, cause: BaseException):
        self.rel_path = rel_path
        self.operation = operation
        super().__init__(
            _format_retrieval_message(
                "failed to persist retrieval output",
                rel_path,
                operation,
                cause,
            )
        )


class SemanticRuntimeUnavailableError(RuntimeError):
    """Raised when semantic sidecars cannot be rebuilt because runtime deps are unavailable.

    This lives above `_semantic/` so refresh orchestration, CLI, and MCP
    handlers can depend on one shared typed boundary without reintroducing
    `_search`/`_semantic` lifecycle coupling.
    """

    def __init__(self, message: str, *, operation: str | None = None):
        self.operation = operation
        super().__init__(_format_operation_message(message, operation))
