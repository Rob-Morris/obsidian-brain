"""Reader-default active grants and exact elevation lease contracts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from _application.adapter import ApplicationAdapter
from _application.receipts import MemoryReceiptStore
from _application.registry import current_application_catalogue, current_request_resolver
from _application.results import ErrorCode
from _application.types import Availability, DependencyTier, SnapshotFreshness
from _command_interface.access import (
    AccessPolicy,
    FileAccessController,
)
from _command_interface.context import compose_local_context
from _application.access_contracts import AccessRequestState, ElevationPolicy


class _Clock:
    def __init__(self):
        self.value = datetime(2026, 8, 12, 8, 0, tzinfo=timezone.utc)

    def now(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


def _vault(tmp_path):
    root = (tmp_path / "Brain").resolve()
    (root / ".brain-core").mkdir(parents=True)
    (root / ".brain-core/VERSION").write_text("0.57.0\n", encoding="utf-8")
    return root


def _controller(tmp_path, *, policy=ElevationPolicy.AUTOMATIC, clock=None):
    clock = clock or _Clock()
    root = _vault(tmp_path)
    controller = FileAccessController(
        vault_root=root,
        principal="operator:test",
        ceiling_profile="contributor",
        ceiling_commands=frozenset(
            {
                "access.reduce",
                "access.request",
                "access.status",
                "command.describe",
                "command.list",
                "invocation.read",
            }
        ),
        initial_profile="reader",
        initial_commands=frozenset(
            {
                "access.reduce",
                "access.request",
                "access.status",
                "command.describe",
                "command.list",
            }
        ),
        policy=AccessPolicy("reader", policy, 60, 300, 120, 5),
        clock=clock,
    )
    return root, controller, clock


def _adapter_context(
    root,
    controller,
    clock,
    invocation_id="access-test",
    receipts=None,
):
    receipts = receipts or MemoryReceiptStore(clock)
    return compose_local_context(
        vault_root=root,
        brain_id="test-brain",
        profile="contributor",
        allowed_tools=controller.ceiling_commands,
        access=controller,
        dependency_tier=DependencyTier.PORTABLE,
        provider_ids=(),
        capability_states=(),
        snapshot_token="access-snapshot",
        snapshot_freshness=SnapshotFreshness.FRESH,
        snapshot_observed_at=clock.now(),
        correlation_id=invocation_id,
        invocation_id=invocation_id,
        receipt_store=receipts,
        clock=clock,
    )


def test_access_status_is_read_only_on_a_fresh_brain(tmp_path):
    root, controller, _clock = _controller(tmp_path)

    snapshot = controller.status()

    assert snapshot.active_commands == (
        "access.reduce",
        "access.request",
        "access.status",
        "command.describe",
        "command.list",
    )
    assert snapshot.inactive_commands == ("invocation.read",)
    assert not (root / ".brain/local").exists()


def test_access_state_refuses_symlinked_private_directory(tmp_path):
    root, controller, _clock = _controller(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / ".brain").mkdir()
    (root / ".brain/local").symlink_to(outside, target_is_directory=True)

    try:
        controller.request(
            ("invocation.read",),
            duration_seconds=30,
            use_count=None,
        )
    except OSError as exc:
        assert "symlinked access-state directory" in str(exc)
    else:
        raise AssertionError("access state followed a symlinked private directory")

    assert list(outside.iterdir()) == []


def test_automatic_exact_lease_activates_consumes_expires_and_reduces(tmp_path):
    root, controller, clock = _controller(tmp_path)

    decision = controller.request(
        ("invocation.read",),
        duration_seconds=30,
        use_count=2,
    )

    assert decision.state is AccessRequestState.GRANTED
    assert decision.lease is not None
    assert controller.consume("invocation.read") is True
    assert controller.status().leases[0].remaining_uses == 1
    assert controller.reduce(
        reset=False,
        lease_ids=(),
        commands=("invocation.read",),
    ).changed is True
    assert controller.allows("invocation.read") is False

    controller.request(("invocation.read",), duration_seconds=1, use_count=None)
    clock.advance(2)
    assert controller.allows("invocation.read") is False


def test_later_mutation_compacts_expired_state_without_status_writes(tmp_path):
    root, controller, clock = _controller(tmp_path)
    controller.request(
        ("invocation.read",),
        duration_seconds=1,
        use_count=None,
    )
    clock.advance(2)

    controller.status()
    before = json.loads(
        (root / ".brain/local/access-state.json").read_text(encoding="utf-8")
    )
    controller.request(
        ("invocation.read",),
        duration_seconds=30,
        use_count=None,
    )
    after = json.loads(
        (root / ".brain/local/access-state.json").read_text(encoding="utf-8")
    )

    assert len(before["principals"]["operator:test"]["leases"]) == 1
    assert len(after["principals"]["operator:test"]["leases"]) == 1


def test_external_policy_requires_trusted_approval_seam(tmp_path):
    _root, controller, _clock = _controller(
        tmp_path,
        policy=ElevationPolicy.EXTERNAL,
    )

    decision = controller.request(
        ("invocation.read",),
        duration_seconds=60,
        use_count=1,
    )

    assert decision.state is AccessRequestState.APPROVAL_REQUIRED
    assert decision.pending_request is not None
    assert controller.allows("invocation.read") is False

    lease = controller.approve(
        decision.pending_request.request_id,
        approver="local-user:terminal",
    )

    assert lease.policy is ElevationPolicy.EXTERNAL
    assert controller.allows("invocation.read") is True


def test_ceiling_denial_never_creates_a_pending_request_or_lease(tmp_path):
    root, controller, _clock = _controller(tmp_path)

    decision = controller.request(
        ("artefact.delete",),
        duration_seconds=None,
        use_count=None,
    )

    assert decision.state is AccessRequestState.DENIED
    assert decision.denied_commands == ("artefact.delete",)
    assert not (root / ".brain/local").exists()


def test_application_denies_before_resolution_then_honours_one_use_lease(tmp_path):
    root, controller, clock = _controller(tmp_path)
    receipts = MemoryReceiptStore(clock)
    adapter = ApplicationAdapter(
        current_application_catalogue(),
        current_request_resolver(),
    )

    denied = adapter.invoke(
        _adapter_context(root, controller, clock, "denied", receipts),
        "invocation.read",
        {"malformed": "request must not be resolved before authority"},
    )

    assert denied.result.error.code is ErrorCode.AUTHORITY_DENIED
    assert denied.result.error.details.boundary == "active_grant"
    assert denied.result.error.details.requestable is True
    assert denied.result.error.next_action.command_id == "access.request"

    granted = adapter.invoke(
        _adapter_context(root, controller, clock, "granted", receipts),
        "access.request",
        {"commands": ["invocation.read"], "use_count": 1},
    )
    invoked = adapter.invoke(
        _adapter_context(root, controller, clock, "invoked", receipts),
        "invocation.read",
        {"invocation_id": "absent"},
    )
    expired = adapter.invoke(
        _adapter_context(root, controller, clock, "expired", receipts),
        "invocation.read",
        {"invocation_id": "absent"},
    )

    assert granted.result.result.state is AccessRequestState.GRANTED
    assert invoked.exit_code == 0
    assert expired.result.error.code is ErrorCode.AUTHORITY_DENIED


def test_foundation_discovery_omits_commands_above_the_ceiling(tmp_path):
    root, controller, clock = _controller(tmp_path)
    adapter = ApplicationAdapter(
        current_application_catalogue(),
        current_request_resolver(),
    )

    listed = adapter.invoke(
        _adapter_context(root, controller, clock, "listed"),
        "command.list",
        {},
    )
    hidden = adapter.invoke(
        _adapter_context(root, controller, clock, "described"),
        "command.describe",
        {"target_command_id": "artefact.delete"},
    )

    assert set(listed.result.result.command_ids) == set(controller.ceiling_commands)
    assert hidden.result.error.code is ErrorCode.NOT_FOUND
