from __future__ import annotations

from typing import Mapping

from .process import ProcessExecution


class DockerError(RuntimeError):
    def __init__(
        self, message: str, execution: ProcessExecution | None = None, *,
        cleanup_error: str | None = None, survivor: Mapping[str, str] | None = None,
        evidence_complete: bool = True,
    ):
        super().__init__(message)
        self.execution = execution
        self.cleanup_error = cleanup_error
        self.survivor = survivor
        self.evidence_complete = evidence_complete and (execution is None or execution.evidence_complete)
