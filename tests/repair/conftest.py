"""Shared fixtures for the repair test suite."""

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


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    """Repair tests resolve the central runtime under `~/.brain/venvs/`.

    Redirect HOME to a per-test directory so we never read or create real
    venvs in the developer's home — and so tests cannot pass via leftover
    state from a previous run.
    """
    fake_home = tmp_path / "_home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))


@pytest.fixture
def repair_vault(tmp_path):
    """Minimal vault that can exercise repair scopes."""
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("0.32.5\n")
    (bc / "session-core.md").write_text("Always:\n- Keep types tidy.\n")
    (bc / "brain_mcp").mkdir()
    (bc / "brain_mcp" / "requirements.txt").write_text("mcp>=1.0.0\n")
    # Repair loads the canonical venv path-resolver from the vault to avoid
    # duplicating the rule in repair.py — copy it from source so the fixture
    # mirrors a real vault layout.
    venv_helper_src = (
        Path(__file__).resolve().parents[2]
        / "src" / "brain-core" / "scripts" / "_common" / "_venv.py"
    )
    venv_helper_dst = bc / "scripts" / "_common" / "_venv.py"
    venv_helper_dst.parent.mkdir(parents=True)
    venv_helper_dst.write_text(venv_helper_src.read_text())

    (tmp_path / ".brain" / "local").mkdir(parents=True)
    (tmp_path / "_Config").mkdir()
    (tmp_path / "_Config" / "router.md").write_text("Brain vault.\n\nAlways:\n- Typed folders.\n")
    (tmp_path / "Wiki").mkdir()
    write_md(
        tmp_path / "Wiki" / "Test Page.md",
        {"type": "living/wiki", "tags": ["test"], "key": "test-page"},
        "# Test Page",
    )
    return tmp_path

