"""Vault-local semantic model provisioning and local-only loading helpers."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
from typing import Any

from _common import safe_write_json
from _lifecycle.retrieval_errors import SemanticRuntimeUnavailableError


SEMANTIC_MODEL_MANIFEST_REL = os.path.join(".brain", "local", "semantic-model-manifest.json")
SEMANTIC_MODELS_DIR_REL = os.path.join(".brain", "local", "semantic-models")
SHIPPED_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
SHIPPED_MODEL_REVISION = "c9745ed1d9f207416be6d2e6f8de32d1f16199bf"

_CACHED_QUERY_ENCODER = None
_CACHED_QUERY_ENCODER_LOCK = threading.Lock()


class SemanticModelError(RuntimeError):
    """Raised when the vault-local semantic model is unavailable or unusable."""


class SemanticModelProvisionError(SemanticModelError):
    """Raised when model provisioning cannot complete successfully."""


class SemanticModelMissingError(SemanticModelError):
    """Raised when the expected local semantic model state is absent."""


class SemanticModelRevisionMismatchError(SemanticModelError):
    """Raised when the provisioned semantic model no longer matches the shipped pin."""


class SemanticModelLoadError(SemanticModelError):
    """Raised when a provisioned local semantic model cannot be loaded."""


@dataclass(frozen=True)
class ModelManifest:
    """Durable local record of the provisioned semantic model identity."""

    model_name: str
    revision: str
    provisioned_at: str
    version: int = 1


@dataclass(frozen=True)
class SemanticModelProvisionOutcome:
    """Result of ensuring the pinned semantic model snapshot is present locally."""

    model_name: str
    revision: str
    local_path: str
    downloaded: bool
    manifest_changed: bool
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModelState:
    """Structured inspection view of the vault-local semantic model state."""

    manifest: ModelManifest | None
    snapshot_path: Path
    manifest_missing: bool
    model_path_missing: bool
    model_revision_mismatch: bool
    load_error: str | None

    @property
    def healthy(self) -> bool:
        return not (
            self.manifest_missing
            or self.model_path_missing
            or self.model_revision_mismatch
            or self.load_error is not None
        )


def shipped_model_pin() -> tuple[str, str]:
    """Return the shipped semantic model identity."""
    return (SHIPPED_MODEL_NAME, SHIPPED_MODEL_REVISION)


def manifest_path(vault_root: str | Path) -> Path:
    """Return the vault-local semantic model manifest path."""
    return Path(vault_root) / SEMANTIC_MODEL_MANIFEST_REL


def model_snapshot_path(vault_root: str | Path, model_name: str, revision: str) -> Path:
    """Return the deterministic vault-local snapshot directory for a model pin."""
    # Hugging Face repo ids contain `/`; flatten them so each pin stays under one
    # vault-local directory tree rooted at `.brain/local/semantic-models/`.
    sanitised = model_name.replace("/", "__")
    return Path(vault_root) / SEMANTIC_MODELS_DIR_REL / sanitised / revision


def clear_query_encoder() -> None:
    """Drop the in-process cached local query encoder, if any."""
    global _CACHED_QUERY_ENCODER
    with _CACHED_QUERY_ENCODER_LOCK:
        _CACHED_QUERY_ENCODER = None


def read_manifest(vault_root: str | Path) -> ModelManifest | None:
    """Return the local semantic model manifest, or None when absent."""
    path = manifest_path(vault_root)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SemanticModelLoadError(f"semantic model manifest is unreadable: {exc}") from exc
    if not isinstance(payload, dict):
        raise SemanticModelLoadError("semantic model manifest is not a JSON object")
    version = payload.get("version")
    model_name = payload.get("model_name")
    revision = payload.get("revision")
    provisioned_at = payload.get("provisioned_at")
    if version != 1 or not all(isinstance(value, str) for value in (model_name, revision, provisioned_at)):
        raise SemanticModelLoadError("semantic model manifest is missing required fields")
    return ModelManifest(
        version=version,
        model_name=model_name,
        revision=revision,
        provisioned_at=provisioned_at,
    )


def write_manifest(vault_root: str | Path, manifest: ModelManifest) -> bool:
    """Persist the semantic model manifest. Returns True when content changed."""
    path = manifest_path(vault_root)
    payload = {
        "version": manifest.version,
        "model_name": manifest.model_name,
        "revision": manifest.revision,
        "provisioned_at": manifest.provisioned_at,
    }
    existing: dict[str, Any] | None = None
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            loaded = None
        if isinstance(loaded, dict):
            existing = loaded
    if existing == payload:
        return False
    safe_write_json(str(path), payload, bounds=str(vault_root))
    return True


def inspect_model_state(vault_root: str | Path) -> ModelState:
    """Inspect whether the shipped semantic model is present and locally usable."""
    manifest = None
    manifest_missing = False
    model_revision_mismatch = False
    load_error = None
    model_name, revision = shipped_model_pin()
    try:
        manifest = read_manifest(vault_root)
    except SemanticModelLoadError as exc:
        load_error = str(exc)

    if manifest is None and load_error is None:
        manifest_missing = True

    effective_name = manifest.model_name if manifest is not None else model_name
    effective_revision = manifest.revision if manifest is not None else revision
    snapshot_path = model_snapshot_path(vault_root, effective_name, effective_revision)
    model_path_missing = not snapshot_path.is_dir()

    if manifest is not None and (manifest.model_name != model_name or manifest.revision != revision):
        model_revision_mismatch = True

    return ModelState(
        manifest=manifest,
        snapshot_path=snapshot_path,
        manifest_missing=manifest_missing,
        model_path_missing=model_path_missing,
        model_revision_mismatch=model_revision_mismatch,
        load_error=load_error,
    )


def verify_local_model_load(state: ModelState) -> ModelState:
    """Return `state`, augmented with a local-load error when the snapshot is unreadable."""
    if (
        state.load_error is not None
        or state.manifest_missing
        or state.model_path_missing
        or state.model_revision_mismatch
    ):
        return state
    try:
        _load_sentence_transformer(state.snapshot_path)
    except SemanticModelLoadError as exc:
        return replace(state, load_error=str(exc))
    return state


def provision_semantic_model(vault_root: str | Path) -> SemanticModelProvisionOutcome:
    """Ensure the shipped semantic model snapshot exists locally for this vault."""
    model_name, revision = shipped_model_pin()
    expected_path = model_snapshot_path(vault_root, model_name, revision)
    manifest = None
    notes: list[str] = []
    try:
        manifest = read_manifest(vault_root)
    except SemanticModelLoadError as exc:
        manifest = None
        notes.append(f"Replaced an unreadable semantic model manifest: {exc}")

    manifest_matches = (
        manifest is not None
        and manifest.model_name == model_name
        and manifest.revision == revision
    )
    needs_download = not (manifest_matches and expected_path.is_dir())
    if not needs_download:
        try:
            _load_sentence_transformer(expected_path)
        except SemanticModelLoadError:
            needs_download = True

    if needs_download:
        _download_snapshot(model_name, revision, expected_path)
        _load_sentence_transformer(expected_path)

    manifest_changed = False
    if needs_download or not manifest_matches:
        manifest_changed = write_manifest(
            vault_root,
            ModelManifest(
                model_name=model_name,
                revision=revision,
                provisioned_at=datetime.now(timezone.utc).astimezone().isoformat(),
            ),
        )
    return SemanticModelProvisionOutcome(
        model_name=model_name,
        revision=revision,
        local_path=str(expected_path),
        downloaded=needs_download,
        manifest_changed=manifest_changed,
        notes=tuple(notes),
    )


def load_local_model_with_manifest(vault_root: str | Path):
    """Load the local semantic model and return it with the matching manifest."""
    manifest = read_manifest(vault_root)
    if manifest is None:
        raise SemanticModelMissingError(
            "semantic model manifest is missing; run `python3 .brain-core/scripts/repair.py semantic`"
        )
    expected_name, expected_revision = shipped_model_pin()
    if manifest.model_name != expected_name or manifest.revision != expected_revision:
        raise SemanticModelRevisionMismatchError(
            "semantic model revision does not match the shipped pin; run `python3 .brain-core/scripts/repair.py semantic`"
        )
    snapshot_path = model_snapshot_path(vault_root, manifest.model_name, manifest.revision)
    if not snapshot_path.is_dir():
        raise SemanticModelMissingError(
            "semantic model snapshot is missing; run `python3 .brain-core/scripts/repair.py semantic`"
        )
    return (_load_sentence_transformer(snapshot_path), manifest)


def load_local_model(vault_root: str | Path):
    """Load the local semantic model for this vault without network access."""
    model, _manifest = load_local_model_with_manifest(vault_root)
    return model


def get_query_encoder(vault_root: str | Path):
    """Return a cached local query encoder for this vault and shipped model pin."""
    global _CACHED_QUERY_ENCODER
    manifest = read_manifest(vault_root)
    if manifest is None:
        raise SemanticModelMissingError(
            "semantic model manifest is missing; run `python3 .brain-core/scripts/repair.py semantic`"
        )
    identity = (
        os.path.realpath(str(vault_root)),
        manifest.model_name,
        manifest.revision,
    )
    cached = _CACHED_QUERY_ENCODER
    if cached is not None and cached[0] == identity:
        return cached[1]
    with _CACHED_QUERY_ENCODER_LOCK:
        cached = _CACHED_QUERY_ENCODER
        if cached is None or cached[0] != identity:
            encoder = load_local_model(vault_root)
            _CACHED_QUERY_ENCODER = (identity, encoder)
        return _CACHED_QUERY_ENCODER[1]


def _download_snapshot(model_name: str, revision: str, snapshot_path: Path) -> None:
    """Download the shipped model snapshot directly into the vault-local path."""
    from huggingface_hub import snapshot_download
    from huggingface_hub.errors import (
        EntryNotFoundError,
        HfHubHTTPError,
        LocalEntryNotFoundError,
        RepositoryNotFoundError,
        RevisionNotFoundError,
    )

    snapshot_path.mkdir(parents=True, exist_ok=True)
    try:
        snapshot_download(
            repo_id=model_name,
            revision=revision,
            local_dir=snapshot_path,
        )
    except (
        EntryNotFoundError,
        HfHubHTTPError,
        LocalEntryNotFoundError,
        OSError,
        RepositoryNotFoundError,
        RevisionNotFoundError,
        ValueError,
    ) as exc:
        raise SemanticModelProvisionError(
            f"semantic model provisioning failed for {model_name}@{revision}: {exc}"
        ) from exc


def _load_sentence_transformer(snapshot_path: Path):
    """Load a local semantic model snapshot without network access."""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise SemanticRuntimeUnavailableError(
            f"semantic runtime dependencies are unavailable: {exc}",
            operation="loading semantic model",
        ) from exc
    try:
        return SentenceTransformer(str(snapshot_path), local_files_only=True)
    except (OSError, ValueError) as exc:
        raise SemanticModelLoadError(
            f"semantic model snapshot at {snapshot_path} could not be loaded locally: {exc}"
        ) from exc
