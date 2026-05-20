"""Provisioning helpers for optional semantic runtime support."""

from __future__ import annotations

from dataclasses import dataclass
import platform
import subprocess
from pathlib import Path

from _bootstrap.runtime import probe_python, step as _step
from _lifecycle.retrieval_assets import refresh_retrieval_assets
from _lifecycle.retrieval_errors import (
    CompiledRouterUnavailableError,
    RetrievalPersistenceError,
    SemanticRuntimeUnavailableError,
    UnreadableRetrievalSourceError,
)
import _semantic.config as semantic_config
import _semantic.model as semantic_model


SEMANTIC_RUNTIME_PACKAGES = (
    "huggingface-hub==1.13.0",
    "numpy==2.4.4",
    "torch==2.11.0",
    "transformers==5.5.4",
    "sentence-transformers==5.4.1",
)
SEMANTIC_RUNTIME_MODULES = ("huggingface_hub", "numpy", "sentence_transformers")
SEMANTIC_RUNTIME_TIMEOUT = 900
_SEMANTIC_ASSET_REFRESH_SUMMARIES = {
    UnreadableRetrievalSourceError: (
        "Semantic runtime is ready, but retrieval asset refresh failed because a retrieval source is unreadable"
    ),
    CompiledRouterUnavailableError: (
        "Semantic runtime is ready, but retrieval asset refresh failed because the compiled router is unavailable"
    ),
    RetrievalPersistenceError: (
        "Semantic runtime is ready, but retrieval asset refresh failed because derived retrieval state could not be persisted"
    ),
    SemanticRuntimeUnavailableError: (
        "Semantic runtime is ready, but retrieval asset refresh failed because semantic runtime dependencies are unavailable"
    ),
    semantic_model.SemanticModelError: (
        "Semantic runtime is ready, but retrieval asset refresh failed because the local semantic model is unavailable or unusable"
    ),
}
SEMANTIC_ASSET_REFRESH_ERRORS = tuple(_SEMANTIC_ASSET_REFRESH_SUMMARIES)


class SemanticProvisionError(RuntimeError):
    """Raised when semantic runtime provisioning cannot make the runtime usable."""


@dataclass(frozen=True)
class SemanticProvisionOutcome:
    """Result of ensuring the semantic runtime and optionally refreshing assets."""

    runtime_changed: bool
    model_outcome: semantic_model.SemanticModelProvisionOutcome
    marker_changed: bool
    marker_installed: bool
    assets_changed: bool
    assets_error: str | None
    notes: list[str]


def refresh_semantic_assets(vault_root: str | Path) -> list[str]:
    """Refresh router, retrieval index, and semantic sidecars for semantic use."""
    return refresh_retrieval_assets(vault_root, force_embeddings=True)


def semantic_runtime_supported_platform(*, system: str | None = None, machine: str | None = None) -> tuple[bool, str | None]:
    """Return whether semantic runtime provisioning is supported on this platform."""
    system = system or platform.system()
    machine = machine or platform.machine()
    if system == "Darwin" and machine == "x86_64":
        return (
            False,
            "semantic runtime is unsupported on Intel macOS; use lexical mode only",
        )
    return True, None


def sync_runtime_packages(python_executable: str) -> None:
    """Install the pinned semantic runtime into the target Python environment."""
    subprocess.run(
        [python_executable, "-m", "pip", "install", *SEMANTIC_RUNTIME_PACKAGES],
        check=True,
        timeout=SEMANTIC_RUNTIME_TIMEOUT,
    )


def _format_asset_refresh_error(exc: BaseException) -> str:
    """Return an operator-facing semantic refresh failure summary."""
    for error_type, summary in _SEMANTIC_ASSET_REFRESH_SUMMARIES.items():
        if isinstance(exc, error_type):
            return f"{summary}: {exc}"
    raise AssertionError(f"unexpected refresh error type: {type(exc).__name__}")


def append_runtime_steps(steps: list[dict], outcome: SemanticProvisionOutcome) -> None:
    """Append shared semantic runtime/model step shapes from a provision outcome."""
    steps.append(
        _step(
            "semantic_runtime",
            "changed" if outcome.runtime_changed else "noop",
            (
                "Provisioned the pinned semantic runtime dependencies for this vault."
                if outcome.runtime_changed
                else "Semantic runtime dependencies are already provisioned."
            ),
        )
    )
    append_model_step(steps, outcome.model_outcome)


def append_model_step(steps: list[dict], outcome: semantic_model.SemanticModelProvisionOutcome) -> None:
    """Append the shared semantic-model step shape from a provision outcome."""
    if outcome.downloaded:
        status = "changed"
        message = "Provisioned the pinned semantic model snapshot for this vault."
    elif outcome.manifest_changed:
        status = "changed"
        message = "Recorded the pinned semantic model manifest for this vault."
    else:
        status = "noop"
        message = "The pinned semantic model snapshot is already provisioned locally for this vault."
    steps.append(_step("semantic_model", status, message))


