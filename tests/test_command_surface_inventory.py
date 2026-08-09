"""Characterise the current public command surfaces before the breaking redesign."""

from __future__ import annotations

import asyncio
import argparse
import ast
import json
from pathlib import Path
import re
import shlex
from unittest.mock import patch

import config as config_mod
import configure
import create
import define
import edit
import lifecycle
import list_artefacts
import machine
import read
import setup
from _common._yaml import load_mapping_file
from _repair_common import REPAIR_SCOPES
from brain_mcp import server


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "command_interface_current_surface_v1.json"
PRE_CUTOVER_BRAIN_CORE_VERSION = "0.54.0"
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


def _is_main_guard(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
        and len(node.test.ops) == 1
        and isinstance(node.test.ops[0], ast.Eq)
        and len(node.test.comparators) == 1
        and isinstance(node.test.comparators[0], ast.Constant)
        and node.test.comparators[0].value == "__main__"
    )


def _direct_script_entrypoints() -> list[str]:
    scripts_dir = REPO_ROOT / "src" / "brain-core" / "scripts"
    return sorted(
        path.relative_to(scripts_dir).as_posix()
        for path in scripts_dir.rglob("*.py")
        if any(
            _is_main_guard(node)
            for node in ast.parse(path.read_text(encoding="utf-8")).body
        )
    )


def _public_python_wrappers() -> list[str]:
    tree = ast.parse(
        (REPO_ROOT / "src" / "brain-core" / "brain_mcp" / "server.py").read_text(
            encoding="utf-8"
        )
    )
    return sorted(
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("brain_")
    )


def _captured_parser(parse_args) -> argparse.ArgumentParser:
    parser: argparse.ArgumentParser | None = None

    def capture(current, _args=None, _namespace=None):
        nonlocal parser
        parser = current
        return argparse.Namespace()

    with patch.object(argparse.ArgumentParser, "parse_args", capture):
        parse_args([])
    assert parser is not None
    return parser


def _choice_values(parser: argparse.ArgumentParser, destination: str) -> list[str]:
    for action in parser._actions:
        if action.dest == destination and action.choices is not None:
            return sorted(action.choices)
    raise AssertionError(f"{parser.prog} has no choice axis {destination!r}")


def _command_paths(
    parser: argparse.ArgumentParser,
    prefix: tuple[str, ...] = (),
) -> list[str]:
    subparsers = next(
        (
            action
            for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        ),
        None,
    )
    if subparsers is not None:
        return sorted(
            path
            for name, child in subparsers.choices.items()
            for path in _command_paths(child, prefix + (name,))
        )

    operations = next(
        (
            action.choices
            for action in parser._actions
            if action.dest == "operation" and action.choices is not None
        ),
        None,
    )
    if operations is not None:
        return sorted(" ".join(prefix + (operation,)) for operation in operations)
    return [" ".join(prefix)]


def _cli_operation_axes() -> dict[str, dict[str, list[str]]]:
    parsers = {
        "configure.py": _captured_parser(configure.parse_args),
        "create.py": create._build_parser(),
        "define.py": define._parser(),
        "edit.py": edit._build_parser(),
        "lifecycle.py": lifecycle._build_parser(),
        "list_artefacts.py": list_artefacts._build_parser(),
        "machine.py": _captured_parser(machine.parse_args),
        "read.py": read._build_parser(),
        "setup.py": _captured_parser(setup.parse_args),
    }
    return {
        "configure.py": {"command_paths": _command_paths(parsers["configure.py"])},
        "create.py": {"resource": _choice_values(parsers["create.py"], "resource")},
        "define.py": {"command_paths": _command_paths(parsers["define.py"])},
        "edit.py": {
            "operation": _choice_values(parsers["edit.py"], "operation"),
            "resource": _choice_values(parsers["edit.py"], "resource"),
        },
        "lifecycle.py": {"command_paths": _command_paths(parsers["lifecycle.py"])},
        "list_artefacts.py": {
            "resource": _choice_values(parsers["list_artefacts.py"], "resource")
        },
        "machine.py": {"command_paths": _command_paths(parsers["machine.py"])},
        "read.py": {"resource": _choice_values(parsers["read.py"], "resource")},
        "repair.py": {"scope": sorted(REPAIR_SCOPES)},
        "setup.py": {"command_paths": _command_paths(parsers["setup.py"])},
    }


