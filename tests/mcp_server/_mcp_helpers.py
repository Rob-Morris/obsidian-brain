"""Shared plain helpers for the MCP server test suite."""

import json
import os
import time

from mcp.types import CallToolResult

from _common._yaml import dump_mapping_text


def _assert_any_error(result):
    """Assert result is a CallToolResult with isError flag."""
    assert isinstance(result, CallToolResult), f"Expected CallToolResult, got {type(result)}"
    assert result.isError is True


def _assert_error(result, substring):
    """Assert result is a CallToolResult error containing expected text."""
    _assert_any_error(result)
    assert substring in result.content[0].text


def _bump_mtime(path, *, seconds: float = 10.0):
    """Make *path* look newer without waiting for filesystem timestamp ticks."""
    ts = time.time() + seconds
    os.utime(path, (ts, ts))


def _extract_create_path(result):
    """Extract path from 'Created type: path' format."""
    return result.split(": ", 1)[1]


def _result_lines(response, *, label):
    """Extract individual result lines from a content-block response."""
    assert not isinstance(response, str), f"Expected {label} content blocks, got {response!r}"
    assert len(response) >= 1, f"Expected at least {label} metadata block, got {response!r}"
    if len(response) == 1:
        return []
    assert hasattr(response[1], "text"), f"Expected text block at index 1, got {response[1]!r}"
    return response[1].text.strip().split("\n")


def _result_text(response):
    """Join TextContent blocks into a single string."""
    if isinstance(response, str):
        return response
    return "\n".join(block.text for block in response)


def _list_result_lines(response):
    """Extract individual result lines from a brain_list response (skipping meta)."""
    return _result_lines(response, label="brain_list")


def _list_text(response):
    """Join TextContent blocks into single string for list assertions."""
    return _result_text(response)


def _progress_payload(result):
    """Decode a structured warmup/progress payload."""
    assert isinstance(result, CallToolResult), f"Expected CallToolResult, got {type(result)}"
    assert result.isError is True
    return json.loads(result.content[0].text)


def _search_result_lines(response):
    """Extract individual result lines from a search response (skipping meta)."""
    return _result_lines(response, label="search")


def _search_text(response):
    """Join TextContent blocks into single string for search assertions."""
    return _result_text(response)


def _write_config_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    _bump_mtime(path)


def _write_config_yaml(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_mapping_text(data), encoding="utf-8")
    _bump_mtime(path)
