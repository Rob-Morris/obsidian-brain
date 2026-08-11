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
class LinkProposal:
    target: str
    resolved_to: str
    strategy: str
    reference_count: int
    file_count: int


@dataclass(frozen=True, slots=True)
class LinksCheckPayload:
    findings: tuple[LinkFinding, ...]
    proposals: tuple[LinkProposal, ...]
    ambiguous_targets: tuple[str, ...]
    unresolvable_targets: tuple[str, ...]
    warnings: int
    info: int


@dataclass(frozen=True, slots=True)
class LinksCheckRequest:
    COMMAND_ID: ClassVar[str] = "links.check"
    COMMAND_VERSION: ClassVar[int] = 2
    RESULT_TYPE: ClassVar[type] = LinksCheckPayload

    path: str | None = None

    def __post_init__(self) -> None:
        if self.path is not None and (
            not isinstance(self.path, str) or not self.path.strip()
        ):
            raise ValueError("links.check path must be a non-empty string")


def execute(context: InvocationContext, request: LinksCheckRequest):
    from _portable.links import check_from_vault
    import fix_links

    root = context.selected_brain.vault_root
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
        for finding in check_from_vault(root)
        if request.path is None or finding["file"] == request.path
    )
    plan = (
        fix_links.scan_file(str(root), request.path)
        if request.path is not None
        else fix_links.scan_and_resolve(str(root), router={})
    )
    return Ok(
        LinksCheckRequest.COMMAND_ID,
        LinksCheckRequest.COMMAND_VERSION,
        LinksCheckPayload(
            findings=findings,
            proposals=tuple(
                LinkProposal(
                    item["target"],
                    item["resolved_to"],
                    item["strategy"],
                    item["ref_count"],
                    item["file_count"],
                )
                for item in plan["fixed"]
            ),
            ambiguous_targets=tuple(item["target"] for item in plan["ambiguous"]),
            unresolvable_targets=tuple(
                item["target"] for item in plan["unresolvable"]
            ),
            warnings=sum(finding.severity == "warning" for finding in findings),
            info=sum(finding.severity == "info" for finding in findings),
        ),
    )


def decode(payload: Mapping[str, object]) -> LinksCheckRequest:
    unexpected = sorted(set(payload) - {"path"})
    if unexpected:
        raise ValueError(f"unexpected fields: {', '.join(unexpected)}")
    path = payload.get("path")
    if path is not None and not isinstance(path, str):
        raise ValueError("path must be a string")
    return LinksCheckRequest(path)


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
