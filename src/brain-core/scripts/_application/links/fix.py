"""Typed applying ``links.fix`` owner."""

from __future__ import annotations

from .._decoding import reject_unexpected

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._mutation_support import contributor_mutation_entry, no_effect_error
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

    path: str | None = None
    links: tuple[str, ...] = ()

    def __post_init__(self) -> None:
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
        if self.links and self.path is None:
            raise ValueError("links filtering requires a path")


def execute(context: InvocationContext, request: LinksFixRequest):
    from _common import (
        MutationLockError,
        public_mutation_error_message,
        vault_mutation_lock,
    )
    from _lifecycle.derived_cache_state import require_fresh_compiled_router
    import fix_links

    root = str(context.selected_brain.vault_root)
    apply = not context.dry_run
    try:
        with vault_mutation_lock(root):
            from ..preparation import admit_owner

            router = require_fresh_compiled_router(root)
            plan = fix_links.plan_link_fixes(root, path=request.path,
                                             links_filter=request.links, router=router)
            admit_owner(context, request, fix_binding, plan=plan)
            result = fix_links.apply_link_fix_plan(root, plan, dry_run=not apply)
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
    reject_unexpected(payload, {"path", "links"})
    path = payload.get("path")
    links = payload.get("links", ())
    if path is not None and not isinstance(path, str):
        raise ValueError("path must be a string")
    if isinstance(links, list):
        links = tuple(links)
    if not isinstance(links, tuple):
        raise ValueError("links must be an array of strings")
    return LinksFixRequest(path, links)


def fix_binding(context, request, *, plan, frozen_inputs=None):
    from ..preparation import ObservedResource, bind_operation, canonical_json, content_digest

    if plan.rewrites.unreadable:
        raise ValueError("Cannot prepare complete link fixes; some candidates are unreadable")
    observed = [ObservedResource("fix-result", request.path or "vault",
                                content_digest(canonical_json(plan.result)))]
    for item in plan.rewrites.writes:
        observed.extend((ObservedResource("source", item.path, content_digest(item.before)),
                         ObservedResource("replacement", item.path, content_digest(item.after))))
    if plan.path and not any(item.path == plan.path for item in plan.rewrites.writes):
        observed.append(ObservedResource("source", plan.path,
                        content_digest((context.selected_brain.vault_root / plan.path).read_bytes())))
    return bind_operation(request, observations=observed, frozen_inputs=frozen_inputs,
                          review={"writes": [item.path for item in plan.rewrites.writes],
                                  "summary": plan.result["summary"]})


def prepare(context, request, *, frozen_inputs=None):
    from _common import vault_mutation_lock
    from _lifecycle.derived_cache_state import require_fresh_compiled_router
    import fix_links

    root = str(context.selected_brain.vault_root)
    with vault_mutation_lock(root):
        router = require_fresh_compiled_router(root)
        plan = fix_links.plan_link_fixes(root, path=request.path, links_filter=request.links, router=router)
        return fix_binding(context, request, plan=plan, frozen_inputs=frozen_inputs)


def _payload(result: dict, path: str | None, applied: bool) -> LinksFixPayload:
    summary = result["summary"]
    return LinksFixPayload(
        mode="apply" if applied else "planned",
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
    from dataclasses import replace
    from ..preparation import OperationPreparation

    return replace(contributor_mutation_entry(LinksFixRequest, execute),
                   preparation=OperationPreparation(prepare))