def _function_calls(path: Path, function_name: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    function = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function_name
    )
    return {
        node.func.id
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def _install_upgrade_operations() -> dict[str, list[str]]:
    root = REPO_ROOT
    install_shell = (root / "install.sh").read_text(encoding="utf-8")
    install_powershell = (root / "install.ps1").read_text(encoding="utf-8")
    install_calls = _function_calls(
        root / "src" / "brain-core" / "scripts" / "install.py",
        "install_vault_action",
    )
    upgrade_tree = ast.parse(
        (root / "src" / "brain-core" / "scripts" / "upgrade.py").read_text(
            encoding="utf-8"
        )
    )

    shared_install_modes = []
    if "_scaffold_existing_vault" in install_calls:
        shared_install_modes.append("existing-vault-install")
    if "_scaffold_fresh_vault" in install_calls:
        shared_install_modes.append("fresh-vault-install")

    shell_modes = list(shared_install_modes)
    if '"--uninstall"' in install_shell:
        shell_modes.append("uninstall")
    if "scripts/upgrade.py" in install_shell:
        shell_modes.append("upgrade")

    has_powershell_install_handoff = (
        '"src\\brain-core\\scripts\\install.py"' in install_powershell
    )
    has_upgrade_owner = any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "upgrade"
        for node in upgrade_tree.body
    )
    return {
        "install.ps1": shared_install_modes if has_powershell_install_handoff else [],
        "install.py": shared_install_modes,
        "install.sh": sorted(shell_modes),
        "upgrade.py": ["upgrade"] if has_upgrade_owner else [],
    }


def _literal_subparser_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return sorted(
        call.args[0].value
        for call in ast.walk(tree)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "add_parser"
        and call.args
        and isinstance(call.args[0], ast.Constant)
        and isinstance(call.args[0].value, str)
    )


def _registry_option_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return sorted(
        call.args[0].value.removeprefix("--")
        for call in ast.walk(tree)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "add_argument"
        and call.args
        and isinstance(call.args[0], ast.Constant)
        and isinstance(call.args[0].value, str)
        and call.args[0].value in {
            "--backfill",
            "--clear-default",
            "--get-default",
            "--list",
            "--prune",
            "--register",
            "--resolve",
            "--set-default",
            "--unregister",
        }
    )


def _direct_script_operation_axes() -> dict[str, list[str]]:
    scripts = REPO_ROOT / "src" / "brain-core" / "scripts"
    fix_source = (scripts / "fix_links.py").read_text(encoding="utf-8")
    sync_source = (scripts / "sync_definitions.py").read_text(encoding="utf-8")
    fix_operations = ["check"]
    if '"--fix"' in fix_source:
        fix_operations.append("fix")
    sync_operations = ["sync"]
    if '"--types"' in sync_source:
        sync_operations.append("install")
    if '"--status"' in sync_source:
        sync_operations.append("status")
    workspace_operations = _registry_option_names(scripts / "workspace_registry.py")
    workspace_operations.append("list")
    return {
        "_common/_venv.py": _literal_subparser_names(scripts / "_common" / "_venv.py"),
        "fix_links.py": sorted(fix_operations),
        "sync_definitions.py": sorted(sync_operations),
        "vault_registry.py": _registry_option_names(scripts / "vault_registry.py"),
        "workspace_registry.py": sorted(workspace_operations),
    }


def test_observation_identifies_its_source_contract() -> None:
    """The frozen pre-cutover snapshot declares its schema and source version."""
    fixture = load_current_surface()
    assert fixture["schema"] == "brain.command-surface-observation/1"
    assert fixture["brain_core_version"] == PRE_CUTOVER_BRAIN_CORE_VERSION


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


def test_observed_direct_scripts_match_executable_modules() -> None:
    """Every script with a Python main guard is captured recursively."""
    observed = load_current_surface()["observed_surfaces"]["direct_scripts"]
    assert observed == sorted(set(observed))
    assert _direct_script_entrypoints() == observed


def test_observed_public_python_wrappers_match_server_exports() -> None:
    """Every public brain_* function in the MCP server is captured."""
    observed = load_current_surface()["observed_surfaces"]["public_python_wrappers"]
    assert observed == sorted(set(observed))
    assert _public_python_wrappers() == observed


def test_observed_cli_operation_axes_match_parser_sources() -> None:
    """Every parser branch that selects a semantic operation is captured."""
    observed = load_current_surface()["observed_surfaces"]["cli_operation_axes"]
    assert _cli_operation_axes() == observed


def test_observed_install_upgrade_operations_match_owners() -> None:
    """Install and upgrade modes remain tied to their current semantic owners."""
    observed = load_current_surface()["observed_surfaces"][
        "install_upgrade_operations"
    ]
    assert _install_upgrade_operations() == observed


def test_observed_direct_script_operation_axes_match_sources() -> None:
    """Multiplexed direct scripts expose every semantic operation explicitly."""
    observed = load_current_surface()["observed_surfaces"][
        "direct_script_operation_axes"
    ]
    assert _direct_script_operation_axes() == observed


def test_documented_contract_and_known_consumer_paths_exist() -> None:
    """Every current canonical contract and known consumer path stays explicit."""
    surfaces = load_current_surface()["observed_surfaces"]
    grouped_paths = (
        list(surfaces["documented_contract_sources"].values())
        + list(surfaces["known_current_consumers"].values())
    )
    for paths in grouped_paths:
        for path in paths:
            assert (REPO_ROOT / path).exists(), path


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


def test_observed_surface_inventory_is_complete_before_dispositions() -> None:
    """All current surfaces are observed before target dispositions are assigned."""
    fixture = load_current_surface()
    assert fixture["inventory_status"] == "observed_complete_dispositions_pending"
    assert fixture["pending_surfaces"] == []
