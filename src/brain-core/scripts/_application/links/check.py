"""Typed ``links.check`` command and internal executor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from ..context import InvocationContext
from ..results import Ok
from ..types import (
    Authority,
    DependencyTier,
    EffectClass,
    Locality,
    Projection,
    ProjectionEligibility,
    RetryClass,
)


@dataclass(frozen=True, slots=True)
class LinkFinding:
    check: str
    severity: str
    file: str
    message: str
    line: int | None = None
    stem: str | None = None
    fix: str | None = None


@dataclass(frozen=True, slots=True)
class LinksCheckPayload:
    findings: tuple[LinkFinding, ...]
    warnings: int
    info: int


@dataclass(frozen=True, slots=True)
class LinksCheckRequest:
    COMMAND_ID: ClassVar[str] = "links.check"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = LinksCheckPayload


def execute(context: InvocationContext, _request: LinksCheckRequest):
    from _portable.links import check_from_vault

    findings = tuple(
        LinkFinding(
            check=finding["check"],
            severity=finding["severity"],
            file=finding["file"],
            message=finding["message"],
            line=finding.get("line"),
            stem=finding.get("stem"),
            fix=finding.get("fix"),
        )
        for finding in check_from_vault(context.selected_brain.vault_root)
    )
    return Ok(
        LinksCheckRequest.COMMAND_ID,
        LinksCheckRequest.COMMAND_VERSION,
        LinksCheckPayload(
            findings=findings,
            warnings=sum(finding.severity == "warning" for finding in findings),
            info=sum(finding.severity == "info" for finding in findings),
        ),
    )


def decode(payload: Mapping[str, object]) -> LinksCheckRequest:
    if payload:
        raise ValueError(f"unexpected fields: {', '.join(sorted(payload))}")
    return LinksCheckRequest()


def catalogue_entry():
    from ..catalogue import ApplicationEntry

    return ApplicationEntry(
        request_type=LinksCheckRequest,
        executor=execute,
        dependency_tier=DependencyTier.PORTABLE,
        locality=Locality.SELECTED_BRAIN_LOCAL,
        required_providers=(),
        optional_providers=(),
        authority=Authority.READER,
        effect_class=EffectClass.NONE,
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

    return ResolverEntry(LinksCheckRequest, decode)
