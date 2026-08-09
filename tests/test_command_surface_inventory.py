"""Characterise the current public command surfaces before the breaking redesign."""

from __future__ import annotations

import asyncio
import ast
import json
from pathlib import Path
import re
import shlex

import config as config_mod
from _common._yaml import load_mapping_file
from brain_mcp import server


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "command_interface_current_surface_v1.json"
VARIANT_PROPERTY_NAMES = {"action", "kind", "op", "operation", "resource", "source"}


def load_current_surface() -> dict:
    """Return the checked-in observation of the pre-cutover command surface."""
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _registered_mcp_tool_names() -> list[str]:
    tree = ast.parse(
        (REPO_ROOT / "src" / "brain-core" / "brain_mcp" / "server.py").read_text(
            encoding="utf-8"
        )
    )
    names: list[str] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            function = decorator.func
            if not (
                isinstance(function, ast.Attribute)
                and isinstance(function.value, ast.Name)
                and function.value.id == "mcp"
                and function.attr == "tool"
            ):
                continue
            explicit_name = next(
                (
                    keyword.value.value
                    for keyword in decorator.keywords
                    if keyword.arg == "name"
                    and isinstance(keyword.value, ast.Constant)
                    and isinstance(keyword.value.value, str)
                ),
                None,
            )
            name = explicit_name or node.name
            if name.startswith("_") and name.endswith("_tool"):
                name = name[1:-5]
            names.append(name)
    return sorted(names)


def _iter_variant_axes(
    node: dict,
    definitions: dict,
    path: tuple[str, ...] = (),
    seen_references: tuple[str, ...] = (),
):
    reference = node.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/$defs/"):
        name = reference.rsplit("/", 1)[-1]
        if name not in seen_references:
            yield from _iter_variant_axes(
                definitions[name],
                definitions,
                path,
                seen_references + (name,),
            )

    for combinator in ("oneOf", "anyOf", "allOf"):
        for child in node.get(combinator, []):
            yield from _iter_variant_axes(child, definitions, path, seen_references)

    for property_name, child in node.get("properties", {}).items():
        values = []
        if "const" in child:
            values = [child["const"]]
        elif isinstance(child.get("enum"), list):
            values = child["enum"]
        if property_name in VARIANT_PROPERTY_NAMES and values:
            yield ".".join(path + (property_name,)), values
        yield from _iter_variant_axes(
            child,
            definitions,
            path + (property_name,),
            seen_references,
        )


def _registered_mcp_variant_axes() -> dict[str, dict[str, list[str]]]:
    axes_by_tool: dict[str, dict[str, set[str]]] = {}
    for tool in asyncio.run(server.mcp.list_tools()):
        definitions = tool.inputSchema.get("$defs", {})
        for path, values in _iter_variant_axes(tool.inputSchema, definitions):
            axes_by_tool.setdefault(tool.name, {}).setdefault(path, set()).update(values)
    return {
        tool_name: {
            path: sorted(values)
            for path, values in sorted(tool_axes.items())
        }
        for tool_name, tool_axes in sorted(axes_by_tool.items())
    }


def _cli_dispatch_commands() -> list[str]:
    source = (REPO_ROOT / "cli" / "brain").read_text(encoding="utf-8")
    match = re.search(r"DISPATCH_SUBCOMMANDS=\(\n(?P<body>.*?)\n\)", source, re.DOTALL)
    assert match is not None, "cli/brain no longer declares DISPATCH_SUBCOMMANDS"
    return sorted(shlex.split(match.group("body")))


def _cli_special_commands() -> list[str]:
    source = (REPO_ROOT / "cli" / "brain").read_text(encoding="utf-8")
    main = source.rsplit("main() {", 1)[1]
    match = re.search(
        r'case "\$subcommand" in(?P<body>.*?)^\s*\*\)',
        main,
        re.DOTALL | re.MULTILINE,
    )
    assert match is not None, "cli/brain no longer has an explicit main dispatch case"

    commands: set[str] = set()
    for label in re.findall(r"^\s*([^\n)]+)\)", match.group("body"), re.MULTILINE):
        for token in label.split("|"):
            token = token.strip()
            if token in {"--help", "-h", "help"}:
                continue
            commands.add("version" if token == "--version" else token)
    return sorted(commands)


def test_observation_identifies_its_source_contract() -> None:
    """The snapshot declares its schema and committed Brain Core version."""
    fixture = load_current_surface()
    assert fixture["schema"] == "brain.command-surface-observation/1"
    version = (REPO_ROOT / "src" / "brain-core" / "VERSION").read_text(
        encoding="utf-8"
    ).strip()
    assert fixture["brain_core_version"] == version


def test_observed_mcp_tools_match_registration_source() -> None:
    """Every currently registered MCP tool is captured exactly once."""
    observed = load_current_surface()["observed_surfaces"]["mcp_tools"]
    assert observed == sorted(set(observed))
    assert _registered_mcp_tool_names() == observed


def test_observed_mcp_variant_axes_match_projected_schemas() -> None:
    """Every discriminator-like public schema axis is characterised."""
    observed = load_current_surface()["observed_surfaces"]["mcp_variant_axes"]
    assert _registered_mcp_variant_axes() == observed


def test_observed_cli_dispatch_matches_launcher_source() -> None:
    """Every generic CLI dispatch leaf is captured exactly once."""
    observed = load_current_surface()["observed_surfaces"]["cli"]["dispatched"]
    assert observed == sorted(set(observed))
    assert _cli_dispatch_commands() == observed


def test_observed_special_cli_commands_remain_explicit() -> None:
    """CLI-owned dispatch branches are captured exhaustively."""
    observed = load_current_surface()["observed_surfaces"]["cli"]["special"]
    assert observed == sorted(set(observed))
    assert _cli_special_commands() == observed


def test_profile_validation_and_defaults_match_observed_mcp_tools() -> None:
    """Profile validation and shipped defaults cannot drift from registration."""
    fixture = load_current_surface()["observed_surfaces"]
    registered = set(fixture["mcp_tools"])
    assert config_mod._VALID_TOOLS == registered

    defaults = load_mapping_file(
        REPO_ROOT / "src" / "brain-core" / "defaults" / "config.yaml"
    )
    actual_profiles = {
        name: profile["allow"]
        for name, profile in defaults["vault"]["profiles"].items()
    }
    observed_profiles = fixture["default_profiles"]
    assert set(actual_profiles) == set(observed_profiles)
    for name, tools in actual_profiles.items():
        assert len(tools) == len(set(tools)), f"duplicate tool in {name} profile"
        assert set(tools) == set(observed_profiles[name])
    assert set(actual_profiles["operator"]) == registered


def test_in_progress_inventory_names_every_pending_surface() -> None:
    """The first slice must not imply the broader Phase 1 inventory is complete."""
    fixture = load_current_surface()
    assert fixture["inventory_status"] == "in_progress"
    assert fixture["pending_surfaces"] == [
        "cli_nested_operations",
        "direct_scripts",
        "public_python_wrappers",
        "installer_and_upgrader_operations",
        "documented_operations_and_consumers",
    ]
