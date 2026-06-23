"""Shared plain helpers for the MCP server test suite."""

import asyncio
import contextlib
import json
import os
import subprocess
import tempfile
import threading
import time
import types
from unittest.mock import patch

import pytest

from mcp.types import CallToolResult

import _lifecycle.retrieval_assets as retrieval_assets
import _lifecycle.retrieval_errors as retrieval_errors
import _search.paths as search_paths
import _search.semantic_query as semantic_query
import _semantic.assets as semantic_assets
import _semantic.model as semantic_model
import _semantic.runtime as semantic_runtime
from brain_mcp import _server_artefacts, _server_content, _server_reading, server
import compile_router
import obsidian_cli
import process
import retrieval_embeddings
import workspace_registry
import config as config_mod
from _common._yaml import dump_mapping_text




def _assert_error(result, substring=None):
    """Assert result is a CallToolResult with isError flag."""
    assert isinstance(result, CallToolResult), f"Expected CallToolResult, got {type(result)}"
    assert result.isError is True
    if substring:
        assert substring in result.content[0].text


def _bump_mtime(path, *, seconds: float = 10.0):
    """Make *path* look newer without waiting for filesystem timestamp ticks."""
    ts = time.time() + seconds
    os.utime(path, (ts, ts))


def _extract_create_path(result):
    """Extract path from 'Created type: path' format."""
    return result.split(": ", 1)[1]


def _list_result_lines(response):
    """Extract individual result lines from a brain_list response (skipping meta)."""
    if isinstance(response, str):
        return []
    if len(response) < 2:
        return []
    return response[1].text.strip().split("\n")


def _list_text(response):
    """Join TextContent blocks into single string for list assertions."""
    if isinstance(response, str):
        return response
    return "\n".join(block.text for block in response)


def _progress_payload(result):
    """Decode a structured warmup/progress payload."""
    assert isinstance(result, CallToolResult), f"Expected CallToolResult, got {type(result)}"
    assert result.isError is True
    return json.loads(result.content[0].text)


def _search_result_lines(response):
    """Extract individual result lines from a search response (skipping meta)."""
    if isinstance(response, str):
        return []
    if len(response) < 2:
        return []
    return response[1].text.strip().split("\n")


def _search_text(response):
    """Join TextContent blocks into single string for search assertions."""
    if isinstance(response, str):
        return response
    return "\n".join(block.text for block in response)


def _write_config_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    _bump_mtime(path)


def _write_config_yaml(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_mapping_text(data), encoding="utf-8")
    _bump_mtime(path)

