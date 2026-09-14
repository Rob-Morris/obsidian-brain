"""Explicit instance authorisation for command-owner tests."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import uuid

from _application.access_session import AuthorisationSession
from _application.access_contracts import AccessConfiguration
from _application.application import CommandApplication
from _application.consent import ConsentIdentity, ConsentPolicy, ConsentService
from _application.context import CapabilitySnapshot, InvocationContext, ProviderBindings, SelectedBrain
from _application.preparation import content_digest
from _application.receipts import OwnedReceiptLookup, ReceiptIntentConflict
from _application.registry import current_application_catalogue, current_request_resolver
from _application.types import DependencyTier, InitialAuthorisationClass, SnapshotFreshness
from _bootstrap.consent_state import MemoryStateStore
from _command_interface.context import SynchronousSessionMirror

NOW = datetime.fromisoformat("2026-08-09T16:00:00+10:00")


class _Receipts:
    def __init__(self):
        self.intents, self.outcomes = {}, {}

    def begin(self, intent):
        key = intent.reference.invocation_id
        if key in self.intents and self.intents[key] != intent:
            raise ReceiptIntentConflict("immutable admission intent changed")
        if key in self.intents:
            return False
        self.intents[key] = intent
        return True

    def finalise(self, outcome):
        key = outcome.reference.invocation_id
        if key not in self.intents:
            raise ValueError("completion requires admission intent")
        if key in self.outcomes and self.outcomes[key] != outcome:
            raise ValueError("immutable completion changed")
        self.outcomes[key] = outcome

    def read(self, reference):
        key = reference.invocation_id
        return OwnedReceiptLookup(reference, self.intents.get(key), self.outcomes.get(key))


class _Pins:
    def __init__(self):
        self.values = {}

    def retain_content(self, key, content):
        if key in self.values and self.values[key] != content:
            raise ValueError("prepared content changed")
        self.values[key] = content
        digest = content_digest(content)
        return {"pin": digest[7:], "sha256": digest, "bytes": len(content)}

    def read_pinned(self, key):
        return self.values.get(key)

    def discard(self):
        self.values.clear()


class _Clock:
    def now(self):
        return NOW


def context_for(vault_root, *, dependency_tier=DependencyTier.PORTABLE, workspace_dir=None,
                capabilities=(), providers=(), authority=None, dry_run=False,
                derived_snapshots=None, session_mirror=None, allowed_commands=None,
                initial_commands=None, profile="reader", catalogue=None, resolver=None,
                context_available=True, context_kind="mcp-instance", request_policy="allowed",
                invocation_id="inv-read", receipts=None):
    catalogue = catalogue or current_application_catalogue()
    versions = {entry.command_id: entry.command_version for entry in catalogue.entries}
    allowed = frozenset(versions if allowed_commands is None else allowed_commands)
    initial = allowed if initial_commands is None else frozenset(initial_commands)
    if authority is not None:
        allowed = frozenset(command for command in allowed if authority.ceiling_allows(command))
        initial = frozenset(entry.command_id for entry in catalogue.entries if entry.command_id in initial
            and authority.allows(command_id=entry.command_id, required=entry.authority, effect=entry.effect_class))
    controls = frozenset(entry.command_id for entry in catalogue.entries
                         if entry.initial_class is InitialAuthorisationClass.CONTROL)
    service = ConsentService(MemoryStateStore(), ConsentIdentity("command-vault", str(vault_root.resolve()),
        "default", str(uuid.uuid4()), context_kind, "test-permissions", "test-interface"),
        ConsentPolicy(profile, allowed, initial & allowed, controls=controls, request_policy=request_policy), versions)
    pins = {}
    def content_for(namespace):
        return pins.setdefault(namespace, _Pins())
    session = AuthorisationSession(service, catalogue, resolver or current_request_resolver(),
        receipts or _Receipts(), content_for,
        configuration_provider=lambda: AccessConfiguration(service.policy.initial_mode,
            tuple(sorted(service.policy.initial)), "test", (), "test",
            service.policy.request_policy, "test", (), "sha256:test-config",
            effective_request_policy=service.policy.request_policy),
        context_available=context_available,
        source="host-request" if context_kind == "mcp-instance" else "cli-request")
    context = InvocationContext(selected_brain=SelectedBrain("command-vault", vault_root.resolve()),
        profile=profile, dependency_tier=dependency_tier,
        capabilities=CapabilitySnapshot("snapshot", SnapshotFreshness.FRESH, NOW, tuple(capabilities)),
        providers=ProviderBindings(tuple(providers)), correlation_id="corr-read", invocation_id=invocation_id,
        receipt_reader=session.receipts, clock=_Clock(), authorisation=session,
        dry_run=dry_run, workspace_dir=workspace_dir, derived_snapshots=derived_snapshots,
        session_mirror=session_mirror if session_mirror is not None else SynchronousSessionMirror(vault_root.resolve()))
    return replace(context, access=session.bind(context))


class _TestApplication(CommandApplication):
    def __init__(self, context, catalogue):
        super().__init__(context, catalogue)
        self._calls = 0
        self._first_id = context.invocation_id

    def invoke(self, request):
        self._calls += 1
        invocation_id = self._first_id if self._calls == 1 else f"{self._first_id}-{self._calls}"
        context = replace(self._context, invocation_id=invocation_id)
        self._context = replace(context, access=context.authorisation.bind(context))
        return super().invoke(request)


def application_for(vault_root, **kwargs):
    context = context_for(vault_root, **kwargs)
    return _TestApplication(context, context.authorisation.catalogue)
