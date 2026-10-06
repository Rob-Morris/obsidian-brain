"""The Brain repair table is the one source of repair commands (DD-082, D14)."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
import sys

import pytest

CLI_DIR = Path(__file__).resolve().parents[2] / "cli"
if str(CLI_DIR) not in sys.path:
    sys.path.insert(0, str(CLI_DIR))

import _repair_common as repair_common
import repair
from _application.registry import current_application_catalogue, current_request_resolver
from _application.types import DependencyTier, InitialAuthorisationClass, Projection
from _launcher import machine_maintenance
from _launcher.owners import LAUNCHER_OWNERS
from _launcher.projection import resolve_request
from _repair_common import AUTOMATIC_SCOPES, Disposition, Identity, Owner, RECOVERY_SCOPES, REPAIR_SCOPES


def _brain_entry(family):
    request = current_request_resolver().resolve(family.command_id, dict(family.request))
    return current_application_catalogue().resolve(request)


@pytest.mark.parametrize("scope", sorted(REPAIR_SCOPES))
def test_every_family_request_decodes_through_its_owner_catalogue(scope):
    family = REPAIR_SCOPES[scope]
    if family.owner is Owner.BRAIN:
        entry = _brain_entry(family)
        assert Projection.SCRIPT in entry.eligible_projections, scope
        assert family.exceptional is (entry.initial_class is InitialAuthorisationClass.EXCEPTIONAL), scope
        if family.disposition is Disposition.AUTOMATIC:
            # The pass is declared portable, so every family it invokes must run there.
            assert DependencyTier.PORTABLE.supports(entry.dependency_tier), scope
    else:
        owner = next(item for item in LAUNCHER_OWNERS.entries if item.command_id == family.command_id)
        assert type(resolve_request(owner.request_type, dict(family.request))) is owner.request_type


@pytest.mark.parametrize("kind", sorted(machine_maintenance.MACHINE_FAMILIES))
def test_every_machine_family_resolves_through_the_launcher_catalogue(kind):
    family = machine_maintenance.MACHINE_FAMILIES[kind]
    assert family.scope == kind and family.owner is Owner.MACHINE
    assert family.disposition is Disposition.JUDGEMENT, "no machine family passes the automatic admission test"
    subject = {"path": "/x y"} if kind == "brain_unregistered" else {}
    request = machine_maintenance.launcher_request(family, subject)
    assert request.COMMAND_ID == family.command_id
    assert machine_maintenance.launcher_guidance(family, subject=subject).startswith("brain ")
    verb = f"{family.noun} {family.verb}" if family.noun != "brain" else family.verb
    expected = f"brain --vault '/x y' {verb}"
    if kind == "brain_unregistered":
        assert request.vault_root == Path("/x y"), "the request is derived from the finding subject"
        expected += """ --request-json '{"vault_root":"/x y"}'"""
    assert machine_maintenance.launcher_guidance(family, vault_root="/x y", subject=subject) == expected


def test_core_register_guidance_matches_the_launcher_table():
    """Core cannot import the launcher, so its hand-built guidance is pinned to the table's rendering."""
    import json
    import shlex

    import vault_registry
    from _launcher.registry import BrainRegisterRequest

    family = machine_maintenance.MACHINE_FAMILIES["brain_unregistered"]
    guidance = vault_registry.register_guidance("/x y")
    assert guidance == machine_maintenance.launcher_guidance(family, subject={"path": "/x y"})
    argv = shlex.split(guidance)
    decoded = resolve_request(BrainRegisterRequest, json.loads(argv[argv.index("--request-json") + 1]))
    assert decoded.vault_root == Path("/x y") and decoded.brain_id is None


def test_core_upgrade_guidance_decodes_as_the_launcher_upgrade():
    """Core names the remedy for an unreadable runtime contract; the CLI's own parser must read it as brain.upgrade."""
    import shlex

    from launcher_catalogue import LAUNCHER_CATALOGUE
    from _launcher.lifecycle import BrainUpgradeRequest
    from _local_cli.main import _parse_common
    from _machine.topology import upgrade_guidance

    binary, *argv = shlex.split(upgrade_guidance("/x y"))
    common, command_argv = _parse_common(argv)
    entry = next(item for item in LAUNCHER_CATALOGUE.entries if item.entry_point == (binary, *command_argv))
    assert entry.command_id == BrainUpgradeRequest.COMMAND_ID
    assert common.vault == "/x y" and common.request_json is None


