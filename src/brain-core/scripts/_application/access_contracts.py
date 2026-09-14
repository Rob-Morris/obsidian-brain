"""Transport-neutral permission, instance authorisation and control contracts."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal, Mapping, Protocol

from ._response_budget import ContentRange, TextCursor
from .consent import ConsentScope
from .results import CommandNextAction


class CommandAuthorisationState(str, Enum):
    AUTHORISED = "authorised"
    REQUIRED = "authorisation_required"
    DENIED = "denied"


class AccessStatusView(str, Enum):
    GRANTS = "grants"
    OPERATIONS = "operations"
    INITIAL = "initial"


class ConsentRecordState(str, Enum):
    PREPARED = "prepared"
    AUTHORISED = "authorised"
    RESERVED = "reserved"
    ENTERED = "entered"
    SPENT = "spent"
    REVOKED = "revoked"
    INVALIDATED = "invalidated"
    DISCARDING = "discarding"
    DISCARDED = "discarded"


@dataclass(frozen=True, slots=True)
class AccessIdentity:
    brain_id: str
    principal: str
    context_id: str
    kind: str


@dataclass(frozen=True, slots=True)
class PermissionsSummary:
    ceiling: str


@dataclass(frozen=True, slots=True)
class AuthorisationSummary:
    initial: str
    context: str
    expires: Literal["context-end"]
    scopes: tuple[Literal["operation", "command"], ...]
    request_policy: str
    context_available: bool
    prepare: str | None
    request: str | None
    status: str | None
    reduce: str | None
    rule: str


@dataclass(frozen=True, slots=True)
class AccessSummary:
    permissions: PermissionsSummary
    authorisation: AuthorisationSummary


@dataclass(frozen=True, slots=True)
class CommandAuthorisation:
    command_id: str
    state: CommandAuthorisationState
    boundary: str
    requestable: bool
    command_review: str | None = None
    next_action: CommandNextAction | None = None


@dataclass(frozen=True, slots=True)
class PreparedOperation:
    operation_id: str
    command_id: str
    command_version: int
    digest: str
    review: str
    state: ConsentRecordState


@dataclass(frozen=True, slots=True)
class PreparedOperationDetails:
    operation_id: str
    command_id: str
    command_version: int
    digest: str
    state: ConsentRecordState
    content: str
    range: ContentRange


@dataclass(frozen=True, slots=True)
class AccessRequestDecision:
    state: CommandAuthorisationState
    command_id: str
    scope: ConsentScope
    changed: bool
    grant_id: str | None = None
    operation_id: str | None = None
    reason: str | None = None
    next_action: CommandNextAction | None = None


@dataclass(frozen=True, slots=True)
class AccessGrantSummary:
    grant_id: str
    command_id: str
    command_version: int
    scope: ConsentScope
    state: ConsentRecordState
    operation_id: str | None = None


@dataclass(frozen=True, slots=True)
class PreparedOperationSummary:
    operation_id: str
    command_id: str
    command_version: int
    digest: str
    state: ConsentRecordState


@dataclass(frozen=True, slots=True)
class InitialCommandAuthorisation:
    command_id: str
    authorised: bool
    source: str
    reduced: bool


@dataclass(frozen=True, slots=True)
class AccessPageCursor:
    revision: int
    after: str
    scope: str

    def __post_init__(self) -> None:
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 0:
            raise ValueError("access cursor revision must be a non-negative integer")
        if not isinstance(self.after, str) or not self.after or not isinstance(self.scope, str) or not self.scope:
            raise ValueError("access cursor requires non-empty after and scope")


@dataclass(frozen=True, slots=True)
class AccessInitialSummary:
    mode: str
    authorised_count: int
    reduced_count: int


@dataclass(frozen=True, slots=True)
class AccessStatusPayload:
    identity: AccessIdentity
    permissions: PermissionsSummary
    initial: AccessInitialSummary
    request_policy: str
    context_available: bool
    configuration_generation: str
    view: AccessStatusView
    entries: tuple[AccessGrantSummary | PreparedOperationSummary | InitialCommandAuthorisation, ...]
    command: CommandAuthorisation | None = None
    next_cursor: AccessPageCursor | None = None


@dataclass(frozen=True, slots=True)
class AccessReductionResult:
    changed: bool


class AuthorisationAccess(Protocol):
    def prepare(self, command_id: str, arguments: Mapping[str, object]) -> PreparedOperation: ...
    def inspect(self, operation_id: str, *, cursor: TextCursor | None = None,
                max_characters: int = 12000) -> PreparedOperationDetails: ...
    def request(self, *, scope: ConsentScope, review: str,
                command_id: str | None = None, operation_id: str | None = None,
                digest: str | None = None) -> AccessRequestDecision: ...
    def status(self, *, view: AccessStatusView = AccessStatusView.GRANTS,
               command_id: str | None = None, cursor: AccessPageCursor | None = None,
               page_size: int = 16) -> AccessStatusPayload: ...
    def reduce(self, *, grant_ids: tuple[str, ...] = (), operation_ids: tuple[str, ...] = (),
               commands: tuple[str, ...] = (), clear_grants: bool = False) -> AccessReductionResult: ...
    def summary(self) -> AccessSummary: ...
    def configuration(self) -> AccessConfiguration: ...
    def command_access(self, command_id: str) -> CommandAuthorisation: ...
    def command_access_many(self, command_ids: tuple[str, ...]) -> tuple[CommandAuthorisation, ...]: ...


@dataclass(frozen=True, slots=True)
class InitialAuthorisationOverride:
    command_id: str
    authorised: bool


@dataclass(frozen=True, slots=True)
class AccessConfigurationDiagnostic:
    code: str
    setting: str
    source: str
    message: str


@dataclass(frozen=True, slots=True)
class AccessConfiguration:
    initial_mode: str
    initial_commands: tuple[str, ...] | None
    initial_source: str
    overrides: tuple[InitialAuthorisationOverride, ...]
    overrides_source: str
    request_policy: str
    request_policy_source: str
    diagnostics: tuple[AccessConfigurationDiagnostic, ...]
    revision: str
    effective_request_policy: str
