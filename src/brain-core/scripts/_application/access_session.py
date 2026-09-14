"""Instance authorisation composition and the invocation-bound control facade."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from . import access_contracts as dto
from .authorisation import InvocationAuthorisation, PreparationCoordinator, PreparedContent
from .catalogue import ApplicationCatalogue
from .consent import ConsentError, ConsentScope, ConsentService
from .preparation import canonical_json, content_digest
from .receipts import OwnedReceiptPort
from .resolver import RequestResolver, RequestResolutionError
from .results import CommandArgument, CommandNextAction, Error
from .types import InitialAuthorisationClass, Projection


@dataclass(frozen=True, slots=True)
class AuthorisationSession:
    service: ConsentService
    catalogue: ApplicationCatalogue
    resolver: RequestResolver
    receipts: OwnedReceiptPort
    content_for: Callable[[str], PreparedContent]
    configuration_provider: Callable[[], dto.AccessConfiguration]
    context_available: bool = True
    unavailable_reason: str | None = None
    source: str = "cli-request"

    def bind(self, context):
        return BoundAuthorisationAccess(self, context)

    def invocation(self, entry, context):
        return InvocationAuthorisation(self.service, entry, context, self.receipts,
            operation_id=context.operation_id, content_for=self.content_for, source=self.source)

    def require_owner(self):
        if not self.context_available:
            raise ConsentError("context_unavailable", self.unavailable_reason or
                               "Exceptional authorisation requires an available MCP instance or brain session run -- program.")


class BoundAuthorisationAccess:
    """Control operations use trusted invocation identity, never a public owner selector."""

    def __init__(self, session: AuthorisationSession, context):
        self.session, self.context = session, context
        self.service = session.service
        self.coordinator = PreparationCoordinator(self.service, session.catalogue, session.content_for)

    def prepare(self, command_id, arguments):
        self.service.check_permission(command_id, request=True)
        self.session.require_owner()
        if "brain_operation" in arguments:
            raise ConsentError("invalid_request", "Prepared business arguments cannot contain brain_operation.")
        try:
            request = self.session.resolver.resolve(command_id, arguments)
        except RequestResolutionError as exc:
            raise ConsentError("invalid_request", str(exc)) from exc
        entry = self.session.catalogue.resolve(request)
        projection = Projection.MCP if self.session.source == "host-request" else Projection.CLI
        if projection not in entry.eligible_projections or entry.initial_class is InitialAuthorisationClass.CONTROL:
            raise ConsentError("unsupported_preparation", "The target is not an ordinary command available through this transport.")
        try:
            value = self.coordinator.prepare(self.context, request, request_id=self.context.invocation_id,
                                             validate_view=self._validate_preparation)
        except ValueError as exc:
            # Domain planners use ValueError for known pre-entry validation.
            # Uncertain private-owner transport failures have distinct I/O types.
            raise ConsentError("invalid_request", str(exc)) from exc
        if value["state"] == "discarded":
            raise ConsentError("operation_missing", "The original preparation was discarded; prepare a new operation.")
        return dto.PreparedOperation(operation_id=value["operation_id"], command_id=value["command_id"],
            command_version=value["command_version"], digest=value["digest"], review=value["review"],
            state=dto.ConsentRecordState(value["state"]))

    @staticmethod
    def _validate_preparation(value):
        from .access.request import OperationConsent, validate_consent_request_size
        from .access.prepare import prepared_result
        from ._response_budget import encoded_result_size
        from .results import Ok
        try:
            validate_consent_request_size(OperationConsent(value["operation_id"], value["digest"], value["review"]))
        except ValueError as exc:
            raise ConsentError("review_capacity", "Required review exceeds the request budget; split the operation.") from exc
        payload = dto.PreparedOperation(**{**value, "state": dto.ConsentRecordState(value["state"])})
        if encoded_result_size(prepared_result(payload)) >= 8000:
            raise ConsentError("review_capacity", "Required review exceeds the response budget; split the operation.")

    def inspect(self, operation_id, *, cursor=None, max_characters=12000):
        from ._response_budget import bounded_text_result
        from .access.prepare import AccessPrepareRequest
        self.session.require_owner()
        value = self.service.inspect(operation_id)
        binding = value["binding"]
        state = dto.ConsentRecordState(value["state"] if value["valid"] else "invalidated")
        # Frozen replay inputs and private pin namespaces are implementation data.
        visible = {"operation_id": operation_id, "digest": value["digest"], "review": value["review"],
                   "command_id": binding["command_id"], "command_version": binding["command_version"],
                   "request": binding["request"], "observations": binding["observations"]}
        text = canonical_json(visible)
        revision = content_digest(canonical_json({"context": self.service.identity.context_id, "value": visible}))
        result = bounded_text_result(AccessPrepareRequest, text, revision, cursor=cursor,
            max_characters=max_characters,
            payload=lambda content, window: dto.PreparedOperationDetails(operation_id=operation_id,
                command_id=binding["command_id"], command_version=binding["command_version"],
                digest=value["digest"], state=state, content=content, range=window))
        if isinstance(result, Error):
            raise ConsentError(result.error.code.value, result.error.message)
        return result.result

    def request(self, *, scope, review, command_id=None, operation_id=None, digest=None):
        self.session.require_owner()
        value = self.service.request(request_id=self.context.invocation_id, scope=scope, review=review,
            command_id=command_id, operation_id=operation_id, digest=digest)
        return dto.AccessRequestDecision(state=dto.CommandAuthorisationState(value.state),
            command_id=value.command_id, scope=ConsentScope(value.scope), changed=value.changed,
            grant_id=value.grant_id, operation_id=value.operation_id, reason=value.reason)

    def reduce(self, *, grant_ids=(), operation_ids=(), commands=(), clear_grants=False):
        self.session.require_owner()
        changed = (self.coordinator.discard(operation_ids) if operation_ids else
                   self.service.reduce(grant_ids=grant_ids, commands=commands, clear_grants=clear_grants))
        return dto.AccessReductionResult(changed=changed)

    def command_access(self, command_id):
        return self.command_access_many((command_id,))[0]

    def command_access_many(self, command_ids):
        states = self.service.command_states(command_ids)
        policy = self.service.policy
        available = self.session.context_available
        results = []
        for command in command_ids:
            state = states[command]
            permitted = command in policy.permissions and command in self.service.versions
            requestable = bool(permitted and command not in policy.controls and
                               policy.request_policy == "allowed" and available)
            boundary = ("permissions" if not permitted else "authorisation" if state == "authorised"
                        else "policy" if policy.request_policy != "allowed"
                        else "context" if not available else "authorisation")
            if state != "authorised" and not requestable:
                state = "denied"
            results.append(dto.CommandAuthorisation(command_id=command,
                state=dto.CommandAuthorisationState(state), boundary=boundary,
                requestable=requestable,
                command_review=self.service.command_review(command) if requestable else None,
                next_action=CommandNextAction("access.status", (CommandArgument("command_id", command),))
                            if state != "authorised" and requestable else
                            CommandNextAction("vault.read-config")
                            if state != "authorised" and boundary == "policy" else None))
        return tuple(results)

    def configuration(self):
        """Inspect bounded redacted effective settings and their winning sources."""
        self.service.check_permission("vault.read-config")
        return self.session.configuration_provider()

    def summary(self):
        self.service.check_permission("session.start")
        available = self.session.context_available
        requests = available and self.service.policy.request_policy == "allowed"
        return dto.AccessSummary(permissions=dto.PermissionsSummary(ceiling=self.service.policy.ceiling_profile),
            authorisation=dto.AuthorisationSummary(initial=self.service.policy.initial_mode,
                context=self.service.identity.kind, expires="context-end", scopes=("operation", "command"),
                request_policy=self.service.policy.request_policy, context_available=available,
                prepare="access.prepare" if requests else None, request="access.request" if requests else None,
                status="access.status", reduce="access.reduce" if available else None,
                rule="Within permissions only. New instance needs fresh consent. Request explicitly; never auto-retry a denied operation."))

    def status(self, *, view=dto.AccessStatusView.GRANTS, command_id=None, cursor=None, page_size=16):
        scope = content_digest(canonical_json({"owner": self.service.identity.owner,
                                              "view": view.value, "command": command_id}))
        if cursor is not None and cursor.scope != scope:
            raise ConsentError("stale_cursor", "This cursor belongs to another context, view or command filter.")
        value = self.service.inventory(view.value, after=cursor.after if cursor else "",
            revision=cursor.revision if cursor else None, limit=page_size, command_id=command_id)
        constructors = {dto.AccessStatusView.GRANTS: dto.AccessGrantSummary,
                        dto.AccessStatusView.OPERATIONS: dto.PreparedOperationSummary,
                        dto.AccessStatusView.INITIAL: dto.InitialCommandAuthorisation}
        entries = []
        for item in value["items"]:
            item = dict(item)
            if "state" in item:
                item["state"] = dto.ConsentRecordState(item["state"])
            if "scope" in item:
                item["scope"] = ConsentScope(item["scope"])
            entries.append(constructors[view](**item))
        identity = self.service.identity
        return dto.AccessStatusPayload(identity=dto.AccessIdentity(brain_id=identity.brain_id,
                principal=identity.principal, context_id=identity.context_id, kind=identity.kind),
            permissions=dto.PermissionsSummary(ceiling=self.service.policy.ceiling_profile),
            initial=dto.AccessInitialSummary(mode=self.service.policy.initial_mode,
                authorised_count=value["initial_count"], reduced_count=value["reduced_count"]),
            request_policy=self.service.policy.request_policy,
            context_available=self.session.context_available,
            configuration_generation=value["generation"], view=view, entries=tuple(entries),
            command=self.command_access(command_id) if command_id is not None else None,
            next_cursor=dto.AccessPageCursor(revision=value["revision"], after=value["next_after"], scope=scope)
                        if value["next_after"] is not None else None)