def _core_guidance(tmp_path, spelling):
    """Raise the Core error that carries one launcher spelling and return its message."""
    import vault_registry
    from _bootstrap.workspace_binding import WorkspaceBindingError, plan_workspace_binding, resolve_brain_target

    start = tmp_path / "start"
    start.mkdir()
    if spelling.startswith("workspace setup"):
        manifest = tmp_path / "ws" / ".brain" / "local" / "workspace.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("brain: other\nslug: ws\n")
        if spelling == "workspace setup":
            call = lambda: plan_workspace_binding(tmp_path / "ws", brain="brain")
        else:
            call = lambda: plan_workspace_binding(tmp_path / "ws", brain="other", slug="different")
    else:
        if spelling == "clear-default":
            default = Path(vault_registry.default_path())
            default.parent.mkdir(parents=True, exist_ok=True)
            default.write_text("ghost\n")
        call = lambda: resolve_brain_target(workspace_env=None, vault_root_env=None, start_dir=start)
    with pytest.raises(WorkspaceBindingError) as raised:
        call()
    return str(raised.value)


@pytest.mark.parametrize("spelling", ["clear-default", "set-default", "workspace setup", "workspace setup slug"])
def test_core_resolution_guidance_decodes_against_the_command_tables(tmp_path, spelling):
    """Core's hand-written launcher commands must parse as the commands they name."""
    import json
    import re
    import shlex

    from _launcher.registry import BrainClearDefaultRequest, BrainSetDefaultRequest
    from launcher_catalogue import LAUNCHER_CATALOGUE

    message = _core_guidance(tmp_path, spelling)
    command_name = spelling.removesuffix(" slug")
    commands = [shlex.split(text) for text in re.findall(r"[(`](brain [^)`]*)[)`]", message)]
    argv = next(command for command in commands if " ".join(command).startswith(f"brain {command_name}"))
    split = argv.index("--request-json") if "--request-json" in argv else len(argv)
    payload = json.loads(argv[split + 1]) if split < len(argv) else {}
    if command_name == "workspace setup":
        assert argv[:split] == ["brain", "workspace", "setup"]
        assert current_request_resolver().resolve("workspace.setup", payload).force is True
        return
    entry = next(item for item in LAUNCHER_CATALOGUE.entries if list(item.entry_point) == argv[:split])
    request_type = {"brain.set-default": BrainSetDefaultRequest, "brain.clear-default": BrainClearDefaultRequest}[entry.command_id]
    assert entry.command_id == f"brain.{spelling}"
    if spelling == "set-default":
        assert payload == {"brain_id": "<id>"}, "the placeholder is the only field"
        payload = {"brain_id": "example"}
    assert isinstance(resolve_request(request_type, payload), request_type)


def test_dispositions_and_recovery_scopes():
    assert AUTOMATIC_SCOPES == ("router", "lexical", "temporaries", "registry")
    assert repair_common.NEVER_HELD == {"router"}
    assert {scope for scope, family in REPAIR_SCOPES.items() if family.clears_embeddings} == {"router", "lexical"}
    assert set(RECOVERY_SCOPES) == {
        "runtime", "mcp", "router", "lexical", "registry",
        "frontmatter", "ownership", "semantic", "empty_folders",
    }
    assert {scope for scope, family in REPAIR_SCOPES.items() if family.owner is Owner.MACHINE} == {"runtime", "mcp"}
    assert {scope for scope, family in REPAIR_SCOPES.items() if family.exceptional} == {"semantic"}
    for scope in AUTOMATIC_SCOPES:
        assert _brain_entry(REPAIR_SCOPES[scope]).initial_class is InitialAuthorisationClass.OBSERVATION, scope
    # Rows that are not the evidence default; the producer scan below keeps the key set honest (DD-086).
    assert {pair: identity for pair, identity in repair_common.JUDGEMENT_FINDINGS.items()
            if identity is not Identity.EVIDENCE} == {
        ("root_files", None): Identity.SUBJECT,
        ("workspace_contract", "workspace_scan_unreadable"): Identity.KIND_ONLY,
    }
    registry = REPAIR_SCOPES["registry"]
    assert (registry.holdable, registry.clears_embeddings, registry.recovery) == (True, False, True)


SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "src" / "brain-core" / "scripts"

# Functions that build a finding-shaped dict without being a ``run_checks`` producer, with the reason.
NOT_FINDING_SITES = {
    ("check.py", "main"): "the CLI's bootstrap-failure envelope, never a run_checks finding",
    ("_lifecycle/workspace_checks.py", "report"): "the emission owner; it refuses any code outside ERROR_CODES and INFO_CODES",
}


def _literal(node):
    return node.value if isinstance(node, ast.Constant) else None


def _constants_assigned(scope) -> dict[str, set[str]]:
    """String constants each name is bound to within ``scope`` (plain or conditional); a non-constant binding fails.

    Resolution is per function, so a constant bound to a name in one function
    never vouches for the same name in another.
    """
    assigned: dict[str, set[str]] = {}
    for node in ast.walk(scope):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            values = (node.value.body, node.value.orelse) if isinstance(node.value, ast.IfExp) else (node.value,)
            constants = {value.value for value in values if isinstance(value, ast.Constant) and isinstance(value.value, str)}
            name = node.targets[0].id
            if len(constants) < len(values):
                assigned[name] = set()  # bound to something that is not a literal: never resolvable
            elif name not in assigned or assigned[name]:
                assigned.setdefault(name, set()).update(constants)
    return assigned


