from __future__ import annotations

import json
import hashlib
import shutil
import tempfile
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from .compatibility import CompatibilityManifest
from .docker import DockerClient, DockerError, MAX_COMMANDS_PER_OPERATION
from .model import (
    EffectCertainty,
    EvidenceCompleteness,
    OperationResult,
    Outcome,
    require_object,
)
from .process import CommandRunner
from .store import StateStore, utc_now


@dataclass(frozen=True)
class HandlerResult:
    resource: Mapping[str, Any] | None = None
    payload: Mapping[str, Any] | None = None
    effect_certainty: EffectCertainty = EffectCertainty.NONE
    evidence_completeness: EvidenceCompleteness = EvidenceCompleteness.COMPLETE


@dataclass
class OperationContext:
    application: "Application"
    operation_id: str
    operation: str
    evidence_directory: Path
    execution_start: int

    @property
    def store(self) -> StateStore:
        return self.application.store

    @property
    def docker(self) -> DockerClient:
        return self.application.docker

    @property
    def runner(self) -> CommandRunner:
        return self.application.runner

    @property
    def compatibility(self) -> CompatibilityManifest:
        return self.application.compatibility

    @property
    def tool_root(self) -> Path:
        return self.application.tool_root


Handler = Callable[[OperationContext, dict[str, Any]], HandlerResult]
EvidenceReferences = Callable[[Mapping[str, Any]], list[str]]


@dataclass(frozen=True)
class OperationSpec:
    handler: Handler
    mutating: bool = False
    evidence_references: EvidenceReferences | None = None


class OperationFailure(RuntimeError):
    """A domain failure whose resource and partial result remain inspectable."""

    def __init__(
        self,
        message: str,
        *,
        outcome: Outcome = Outcome.FAILURE,
        effect_certainty: EffectCertainty = EffectCertainty.UNKNOWN,
        evidence_completeness: EvidenceCompleteness = EvidenceCompleteness.COMPLETE,
        resource: Mapping[str, Any] | None = None,
        payload: Mapping[str, Any] | None = None,
        error_type: str | None = None,
    ):
        super().__init__(message)
        self.outcome = outcome
        self.effect_certainty = effect_certainty
        self.evidence_completeness = evidence_completeness
        self.resource = resource
        self.payload = payload or {}
        self.error_type = error_type or type(self).__name__


def _failure_result(
    *,
    operation_id: str,
    operation: str,
    started_at: str,
    error: Exception,
    outcome: Outcome = Outcome.FAILURE,
    effect_certainty: EffectCertainty,
    evidence_completeness: EvidenceCompleteness = EvidenceCompleteness.COMPLETE,
    resource: Mapping[str, Any] | None = None,
    payload: Mapping[str, Any] | None = None,
    error_type: str | None = None,
) -> OperationResult:
    return OperationResult(
        operation_id=operation_id,
        operation=operation,
        outcome=outcome,
        effect_certainty=effect_certainty,
        evidence_completeness=evidence_completeness,
        started_at=started_at,
        finished_at=utc_now(),
        resource=resource,
        payload=payload or {},
        evidence_bundle=operation_id,
        errors=({"type": error_type or type(error).__name__, "message": str(error)},),
    )


