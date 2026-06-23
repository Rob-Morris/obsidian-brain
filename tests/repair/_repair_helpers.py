"""Shared plain helpers for the repair test suite."""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import _bootstrap.diagnostics as bootstrap_diagnostics
import _bootstrap.mcp_state as bootstrap_mcp_state
import _bootstrap.runtime as bootstrap_runtime
from _common import _shell
import _lifecycle.frontmatter_repairs as frontmatter_repairs
from _lifecycle.derived_cache_state import CacheState
import _lifecycle.semantic_repairs as semantic_repairs
import _semantic.config as semantic_config
import _semantic.model as semantic_model
import _repair_common as repair_common
import _repair_runtime as repair_runtime
import check
import migrate_to_0_48_2
import repair
from brain_test_support import make_router, write_md


def _wiki_router():
    artefact = {
        "folder": "Wiki",
        "type": "living/wiki",
        "key": "wiki",
        "classification": "living",
        "configured": True,
        "path": "Wiki",
        "naming": {"pattern": "{name}.md", "folder": "Wiki/"},
        "frontmatter": {
            "type": "living/wiki",
            "required": ["type", "tags"],
            "status_enum": None,
            "terminal_statuses": None,
        },
        "taxonomy_file": "_Config/Taxonomy/Living/wiki.md",
        "template_file": None,
        "trigger": None,
    }
    return make_router([artefact], meta={"brain_core_version": "0.32.5"})


def _register_project_client(vault: Path, client: str) -> dict:
    server_config = bootstrap_diagnostics._expected_project_server_config(vault)
    if client == "claude":
        has_claude_cli = repair_runtime.mcp_transport._has_claude_cli
        repair_runtime.mcp_transport._has_claude_cli = lambda: False
        try:
            record = repair_runtime.mcp_transport.register_claude(vault, server_config, "project", vault)
        finally:
            repair_runtime.mcp_transport._has_claude_cli = has_claude_cli
    else:
        record = repair_runtime.mcp_transport.register_codex(server_config, "project", vault)
    repair_runtime.mcp_transport.record_init_target(vault, record)
    return server_config


def _write_legacy_session_hook(vault: Path, *, machine_python: str = "/usr/bin/python3.12") -> str:
    legacy_command = (
        "echo 'brain_session called:' "
        f"&& {machine_python} {vault / '.brain-core' / 'scripts' / 'session.py'} "
        f"--vault {vault} --workspace-dir {vault} --json"
    )
    settings_path = vault / ".claude" / "settings.local.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps({
        "hooks": {
            "SessionStart": [
                {
                    "hooks": [
                        {"type": "command", "command": legacy_command},
                    ]
                }
            ]
        }
    }))
    return legacy_command


def _model_outcome(vault: Path, *, downloaded=False, manifest_changed=False):
    return semantic_model.SemanticModelProvisionOutcome(
        model_name=semantic_model.SHIPPED_MODEL_NAME,
        revision=semantic_model.SHIPPED_MODEL_REVISION,
        local_path=str(
            semantic_model.model_snapshot_path(
                vault,
                semantic_model.SHIPPED_MODEL_NAME,
                semantic_model.SHIPPED_MODEL_REVISION,
            )
        ),
        downloaded=downloaded,
        manifest_changed=manifest_changed,
        notes=(),
    )


def _model_state(
    vault: Path,
    *,
    manifest_missing=False,
    model_path_missing=False,
    model_revision_mismatch=False,
    load_error=None,
):
    manifest = None
    if not manifest_missing:
        manifest = semantic_model.ModelManifest(
            model_name=semantic_model.SHIPPED_MODEL_NAME,
            revision=semantic_model.SHIPPED_MODEL_REVISION,
            provisioned_at="2026-05-06T00:00:00+10:00",
        )
    return semantic_model.ModelState(
        manifest=manifest,
        snapshot_path=semantic_model.model_snapshot_path(
            vault,
            semantic_model.SHIPPED_MODEL_NAME,
            semantic_model.SHIPPED_MODEL_REVISION,
        ),
        manifest_missing=manifest_missing,
        model_path_missing=model_path_missing,
        model_revision_mismatch=model_revision_mismatch,
        load_error=load_error,
    )


def _mock_healthy_runtime(monkeypatch):
    monkeypatch.setattr(
        bootstrap_diagnostics,
        "inspect_runtime",
        lambda _vault: {
            "healthy": True,
            "python": sys.executable,
            "issues": [],
            "missing_modules": [],
            "message": "Central managed runtime is ready for packageful Brain work.",
        },
    )

