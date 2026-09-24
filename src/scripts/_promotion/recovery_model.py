"""Immutable values and manifest grammar for exceptional promotion recovery."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Mapping
from urllib.parse import unquote, urlsplit

from .model import PromotionError, SHA_RE

SCHEMA = "brain.promotion-recovery-plan/1"
RESULT_SCHEMA = "brain.promotion-recovery-result/1"


def github_repository(url: str) -> str | None:
    """Return a transport-independent GitHub owner/repo, without URL credentials."""
    scp = re.fullmatch(r"(?:[^@/:]+@)?github\.com:([^/]+)/([^/]+?)(?:\.git)?", url,
                       flags=re.IGNORECASE)
    if scp:
        return f"{scp.group(1).lower()}/{scp.group(2).lower()}"
    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "http", "ssh", "git"} or parsed.hostname != "github.com":
        return None
    parts = parsed.path.strip("/").split("/")
    if len(parts) != 2 or not all(parts):
        return None
    owner, name = parts
    return f"{owner.lower()}/{name.removesuffix('.git').lower()}"


def canonical_origin(root: Path, url: str) -> str:
    github = github_repository(url)
    if github is not None:
        return f"github:{github}"
    parsed = urlsplit(url)
    if parsed.scheme == "file" and parsed.hostname in (None, "localhost"):
        path = Path(unquote(parsed.path))
    elif parsed.scheme == "" and not re.match(r"^[^/]+@[^/:]+:", url):
        path = Path(url)
    else:
        raise PromotionError("recovery requires a GitHub or local origin repository")
    if not path.is_absolute():
        path = root / path
    return f"local:{path.resolve()}"


def plan_ref(sha: str) -> str:
    _sha(sha, "plan")
    return f"refs/heads/recovery/{sha}/plan"


def result_ref(sha: str) -> str:
    _sha(sha, "plan")
    return f"refs/heads/recovery/{sha}/result"


def local_plan_ref(sha: str) -> str:
    _sha(sha, "plan")
    return f"refs/recovery/{sha}/plan"


def _sha(value: object, name: str) -> str:
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise PromotionError(f"recovery manifest has invalid {name}")
    return value


@dataclass(frozen=True)
class RecoveryEntry:
    old_candidate: str
    old_source: str
    old_tip: str
    old_version: str
    old_ref: str
    old_ref_sha: str | None
    new_candidate: str
    new_source: str
    new_reconciliation: str
    new_version: str
    new_ref: str
    new_ref_old: str | None
    summary: str
    release_type: str
    date: str
    body: str
    old_note: str
    new_note: str
    cli_version: str
    proxy_version: str
    release_paths: tuple[str, ...]
    substitutions: tuple[Mapping[str, object], ...]

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "RecoveryEntry":
        fields = cls.__dataclass_fields__
        if set(value) != set(fields):
            raise PromotionError("recovery entry fields differ from the sealed schema")
        for name in ("old_candidate", "old_source", "old_tip", "old_ref_sha",
                     "new_candidate", "new_source", "new_reconciliation", "new_ref_old"):
            item = value[name]
            if item is not None:
                _sha(item, name)
        for name in fields:
            if name in {"old_ref_sha", "new_ref_old", "release_paths", "substitutions"}:
                continue
            if not isinstance(value[name], str):
                raise PromotionError(f"recovery entry has invalid {name}")
        if not isinstance(value["substitutions"], list) or not isinstance(value["release_paths"], list):
            raise PromotionError("recovery entry has invalid substitutions")
        if any(not isinstance(path, str) for path in value["release_paths"]):
            raise PromotionError("recovery entry has invalid release paths")
        return cls(**{**value, "release_paths": tuple(value["release_paths"]),
                      "substitutions": tuple(value["substitutions"])})  # type: ignore[arg-type]


@dataclass(frozen=True)
class RecoveryPlan:
    sha: str
    manifest: Mapping[str, object]
    entries: tuple[RecoveryEntry, ...]

    @property
    def snapshot(self) -> Mapping[str, object]:
        return self.manifest["snapshot"]  # type: ignore[return-value]

    @property
    def target_unreleased(self) -> str:
        return self.manifest["target_unreleased"]  # type: ignore[return-value]

    @property
    def target_dev(self) -> str:
        return self.manifest["target_dev"]  # type: ignore[return-value]

    @property
    def transactions(self) -> Mapping[str, object]:
        return self.manifest["transactions"]  # type: ignore[return-value]


def decode_plan(sha: str, manifest: Mapping[str, object]) -> RecoveryPlan:
    _sha(sha, "plan")
    expected = {"schema", "repository", "snapshot", "entries", "target_unreleased",
                "target_dev", "version_map", "construction", "transactions"}
    if set(manifest) != expected or manifest["schema"] != SCHEMA:
        raise PromotionError("recovery plan manifest has an unsupported schema")
    snapshot = manifest["snapshot"]
    if not isinstance(snapshot, dict) or set(snapshot) != {
        "main", "anchor", "unreleased", "unreleased_exists", "dev"
    }:
        raise PromotionError("recovery plan snapshot is malformed")
    for name in ("main", "anchor", "dev"):
        _sha(snapshot[name], name)
    if snapshot["unreleased"] is not None:
        _sha(snapshot["unreleased"], "unreleased")
    if snapshot["unreleased_exists"] is not (snapshot["unreleased"] is not None):
        raise PromotionError("recovery plan unreleased existence is inconsistent")
    _sha(manifest["target_unreleased"], "target_unreleased")
    _sha(manifest["target_dev"], "target_dev")
    raw_entries = manifest["entries"]
    if not isinstance(raw_entries, list) or any(not isinstance(x, dict) for x in raw_entries):
        raise PromotionError("recovery plan entries are malformed")
    entries = tuple(RecoveryEntry.from_dict(x) for x in raw_entries)
    for name in ("repository", "version_map", "construction", "transactions"):
        if not isinstance(manifest[name], dict):
            raise PromotionError(f"recovery plan {name} is malformed")
    return RecoveryPlan(sha, manifest, entries)