class Application:
    def __init__(
        self,
        *,
        store: StateStore,
        runner: CommandRunner,
        docker: DockerClient,
        tool_root: Path,
    ):
        self.store = store
        self.runner = runner
        self.docker = docker
        self.tool_root = tool_root.resolve()
        self.compatibility = CompatibilityManifest(self.tool_root / "compatibility.json")
        self._operations: dict[str, OperationSpec] = {}
        self._dispatch_depth = 0

    def register(
        self,
        operation: str,
        handler: Handler,
        *,
        mutating: bool = False,
        evidence_references: EvidenceReferences | None = None,
    ) -> None:
        if operation in self._operations:
            raise ValueError(f"duplicate operation handler: {operation}")
        self._operations[operation] = OperationSpec(
            handler=handler,
            mutating=mutating,
            evidence_references=evidence_references,
        )

    def dispatch(self, operation: str, request: Any) -> OperationResult:
        self._dispatch_depth += 1
        try:
            return self._dispatch(operation, request)
        finally:
            self._dispatch_depth -= 1
            if self._dispatch_depth == 0 and isinstance(self.docker, DockerClient):
                self.docker.configuration.reset()

    def _dispatch(self, operation: str, request: Any) -> OperationResult:
        spec = self._operations.get(operation)
        if spec is None:
            raise ValueError(f"unknown operation: {operation}")
        handler = spec.handler
        parsed = require_object(request)
        operation_id = f"op-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:12]}"
        evidence_directory = self.store.evidence_directory(operation_id)
        started = utc_now()
        execution_start = self.docker.begin_operation()
        context = OperationContext(self, operation_id, operation, evidence_directory, execution_start)
        try:
            handled = handler(context, parsed)
            result = OperationResult(
                operation_id=operation_id,
                operation=operation,
                outcome=Outcome.SUCCESS,
                effect_certainty=handled.effect_certainty,
                evidence_completeness=handled.evidence_completeness,
                started_at=started,
                finished_at=utc_now(),
                resource=handled.resource,
                payload=handled.payload or {},
                evidence_bundle=operation_id,
            )
        except OperationFailure as exc:
            result = _failure_result(
                operation_id=operation_id,
                operation=operation,
                started_at=started,
                error=exc,
                outcome=exc.outcome,
                effect_certainty=exc.effect_certainty,
                evidence_completeness=exc.evidence_completeness,
                resource=exc.resource,
                payload=exc.payload,
                error_type=exc.error_type,
            )
        except DockerError as exc:
            execution = exc.execution
            timed_out = bool(execution and execution.timed_out)
            cancelled = bool(execution and execution.cancelled)
            complete = exc.evidence_complete
            result = _failure_result(
                operation_id=operation_id,
                operation=operation,
                started_at=started,
                error=exc,
                outcome=Outcome.TIMEOUT if timed_out else Outcome.CANCELLED if cancelled else Outcome.FAILURE,
                effect_certainty=(
                    EffectCertainty.UNKNOWN if spec.mutating else EffectCertainty.NONE
                ),
                evidence_completeness=(
                    EvidenceCompleteness.COMPLETE if complete else EvidenceCompleteness.PARTIAL
                ),
                payload={
                    **({"cleanup_error": exc.cleanup_error} if exc.cleanup_error else {}),
                    **({"survivor": exc.survivor} if exc.survivor else {}),
                },
            )
        except Exception as exc:
            result = _failure_result(
                operation_id=operation_id,
                operation=operation,
                started_at=started,
                error=exc,
                effect_certainty=(
                    EffectCertainty.UNKNOWN if spec.mutating else EffectCertainty.NONE
                ),
            )
        try:
            executions = self.docker.executions[execution_start:]
            # A scenario owns orchestration and host-state evidence only. Primitive
            # dispatches own their Docker transcripts; duplicating them here would
            # violate the referenced-evidence model and the per-bundle bound.
            if spec.evidence_references is not None:
                executions = []
            commands = [execution.to_dict() for execution in executions]
            if any(not execution.evidence_complete for execution in executions):
                result = replace(result, evidence_completeness=EvidenceCompleteness.PARTIAL)
            (evidence_directory / "commands.json").write_text(
                json.dumps(commands, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            files = []
            for path in sorted(evidence_directory.rglob("*")):
                if not path.is_file() or path.name == "bundle.json":
                    continue
                content = path.read_bytes()
                files.append(
                    {
                        "path": str(path.relative_to(evidence_directory)),
                        "size": len(content),
                        "sha256": hashlib.sha256(content).hexdigest(),
                    }
                )
            command_limit = getattr(
                self.docker, "operation_command_limit", MAX_COMMANDS_PER_OPERATION
            )
            bundle = {
                "schema": "brain-lab.evidence-bundle/1",
                "operation_id": operation_id,
                "command_count": len(commands),
                "retention": {
                    "per_stream_bytes": self.runner.stream_limit,
                    "maximum_commands": command_limit,
                    "maximum_command_stream_bytes": (
                        self.runner.stream_limit * 2 * command_limit
                    ),
                },
                "retained_bytes": sum(item["size"] for item in files),
                "files": files,
            }
            if spec.evidence_references is not None:
                bundle["referenced_operation_ids"] = spec.evidence_references(result.payload)
            (evidence_directory / "bundle.json").write_text(
                json.dumps(bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        except (OSError, TypeError, ValueError) as exc:
            result = replace(
                result,
                evidence_completeness=EvidenceCompleteness.PARTIAL,
                errors=(
                    *result.errors,
                    {
                        "type": "EvidenceCollectionError",
                        "message": f"primary result preserved; evidence finalisation failed: {exc}",
                    },
                ),
            )
        try:
            self.store.write("result", operation_id, result.to_dict())
        except OSError as exc:
            result = replace(
                result,
                evidence_completeness=EvidenceCompleteness.PARTIAL,
                errors=(
                    *result.errors,
                    {
                        "type": "ReceiptPersistenceError",
                        "message": f"primary result preserved; result receipt could not be persisted: {exc}",
                    },
                ),
            )
        return result
