"""Shared retrieval-source error types."""

from __future__ import annotations


class UnreadableRetrievalSourceError(OSError):
    """Raised when Brain cannot read a source file needed for retrieval work.

    This subclasses ``OSError`` so existing I/O-boundary handlers can treat
    unreadable retrieval sources as filesystem failures even when the immediate
    cause was a decoding error.
    """

    def __init__(self, rel_path: str, operation: str, cause: BaseException):
        self.rel_path = rel_path
        self.operation = operation
        detail = str(cause).strip()
        suffix = f": {detail}" if detail else ""
        super().__init__(
            f"unreadable retrieval source '{rel_path}' while {operation}{suffix}"
        )


class CompiledRouterUnavailableError(RuntimeError):
    """Raised when retrieval outputs need a compiled router that is unavailable.

    Unlike the file-level retrieval errors, this is a system-level readiness
    failure, so it carries an operation label but no per-file path.
    """

    def __init__(self, message: str, *, operation: str | None = None):
        self.operation = operation
        suffix = f" while {operation}" if operation else ""
        super().__init__(f"{message}{suffix}")


class RetrievalPersistenceError(RuntimeError):
    """Raised when Brain cannot persist derived retrieval state to disk."""

    def __init__(self, rel_path: str, operation: str, cause: BaseException):
        self.rel_path = rel_path
        self.operation = operation
        detail = str(cause).strip()
        suffix = f": {detail}" if detail else ""
        super().__init__(
            f"failed to persist retrieval output '{rel_path}' while {operation}{suffix}"
        )
