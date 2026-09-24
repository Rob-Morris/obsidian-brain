"""Promotion values, request grammar and remote outcome classification."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Mapping
from _version_contract import SEMVER_PATTERN, SEMVER_RE, release_summary_problems

SOURCE_TRAILER = "Brain-Dev-Source"
TIP_TRAILER = "Brain-Dev-Tip"
ZERO_SHA = "0" * 40
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
PROMOTION_BRANCH_RE = re.compile(rf"^promotion/v{SEMVER_PATTERN}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

class PromotionError(RuntimeError):
    """A promotion precondition failed."""


@dataclass(frozen=True)
class PromotionRequest:
    """One explicit version, narrative, and dev cut."""

    core_version: str
    summary: str
    release_type: str
    changes: tuple[str, ...]
    release_date: str
    body: str
    cli_version: str | None = None
    proxy_version: str | None = None
    cut: str | None = None


@dataclass(frozen=True)
class GitCommit:
    """The commit fields promotion topology needs."""

    sha: str
    tree: str
    parents: tuple[str, ...]
    message: str

    @property
    def subject(self) -> str:
        return self.message.splitlines()[0] if self.message else ""


@dataclass(frozen=True)
class PushUpdate:
    """One pre-push ref update. A missing endpoint is the all-zero SHA."""

    remote_ref: str
    remote_sha: str
    local_sha: str


@dataclass(frozen=True)
class RemoteHeads:
    """Origin's main, dev, and unreleased. ``observed`` is false only when the query failed."""

    main: str | None
    dev: str | None
    observed: bool
    unreleased: str | None = None


def parse_trailers(message: str) -> dict[str, str]:
    """Read the uninterrupted trailer block at the end of a commit message."""
    trailers: dict[str, str] = {}
    for line in reversed(message.strip().splitlines()):
        if not line.strip():
            break
        key, separator, value = line.partition(": ")
        if not separator or not key or " " in key:
            break
        trailers[key] = value
    return trailers


def version_tuple(version: str) -> tuple[int, int, int]:
    if not SEMVER_RE.fullmatch(version):
        raise PromotionError(f"invalid semantic version: {version!r}")
    return tuple(int(part) for part in version.split("."))  # type: ignore[return-value]


def load_request(payload: Mapping[str, object]) -> PromotionRequest:
    """Validate a promotion request. The caller chooses the version and the cut."""
    required = ("core_version", "summary", "release_type", "changes", "date", "body")
    missing = [key for key in required if key not in payload]
    if missing:
        raise PromotionError(f"promotion request is missing {', '.join(missing)}")
    core = payload["core_version"]
    summary = payload["summary"]
    release_type = payload["release_type"]
    changes = payload["changes"]
    release_date = payload["date"]
    body = payload["body"]
    if not isinstance(core, str) or not SEMVER_RE.fullmatch(core):
        raise PromotionError("core_version must be a semantic version")
    if not isinstance(summary, str) or not summary.strip():
        raise PromotionError("summary must be a non-empty string")
    summary = summary.strip()
    if release_summary_problems(summary):
        raise PromotionError("summary must omit a trailing period and any version suffix")
    if not isinstance(release_type, str) or not release_type.strip():
        raise PromotionError("release_type must be a non-empty string")
    if (
        not isinstance(changes, list)
        or not changes
        or any(not isinstance(item, str) or not item.strip() for item in changes)
    ):
        raise PromotionError("changes must be a non-empty list of strings")
    if not isinstance(release_date, str) or not DATE_RE.fullmatch(release_date):
        raise PromotionError("date must be YYYY-MM-DD")
    try:
        datetime.strptime(release_date, "%Y-%m-%d")
    except ValueError as exc:
        raise PromotionError("date must be a real YYYY-MM-DD") from exc
    if not isinstance(body, str) or not body.strip():
        raise PromotionError("body must be a non-empty string")
    if SOURCE_TRAILER in body or TIP_TRAILER in body:
        raise PromotionError("body must not contain promotion trailers")
    cut = payload.get("cut")
    if cut is not None and (not isinstance(cut, str) or not SHA_RE.fullmatch(cut)):
        raise PromotionError("cut must be a full SHA or omitted")
    cli_version = payload.get("cli_version")
    proxy_version = payload.get("proxy_version")
    for name, value in (("cli_version", cli_version), ("proxy_version", proxy_version)):
        if value is not None and (not isinstance(value, str) or not SEMVER_RE.fullmatch(value)):
            raise PromotionError(f"{name} must be a semantic version or null")
    return PromotionRequest(
        core,
        summary,
        release_type.strip(),
        tuple(changes),
        release_date,
        body if body.endswith("\n") else body + "\n",
        cli_version if isinstance(cli_version, str) else None,
        proxy_version if isinstance(proxy_version, str) else None,
        cut,
    )


def candidate_message(request: PromotionRequest, cut: str, tip: str) -> str:
    return (
        f"{request.summary} (v{request.core_version})\n\n"
        f"{request.body.rstrip()}\n\n"
        f"{SOURCE_TRAILER}: {cut}\n"
        f"{TIP_TRAILER}: {tip}\n"
    )


def ref_label(sha: str | None) -> str:
    return "absent" if sha is None else sha


def classify_remote(
    old_ledger: str,
    old_dev: str,
    new_ledger: str,
    new_dev: str,
    seen_ledger: str | None,
    seen_dev: str | None,
    *,
    observed: bool,
) -> str:
    """Classify an atomic push as unchanged, settled, diverged, or unobserved.

    ``observed`` is false only when listing origin failed. An absent ref was
    seen, and is diverged rather than unobserved.
    """
    if not observed:
        return "unobserved"
    if seen_ledger is None or seen_dev is None:
        return "diverged"
    if (seen_ledger, seen_dev) == (old_ledger, old_dev):
        return "unchanged"
    if (seen_ledger, seen_dev) == (new_ledger, new_dev):
        return "settled"
    return "diverged"


def promotion_branch(version: str) -> str:
    branch = f"promotion/v{version}"
    if not PROMOTION_BRANCH_RE.fullmatch(branch):
        raise PromotionError(f"invalid promotion branch {branch}")
    return branch
