"""Typed ``links.check`` command and internal executor."""

from __future__ import annotations

from .._decoding import reject_unexpected
from .._read_support import catalogue_entry as portable_reader_entry

from dataclasses import dataclass
from typing import ClassVar, Mapping

from ..context import InvocationContext
from ..results import ErrorCode, Ok, request_error


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
    from pathlib import Path
    from _common import (
        MissingFileResult, build_vault_file_index, check_wikilinks_in_file,
    )
    from _common._document_revision import NonStandardVaultTextError
    from _portable.links import check_from_vault, describe_link_findings
    from _portable.vault_files import read_vault_file
    import fix_links

    root = context.selected_brain.vault_root
    if request.path is not None:
        try:
            text = read_vault_file(root, request.path)
        except NonStandardVaultTextError:
            return Ok(LinksCheckRequest.COMMAND_ID, LinksCheckRequest.COMMAND_VERSION,
                      LinksCheckPayload((), (), (), (), 0, 0))
        if isinstance(text, MissingFileResult):
            return request_error(LinksCheckRequest, ErrorCode.NOT_FOUND,
                                 f"Link-check source is not a file: {request.path}")
        if isinstance(text, dict):
            return request_error(LinksCheckRequest, ErrorCode.INVALID_REQUEST, text["error"])
        if "[[" not in text:
            return Ok(LinksCheckRequest.COMMAND_ID, LinksCheckRequest.COMMAND_VERSION,
                      LinksCheckPayload((), (), (), (), 0, 0))
        rel_path = Path(text.source_path).relative_to(root.resolve()).as_posix()
        file_index = build_vault_file_index(root)
        raw = check_wikilinks_in_file(root, rel_path, file_index=file_index, text=text)
        source_findings = describe_link_findings(rel_path, text, raw)
        plan = fix_links.plan_file_findings(rel_path, raw)
    else:
        file_index = build_vault_file_index(root)
        source_findings = check_from_vault(root, file_index=file_index)
        plan = fix_links.scan_and_resolve(
            str(root), router={}, for_mutation=False, file_index=file_index,
        )
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
        for finding in source_findings
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
    reject_unexpected(payload, {"path"})
    path = payload.get("path")
    if path is not None and not isinstance(path, str):
        raise ValueError("path must be a string")
    return LinksCheckRequest(path)


def catalogue_entry():
    return portable_reader_entry(LinksCheckRequest, execute)
