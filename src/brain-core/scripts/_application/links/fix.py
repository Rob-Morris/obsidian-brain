"""Typed ``links.fix`` owner with explicit preview/apply intent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._mutation_support import no_effect_error, operator_mutation_entry
from ..context import InvocationContext
from ..receipts import CommittedEffect
from ..results import ErrorCode, Ok


@dataclass(frozen=True, slots=True)
class ResolvableLink:
    target: str
    resolved_to: str
    strategy: str
    reference_count: int
    file_count: int


@dataclass(frozen=True, slots=True)
class AmbiguousLink:
    target: str
    candidates: tuple[str, ...]
    strategy: str


@dataclass(frozen=True, slots=True)
class UnresolvableLink:
    target: str
    reference_count: int
    files: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LinkFixSummary:
    total_broken: int
    resolvable: int
    ambiguous: int
    unresolvable: int


@dataclass(frozen=True, slots=True)
class LinksFixPayload:
    mode: str
    path: str | None
    resolvable: tuple[ResolvableLink, ...]
    ambiguous: tuple[AmbiguousLink, ...]
    unresolvable: tuple[UnresolvableLink, ...]
    summary: LinkFixSummary
    substitutions: int


@dataclass(frozen=True, slots=True)
class LinksFixRequest:
    COMMAND_ID: ClassVar[str] = "links.fix"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = LinksFixPayload

    apply: bool = False
    path: str | None = None
    links: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.apply, bool):
            raise ValueError("links.fix apply must be a boolean")
        if self.path is not None and (
            not isinstance(self.path, str) or not self.path.strip()
        ):
            raise ValueError("links.fix path must be a non-empty string")
        if not isinstance(self.links, tuple) or any(
            not isinstance(item, str) or not item.strip() for item in self.links
        ):
            raise ValueError("links.fix links must contain non-empty strings")
        if len(self.links) != len(set(self.links)):
            raise ValueError("links.fix links must be unique")
        if self.links and (self.path is None or not self.apply):
            raise ValueError("links filtering requires apply=true and a path")


def execute(context: InvocationContext, request: LinksFixRequest):
    from _common import (
        MutationLockError,
        public_mutation_error_message,
        vault_mutation_lock,
    )
    from _lifecycle.derived_cache_state import load_fresh_compiled_router
    import fix_links

    root = str(context.selected_brain.vault_root)
    router = load_fresh_compiled_router(root)
    if "error" in router:
        return no_effect_error(LinksFixRequest, ErrorCode.CONFLICT, router["error"])
    apply = request.apply and not context.dry_run
    try:
        if apply:
            with vault_mutation_lock(root):
                result = _run(fix_links, root, router, request, apply=True)
        else:
            result = _run(fix_links, root, router, request, apply=False)
    except MutationLockError as exc:
        return no_effect_error(
            LinksFixRequest,
            ErrorCode.CONFLICT,
            public_mutation_error_message(exc),
            retryable=True,
        )
    except FileNotFoundError as exc:
        return no_effect_error(LinksFixRequest, ErrorCode.NOT_FOUND, str(exc))
    except ValueError as exc:
        return no_effect_error(LinksFixRequest, ErrorCode.INVALID_REQUEST, str(exc))

    payload = _payload(result, request.path, apply)
    effects = (
        (CommittedEffect(request.COMMAND_ID, request.path or "vault"),)
        if payload.substitutions
        else ()
    )
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        payload,
        committed_effects=effects,
    )


def decode(payload: Mapping[str, object]) -> LinksFixRequest:
    unexpected = sorted(set(payload) - {"apply", "path", "links"})
    if unexpected:
        raise ValueError(f"unexpected fields: {', '.join(unexpected)}")
    apply = payload.get("apply", False)
    path = payload.get("path")
    links = payload.get("links", ())
    if not isinstance(apply, bool):
        raise ValueError("apply must be a boolean")
    if path is not None and not isinstance(path, str):
        raise ValueError("path must be a string")
    if isinstance(links, list):
        links = tuple(links)
    if not isinstance(links, tuple):
        raise ValueError("links must be an array of strings")
    return LinksFixRequest(apply, path, links)


def _run(fix_links, root, router, request, *, apply: bool):
    result = (
        fix_links.scan_file(root, request.path, router=router)
        if request.path
        else fix_links.scan_and_resolve(root, router=router)
    )
    substitutions = 0
    if apply and result["fixed"]:
        if request.path:
            substitutions = fix_links.apply_fixes_to_file(
                root,
                request.path,
                result["fixed"],
                links_filter=request.links or None,
            )
        else:
            substitutions = fix_links.apply_fixes(root, result["fixed"])
    result["substitutions"] = substitutions
    return result


def _payload(result: dict, path: str | None, applied: bool) -> LinksFixPayload:
    summary = result["summary"]
    return LinksFixPayload(
        mode="apply" if applied else "preview",
        path=path,
        resolvable=tuple(
            ResolvableLink(
                item["target"],
                item["resolved_to"],
                item["strategy"],
                item["ref_count"],
                item["file_count"],
            )
            for item in result["fixed"]
        ),
        ambiguous=tuple(
            AmbiguousLink(
                item["target"], tuple(item["candidates"]), item["strategy"]
            )
            for item in result["ambiguous"]
        ),
        unresolvable=tuple(
            UnresolvableLink(
                item["target"], item["ref_count"], tuple(item["files"])
            )
            for item in result["unresolvable"]
        ),
        summary=LinkFixSummary(
            summary["total_broken"],
            summary["fixed"],
            summary["ambiguous"],
            summary["unresolvable"],
        ),
        substitutions=result["substitutions"],
    )


def catalogue_entry():
    return operator_mutation_entry(LinksFixRequest, execute)


def resolver_entry():
    from ..resolver import ResolverEntry

    return ResolverEntry(LinksFixRequest, decode)
