"""The Brain repair table is the one source of repair commands (DD-082, D14)."""

from __future__ import annotations

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
from _repair_common import AUTOMATIC_SCOPES, Disposition, Owner, RECOVERY_SCOPES, REPAIR_SCOPES


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


def test_core_doctor_register_guidance_matches_the_launcher_table():
    """Core cannot import the launcher, so its hand-built guidance is pinned to the table's rendering."""
    import json
    import shlex

    import doctor_machine
    from _launcher.registry import BrainRegisterRequest

    family = machine_maintenance.MACHINE_FAMILIES["brain_unregistered"]
    guidance = doctor_machine.register_guidance("/x y")
    assert guidance == machine_maintenance.launcher_guidance(family, subject={"path": "/x y"})
    argv = shlex.split(guidance)
    decoded = resolve_request(BrainRegisterRequest, json.loads(argv[argv.index("--request-json") + 1]))
    assert decoded.vault_root == Path("/x y") and decoded.brain_id is None


def test_first_slice_dispositions_and_recovery_scopes():
    assert AUTOMATIC_SCOPES == ("router", "lexical", "temporaries")
    assert repair_common.NEVER_HELD == {"router"}
    assert {scope for scope, family in REPAIR_SCOPES.items() if family.clears_embeddings} == {"router", "lexical"}
    assert set(RECOVERY_SCOPES) == {
        "runtime", "mcp", "router", "lexical", "registry",
        "frontmatter", "ownership", "semantic", "empty_folders",
    }
    assert {scope for scope, family in REPAIR_SCOPES.items() if family.owner is Owner.MACHINE} == {"runtime", "mcp"}
    assert {scope for scope, family in REPAIR_SCOPES.items() if family.exceptional} == {"registry", "semantic"}
    for scope in AUTOMATIC_SCOPES:
        assert _brain_entry(REPAIR_SCOPES[scope]).initial_class is InitialAuthorisationClass.OBSERVATION, scope
    assert repair_common.JUDGEMENT_CODES == {
        ("workspace_contract", "workspace_reference_missing"),
        ("workspace_contract", "workspace_reference_archived"),
    }


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

        argv = repair_common.build_catalogue_argv(tmp_path, REPAIR_SCOPES["registry"])

        root = str(tmp_path.resolve())
        assert argv == ["brain", "--vault", root, "session", "run", "--",
                        "brain", "--vault", root, "workspace", "repair-registry"]

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