def _function_scopes(tree):
    """Each function definition in ``tree`` paired with the constants its names resolve to."""
    return [(function, _constants_assigned(function)) for function in ast.walk(tree)
            if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _enclosing_functions(tree) -> dict[int, str]:
    """Innermost enclosing function name per node id (inner definitions are walked after outer ones)."""
    owners: dict[int, str] = {}
    for function in ast.walk(tree):
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(function):
                owners[id(node)] = function.name
    return owners


def _finding_sites() -> dict[tuple[str, str | None], set[str]]:
    """Every (check, code) a finding-shaped dict literal in the scripts tree can carry, with its severities.

    Fails closed: a finding dict whose ``severity`` is not a literal, an
    error whose ``check`` or ``code`` is not a literal (or a name bound only
    to literals), a ``dict(...)`` call or a subscript assignment that sets a
    severity, all fail the scan unless the enclosing function is listed in
    ``NOT_FINDING_SITES`` with a reason. A dict handed to
    ``attach_repair_guidance``, directly or through a name, is a family
    finding and carries no judgement identity. Scanning the whole tree,
    rather than the modules ``run_checks`` composes today, means a new
    collector is seen before it is wired in.
    """
    sites: dict[tuple[str, str | None], set[str]] = {}
    for path in sorted(SCRIPTS_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = str(path.relative_to(SCRIPTS_ROOT))
        owners = _enclosing_functions(tree)
        resolvers: dict[int, dict[str, set[str]]] = {}
        for function, assigned in _function_scopes(tree):
            for node in ast.walk(function):
                resolvers[id(node)] = assigned
        family_names = {node.args[0].id for node in ast.walk(tree)
                        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "attach_repair_guidance"
                        and node.args and isinstance(node.args[0], ast.Name)}
        family_dicts = {id(node.args[0]) for node in ast.walk(tree)
                        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "attach_repair_guidance"
                        and node.args}
        family_dicts |= {id(node.value) for node in ast.walk(tree)
                         if isinstance(node, ast.Assign) and len(node.targets) == 1
                         and getattr(node.targets[0], "id", None) in family_names}
        for node in ast.walk(tree):
            where = f"{rel}:{getattr(node, 'lineno', '?')} in {owners.get(id(node))}"
            if (rel, owners.get(id(node))) in NOT_FINDING_SITES:
                continue
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "dict":
                assert not any(item.arg == "severity" for item in node.keywords), f"finding built with dict(): {where}"
            if isinstance(node, ast.Assign) and any(
                    isinstance(target, ast.Subscript) and _literal(target.slice) == "severity" for target in node.targets):
                raise AssertionError(f"severity set by subscript: {where}")
            if not isinstance(node, ast.Dict) or id(node) in family_dicts:
                continue
            fields = {_literal(key): value for key, value in zip(node.keys, node.values) if key is not None}
            if "check" not in fields or "severity" not in fields:
                continue
            severity = _literal(fields["severity"])
            assert severity in {"error", "warning", "info"}, f"non-literal severity: {where}"
            check = _literal(fields["check"])
            code_node = fields.get("code")
            if code_node is None:
                codes = {None}
            elif isinstance(code_node, ast.Constant):
                codes = {code_node.value}
            elif isinstance(code_node, ast.Name):
                codes = resolvers.get(id(node), {}).get(code_node.id, set())
            else:
                codes = set()
            if severity == "error":
                assert check is not None and codes, f"error with a non-literal check or code: {where}"
            elif check is None:
                continue  # a dynamic warning or info check name can match no table row
            for code in codes:
                sites.setdefault((check, code), set()).add(severity)
    return sites


def _workspace_report_sites() -> dict[str, set[str]]:
    """Every code passed to ``workspace_checks.report`` with its severity; a non-literal code or severity fails."""
    from _lifecycle import workspace_checks

    tree = ast.parse(inspect.getsource(workspace_checks))
    resolvers: dict[int, dict[str, set[str]]] = {}
    for function, assigned in _function_scopes(tree):
        for node in ast.walk(function):
            resolvers[id(node)] = assigned
    binding_codes = {workspace_checks.binding_code(state) for state in workspace_checks._REPORTED_BINDING_STATES}
    sites: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or getattr(node.func, "id", None) != "report":
            continue
        where = f"workspace_checks.py:{node.lineno}"
        assert len(node.args) <= 4, f"severity must be passed by keyword: {where}"
        severity = next((item.value for item in node.keywords if item.arg == "severity"), ast.Constant("error"))
        assert _literal(severity) in {"error", "info", "warning"}, f"non-literal severity: {where}"
        target = node.args[0]
        if isinstance(target, ast.Call) and getattr(target.func, "id", None) == "binding_code":
            codes = binding_codes
        elif isinstance(target, ast.Name):
            codes = resolvers.get(id(node), {}).get(target.id) or set()
        else:
            codes = {_literal(target)}
        assert codes and None not in codes, f"non-literal code: {where}"
        for code in codes:
            sites.setdefault(code, set()).add(_literal(severity))
    return sites


def test_every_family_less_error_a_producer_can_emit_is_classified():
    """A new error code needs a JUDGEMENT_FINDINGS row saying what identifies it (DD-086)."""
    from _lifecycle import workspace_checks

    report_sites = _workspace_report_sites()
    assert {code for code, severities in report_sites.items() if "error" in severities} == workspace_checks.ERROR_CODES
    assert {code for code, severities in report_sites.items() if "info" in severities} == workspace_checks.INFO_CODES
    sites = _finding_sites()
    errors = {pair for pair, severities in sites.items() if "error" in severities}
    errors |= {("workspace_contract", code) for code in workspace_checks.ERROR_CODES}
    assert {("root_files", None), ("living_key_fields", None)} <= errors, "the scan reads the check functions"

    table = set(repair_common.JUDGEMENT_FINDINGS)
    assert errors <= table, f"unclassified family-less error codes: {sorted(errors - table, key=str)}"
    emitted = set(sites) | {("workspace_contract", code) for code in report_sites}
    assert table <= emitted, f"table rows no producer emits: {sorted(table - emitted, key=str)}"


def test_repair_py_offers_only_recovery_scopes(capsys):
    assert repair.parse_args(["router", "--dry-run"]).scope == "router"
    with pytest.raises(SystemExit) as exc:
        repair.parse_args(["temporaries"])
    assert exc.value.code == 2
    assert "temporaries" not in capsys.readouterr().err.split("choose from", 1)[1]


def test_family_rejects_exceptional_automatic_rows():
    with pytest.raises(ValueError, match="exceptional"):
        repair_common.RepairFamily("x", "x.y", {}, Disposition.AUTOMATIC, Owner.BRAIN, False, "d", exceptional=True)


class TestGuidanceForms:
    """Three guidance forms: catalogue, exceptional job, and machine-owned."""

    def test_brain_family_names_the_launcher_when_one_is_on_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr(repair_common, "find_launcher_binary", lambda: "/opt/bin/brain")

        command = repair_common.build_catalogue_command(tmp_path, REPAIR_SCOPES["ownership"])

        assert command == (
            f"/opt/bin/brain --vault {tmp_path.resolve()} artefact repair "
            "--request-json '{\"scope\":\"ownership\"}'"
        )

    def test_brain_family_falls_back_to_the_direct_script(self, tmp_path, monkeypatch):
        monkeypatch.setattr(repair_common, "find_launcher_binary", lambda: None)
        monkeypatch.setattr(repair_common, "find_launcher_python", lambda: "/opt/python3.13")

        argv = repair_common.build_catalogue_argv(tmp_path, REPAIR_SCOPES["router"])

        assert argv == [
            "/opt/python3.13",
            str(tmp_path.resolve() / ".brain-core/scripts/command.py"),
            "runtime", "refresh-router", "--vault", str(tmp_path.resolve()),
        ]

    def test_exceptional_family_names_a_session_run_job(self, tmp_path, monkeypatch):
        monkeypatch.setattr(repair_common, "find_launcher_binary", lambda: None)

        argv = repair_common.build_catalogue_argv(tmp_path, REPAIR_SCOPES["semantic"])

        root = str(tmp_path.resolve())
        assert argv == ["brain", "--vault", root, "session", "run", "--",
                        "brain", "--vault", root, "retrieval", "repair-semantic"]

    def test_ordinary_registry_family_names_its_command_directly(self, tmp_path, monkeypatch):
        monkeypatch.setattr(repair_common, "find_launcher_binary", lambda: "/opt/bin/brain")

        argv = repair_common.build_catalogue_argv(tmp_path, REPAIR_SCOPES["registry"])

        assert argv == ["/opt/bin/brain", "--vault", str(tmp_path.resolve()), "workspace", "repair-registry"]

    def test_machine_family_prefers_the_launcher_and_falls_back_to_repair_py(self, tmp_path, monkeypatch):
        monkeypatch.setattr(repair_common, "find_launcher_binary", lambda: "/opt/bin/brain")
        with_launcher = repair_common.build_catalogue_argv(tmp_path, REPAIR_SCOPES["mcp"])
        monkeypatch.setattr(repair_common, "find_launcher_binary", lambda: None)
        monkeypatch.setattr(repair_common, "find_launcher_python", lambda: "/opt/python3.13")
        without = repair_common.build_catalogue_argv(tmp_path, REPAIR_SCOPES["mcp"])

        assert with_launcher == ["/opt/bin/brain", "--vault", str(tmp_path.resolve()), "mcp", "repair"]
        assert without == ["/opt/python3.13", str(tmp_path.resolve() / ".brain-core/scripts/repair.py"),
                           "mcp", "--vault", str(tmp_path.resolve())]

    def test_attached_guidance_carries_command_id_and_catalogue_command(self, tmp_path, monkeypatch):
        monkeypatch.setattr(repair_common, "find_launcher_binary", lambda: "/opt/bin/brain")

        finding = repair_common.attach_repair_guidance({"check": "x"}, tmp_path, "lexical")

        assert finding["repair"]["command_id"] == "retrieval.refresh-lexical"
        assert finding["repair"]["command"].endswith("retrieval refresh-lexical")
        assert finding["fix"] == f"Run `{finding['repair']['command']}`"


def test_family_for_finding_is_the_one_lookup_and_fails_loudly_on_a_broken_producer():
    from _repair_common import family_for_finding

    assert family_for_finding({"check": "workspace_registry"}) is None
    assert family_for_finding({"check": "router", "repair": {"scope": "router"}}) is REPAIR_SCOPES["router"]
    with pytest.raises(KeyError):
        family_for_finding({"check": "x", "repair": {"scope": "no-such-scope"}})
    with pytest.raises(ValueError):
        family_for_finding({"check": "x", "repair": None})
