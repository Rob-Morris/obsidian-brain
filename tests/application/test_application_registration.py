"""Conformance for the canonical selected-Brain command inventory."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import get_args

from _application.registry import (
    current_application_catalogue,
    current_request_resolver,
)
from _application.requests import CommandRequest
from _application import requests


APPLICATION_ROOT = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "brain-core"
    / "scripts"
    / "_application"
)


def test_static_request_union_matches_the_registered_application_surface():
    catalogue = current_application_catalogue()
    resolver = current_request_resolver()

    union_types = frozenset(get_args(CommandRequest))
    catalogue_types = frozenset(entry.request_type for entry in catalogue.entries)
    resolver_types = frozenset(entry.request_type for entry in resolver.entries)

    assert union_types == catalogue_types == resolver_types

    exported_types = frozenset(
        getattr(requests, name)
        for name in requests.__all__
        if name != "CommandRequest"
    )
    assert exported_types == union_types


def test_no_retired_command_identity_remains_in_application_modules():
    registered = {
        entry.command_id for entry in current_application_catalogue().entries
    }
    declared = set()
    for path in APPLICATION_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
            if not any(
                isinstance(target, ast.Name) and target.id == "COMMAND_ID"
                for target in targets
            ):
                continue
            if isinstance(node.value, ast.Constant) and node.value.value:
                declared.add(node.value.value)

    assert declared == registered
