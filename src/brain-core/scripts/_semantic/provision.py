"""Provisioning helpers for optional semantic runtime support."""

from __future__ import annotations

from dataclasses import dataclass
import platform
import subprocess
from pathlib import Path

from _lifecycle_common import probe_python, step as _step
import _semantic.config as semantic_config


SEMANTIC_RUNTIME_PACKAGES = (
    "numpy==2.4.4",
    "torch==2.11.0",
    "transformers==5.5.4",
    "sentence-transformers==5.4.1",
)
SEMANTIC_RUNTIME_MODULES = ("numpy", "sentence_transformers")
SEMANTIC_RUNTIME_TIMEOUT = 900
SEMANTIC_ASSET_REFRESH_ERRORS = (OSError, ValueError)


class SemanticProvisionError(RuntimeError):
    """Raised when semantic runtime provisioning cannot make the runtime usable."""


@dataclass(frozen=True)
class SemanticProvisionOutcome:
    """Result of ensuring the semantic runtime and optionally refreshing assets."""

    runtime_changed: bool
    marker_changed: bool
    assets_changed: bool
    assets_error: str | None
    notes: list[str]


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


def refresh_semantic_assets(vault_root: str | Path) -> list[str]:
    """Refresh router, retrieval index, and embeddings sidecars for semantic use."""
    import build_index
    import compile_router

    vault_root = Path(vault_root)
    compiled = compile_router.compile(str(vault_root))
    compile_router.persist_compiled_router(str(vault_root), compiled)
    compile_router.refresh_session_markdown(str(vault_root), compiled)

    index = build_index.build_index(str(vault_root))
    build_index.persist_retrieval_outputs(str(vault_root), index, router=compiled)
    return []


def append_runtime_steps(steps: list[dict], outcome: SemanticProvisionOutcome, *, include_marker: bool = True) -> None:
    """Append shared semantic runtime/marker step shapes from a provision outcome."""
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
    if include_marker:
        steps.append(
            _step(
                "semantic_runtime_marker",
                "changed" if outcome.marker_changed else "noop",
                (
                    "Marked the local semantic runtime as provisioned for this vault."
                    if outcome.marker_changed
                    else "The local semantic runtime is already marked as provisioned for this vault."
                ),
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


def plan_runtime_steps(
    steps: list[dict],
    *,
    runtime_missing: bool,
    marker_missing: bool,
    include_marker: bool = True,
) -> None:
    """Append planned semantic runtime/marker steps for a dry-run path."""
    if runtime_missing:
        steps.append(
            _step(
                "semantic_runtime",
                "planned",
                "Would provision or re-sync the pinned semantic runtime dependencies for this vault.",
            )
        )
        return
    if marker_missing and include_marker:
        steps.append(
            _step(
                "semantic_runtime_marker",
                "planned",
                "Would mark the local semantic runtime as provisioned for this vault.",
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

    marker_changed = semantic_config.set_semantic_engine_installed(vault_root, installed=True)
    notes: list[str] = []
    assets_changed = False
    assets_error = None
    if refresh_assets:
        try:
            notes = refresh_semantic_assets(vault_root)
            assets_changed = True
        except SEMANTIC_ASSET_REFRESH_ERRORS as exc:
            assets_error = f"Semantic runtime is ready, but retrieval asset refresh failed: {exc}"

    return SemanticProvisionOutcome(
        runtime_changed=runtime_changed,
        marker_changed=bool(marker_changed),
        assets_changed=assets_changed,
        assets_error=assets_error,
        notes=notes,
    )
