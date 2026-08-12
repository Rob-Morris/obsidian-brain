"""CLI-only human approval for externally gated access leases."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

CLI_DIR = Path(__file__).resolve().parents[2] / "cli"
if str(CLI_DIR) not in sys.path:
    sys.path.insert(0, str(CLI_DIR))

from launcher_catalogue import LAUNCHER_CATALOGUE
from _application.projection import project_identity
from _application.registry import current_application_catalogue
from _command_interface.access import compose_access_controller
from _common import hash_key
from _launcher.access import AccessApproveRequest
from _launcher.context import LauncherContext, ProviderBindings
from _launcher.contracts import Error, ErrorCode, Ok
from _launcher.invocation import LauncherInvocation
from _launcher.owners import LAUNCHER_OWNERS
import config as brain_config


class _Authority:
    def allows(self, **_kwargs):
        return True


class _CallerFilesystem:
    provider_id = "caller_filesystem"
    available = True


class _Receipts:
    def __init__(self):
        self.values = []

    def write(self, receipt):
        self.values.append(receipt)


class _Clock:
    def now(self):
        return datetime.now(timezone.utc)


def _configured_vault(command_vault_clone, *, key="human-secret"):
    root = command_vault_clone.vault_root
    shared = root / ".brain/config.yaml"
    shared.parent.mkdir(parents=True, exist_ok=True)
    shared.write_text(
        "vault:\n"
        "  access:\n"
        "    elevation_policy: external\n"
        "  operators:\n"
        "    - id: agent\n"
        "      profile: contributor\n"
        "      auth:\n"
        "        type: key\n"
        f'        hash: "{hash_key("agent-secret")}"\n'
        "    - id: human-approver\n"
        "      profile: contributor\n"
        "      auth:\n"
        "        type: key\n"
        f'        hash: "{hash_key(key)}"\n',
        encoding="utf-8",
    )
    catalogue = current_application_catalogue()
    config = brain_config.load_config(
        str(root),
        additional_valid_tools=frozenset(
            project_identity(entry.command_id).mcp_tool
            for entry in catalogue.entries
        ),
    )
    commands = frozenset(config["vault"]["profiles"]["contributor"]["allow"])
    controller = compose_access_controller(
        vault_root=root,
        config=config,
        principal="operator:agent",
        ceiling_profile="contributor",
        ceiling_commands=commands,
        clock=_Clock(),
    )
    decision = controller.request(
        ("invocation.read",),
        duration_seconds=60,
        use_count=1,
    )
    return root, controller, decision.pending_request.request_id


def _invoke(root, request_id, *, key="human-secret", dry_run=False):
    context = LauncherContext(
        profile="local-operator",
        authority=_Authority(),
        providers=ProviderBindings((_CallerFilesystem(),)),
        correlation_id="access-approval",
        invocation_id="access-approval",
        receipt_writer=_Receipts(),
        clock=_Clock(),
        caller_dir=root.parent.resolve(),
        home_dir=root.parent.resolve(),
        cli_version="2.1.0",
        cli_binary=(root.parent / "brain").resolve(),
        launcher_python=Path(sys.executable).resolve(),
        current_vault=root,
        operator_key=key,
        dry_run=dry_run,
    )
    return LauncherInvocation(context, LAUNCHER_CATALOGUE, LAUNCHER_OWNERS).invoke(
        AccessApproveRequest(request_id)
    )


def test_registered_operator_can_approve_exact_pending_request(command_vault_clone):
    root, controller, request_id = _configured_vault(command_vault_clone)

    result = _invoke(root, request_id)

    assert isinstance(result, Ok)
    assert result.result.status == "approved"
    assert result.result.commands == ("invocation.read",)
    assert controller.allows("invocation.read") is True


def test_approval_dry_run_and_wrong_key_do_not_issue_a_lease(command_vault_clone):
    root, controller, request_id = _configured_vault(command_vault_clone)

    planned = _invoke(root, request_id, dry_run=True)
    denied = _invoke(root, request_id, key="wrong-secret")

    assert isinstance(planned, Ok)
    assert planned.result.status == "planned"
    assert isinstance(denied, Error)
    assert denied.error.code is ErrorCode.AUTHORITY_DENIED
    assert controller.allows("invocation.read") is False


def test_requesting_principal_cannot_approve_its_own_request(
    command_vault_clone,
):
    root, controller, request_id = _configured_vault(command_vault_clone)

    denied = _invoke(root, request_id, key="agent-secret")

    assert isinstance(denied, Error)
    assert denied.error.code is ErrorCode.AUTHORITY_DENIED
    assert controller.allows("invocation.read") is False