def append_marker_step(steps: list[dict], outcome: SemanticProvisionOutcome) -> None:
    """Append the shared semantic runtime-marker step from a provision outcome."""
    if outcome.marker_installed:
        message = (
            "Marked the local semantic runtime as provisioned for this vault."
            if outcome.marker_changed
            else "The local semantic runtime is already marked as provisioned for this vault."
        )
    else:
        message = (
            "Cleared the local semantic runtime marker until semantic provisioning completes successfully."
            if outcome.marker_changed
            else "The local semantic runtime remains unmarked until semantic provisioning completes successfully."
        )
    steps.append(
        _step(
            "semantic_runtime_marker",
            "changed" if outcome.marker_changed else "noop",
            message,
        )
    )


def append_asset_step(steps: list[dict], notes: list[str], outcome: SemanticProvisionOutcome) -> None:
    """Append a shared semantic asset step from a provision outcome."""
    if outcome.assets_error:
        steps.append(_step("semantic_assets", "error", outcome.assets_error))
    elif outcome.assets_changed:
        steps.append(
            _step(
                "semantic_assets",
                "changed",
                "Rebuilt the compiled router, retrieval index, and semantic embeddings sidecars.",
            )
        )
    notes.extend(outcome.notes)


def plan_runtime_step(steps: list[dict], *, runtime_missing: bool) -> None:
    """Append the planned semantic runtime step for a dry-run path."""
    if runtime_missing:
        steps.append(
            _step(
                "semantic_runtime",
                "planned",
                "Would provision or re-sync the pinned semantic runtime dependencies for this vault.",
            )
        )


def plan_marker_step(steps: list[dict], *, marker_missing: bool) -> None:
    """Append the planned semantic runtime-marker step for a dry-run path."""
    if marker_missing:
        steps.append(
            _step(
                "semantic_runtime_marker",
                "planned",
                "Would mark the local semantic runtime as provisioned for this vault once semantic provisioning completes successfully.",
            )
        )


def plan_model_step(
    steps: list[dict],
    *,
    model_needs_provision: bool,
) -> None:
    """Append the planned semantic-model step for a dry-run path."""
    if model_needs_provision:
        steps.append(
            _step(
                "semantic_model",
                "planned",
                "Would provision or update the pinned local semantic model snapshot for this vault.",
            )
        )


def plan_asset_step(steps: list[dict], *, assets_missing: bool) -> None:
    """Append the planned semantic asset-refresh step for a dry-run path."""
    if assets_missing:
        steps.append(
            _step(
                "semantic_assets",
                "planned",
                "Would rebuild the compiled router, retrieval index, and semantic embeddings sidecars.",
            )
        )


def provision_semantic_runtime(
    vault_root: str | Path,
    *,
    python_executable: str,
    runtime_ok: bool | None = None,
    refresh_assets: bool = True,
) -> SemanticProvisionOutcome:
    """Ensure the semantic runtime is usable and optionally refresh semantic assets."""
    supported, unsupported_reason = semantic_runtime_supported_platform()
    if not supported:
        semantic_config.set_semantic_engine_installed(vault_root, installed=False)
        raise SemanticProvisionError(
            unsupported_reason or "semantic runtime is unsupported on this platform"
        )

    runtime_changed = False
    if runtime_ok is None:
        runtime_probe = probe_python(python_executable, modules=SEMANTIC_RUNTIME_MODULES)
        runtime_ok = bool(runtime_probe.get("ok"))
    if not runtime_ok:
        semantic_config.set_semantic_engine_installed(vault_root, installed=False)
        try:
            sync_runtime_packages(python_executable)
        except subprocess.CalledProcessError as exc:
            raise SemanticProvisionError(
                f"Semantic runtime dependency installation failed with exit code {exc.returncode}."
            ) from exc
        runtime_probe = probe_python(python_executable, modules=SEMANTIC_RUNTIME_MODULES)
        if not runtime_probe.get("ok"):
            raise SemanticProvisionError(
                "Semantic runtime dependency installation completed, but required modules are still unavailable."
            )
        runtime_changed = True

    try:
        model_outcome = semantic_model.provision_semantic_model(vault_root)
    except semantic_model.SemanticModelProvisionError as exc:
        semantic_config.set_semantic_engine_installed(vault_root, installed=False)
        raise SemanticProvisionError(str(exc)) from exc

    notes: list[str] = list(model_outcome.notes)
    assets_changed = False
    assets_error = None
    if refresh_assets:
        try:
            notes.extend(refresh_semantic_assets(vault_root))
            assets_changed = True
        except SEMANTIC_ASSET_REFRESH_ERRORS as exc:
            assets_error = _format_asset_refresh_error(exc)

    marker_installed = assets_error is None
    marker_changed = semantic_config.set_semantic_engine_installed(
        vault_root,
        installed=marker_installed,
    )

    return SemanticProvisionOutcome(
        runtime_changed=runtime_changed,
        model_outcome=model_outcome,
        marker_changed=bool(marker_changed),
        marker_installed=marker_installed,
        assets_changed=assets_changed,
        assets_error=assets_error,
        notes=notes,
    )
