"""Application-owned, instance-scoped consent over an opaque atomic store.

The store owns lifetime and compare/exchange, not permissions or scope matching.
Successful admission spends specific consent even for read-only operations.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Mapping, Protocol
import uuid

from .preparation import OperationBinding, canonical_json, content_digest
from .types import validate_command_id


MAX_PREPARED_OPERATIONS = 128
MAX_GRANTS = 128
MAX_REQUEST_IDENTITIES = 4096
MAX_REVIEW_BYTES = 7000
_CONTEXT = "consent/context"


class ConsentError(Exception):
    """A consent boundary rejected work with an actionable, non-secret reason."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


class ConsentScope(str, Enum):
    OPERATION = "operation"
    COMMAND = "command"


class StateSnapshot(Protocol):
    values: dict[str, object]
    versions: dict[str, int]


class ConsentStateStore(Protocol):
    """Read selected opaque records and atomically replace matching versions."""

    def snapshot(self, keys: tuple[str, ...]) -> StateSnapshot: ...

    def compare_exchange(self, expected_versions: Mapping[str, int],
                         writes: Mapping[str, object | None]) -> bool: ...

    def list_keys(self, *, prefix: str = "", after: str | None = None,
                  limit: int = 128, revision: int | None = None): ...


@dataclass(frozen=True, slots=True)
class ConsentIdentity:
    brain_id: str
    vault_root: str
    principal: str
    context_id: str
    kind: str
    permission_generation: str
    interface_generation: str

    def __post_init__(self):
        if any(not isinstance(value, str) or not value for value in (
            self.brain_id, self.vault_root, self.principal, self.context_id,
            self.permission_generation, self.interface_generation,
        )):
            raise ValueError("consent requires complete trusted ownership")
        if self.kind not in {"mcp-instance", "cli-job", "standalone"}:
            raise ValueError("invalid consent context kind")

    @property
    def owner(self) -> dict:
        return {name: getattr(self, name) for name in (
            "brain_id", "vault_root", "principal", "context_id", "kind",
        )}


@dataclass(frozen=True, slots=True)
class ConsentPolicy:
    ceiling_profile: str
    permissions: frozenset[str]
    initial: frozenset[str]
    initial_mode: str = "normal"
    request_policy: str = "allowed"
    controls: frozenset[str] = frozenset()

    def __post_init__(self):
        if not self.initial <= self.permissions:
            raise ValueError("initial authorisation exceeds credential permissions")
        for command in self.permissions | self.controls:
            validate_command_id(command)
        if self.request_policy not in {"allowed", "denied", "migration_required"}:
            raise ValueError("invalid consent request policy")
        if not self.ceiling_profile or self.initial_mode not in {"normal", "read-only", "explicit"}:
            raise ValueError("invalid initial authorisation selection")


@dataclass(frozen=True, slots=True)
class ConsentDecision:
    state: str
    command_id: str
    scope: str
    grant_id: str | None = None
    operation_id: str | None = None
    reason: str | None = None
    changed: bool = False


@dataclass(frozen=True, slots=True)
class AdmissionProof:
    command_id: str
    command_version: int
    invocation_id: str
    generation: str
    basis: str
    grant_id: str | None = None
    operation_id: str | None = None


def _identifier(value: str, label: str) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 128:
        raise ValueError(f"{label} must contain 1–128 UTF-8 bytes")
    return value


def _key(prefix: str, identifier: str) -> str:
    return f"consent/{prefix}/{content_digest(_identifier(identifier, prefix))[7:]}"


def _decision(value: dict, *, changed: bool = False) -> ConsentDecision:
    return ConsentDecision(value["state"], value["command_id"], value["scope"],
                           value.get("grant_id"), value.get("operation_id"),
                           value.get("reason"), changed)


class ConsentService:
    """Match explicit grants within current permission and contract generations."""

    def __init__(self, store: ConsentStateStore, identity: ConsentIdentity,
                 policy: ConsentPolicy, command_versions: Mapping[str, int], *,
                 refresh: Callable[[], tuple[ConsentIdentity, ConsentPolicy]] | None = None):
        self.store = store
        self.identity = identity
        self.policy = policy
        self.versions = dict(command_versions)
        self._refresh = refresh
        self._contract_generation = content_digest(canonical_json(self.versions))
        for command, version in self.versions.items():
            validate_command_id(command)
            if type(version) is not int or version < 1:
                raise ValueError("command contract versions must be positive integers")

    def _context(self):
        if self._refresh is not None:
            identity, policy = self._refresh()
            if identity.owner != self.identity.owner:
                raise ConsentError("context_changed", "Brain or authenticated owner changed; start a new context.")
            self.identity, self.policy = identity, policy
        for _ in range(8):
            snapshot = self.store.snapshot((_CONTEXT,))
            context = snapshot.values.get(_CONTEXT)
            if context is None:
                context = {"owner": self.identity.owner, "generation": str(uuid.uuid4()),
                           "permission_generation": self.identity.permission_generation,
                           "interface_generation": self.identity.interface_generation,
                           "contract_generation": self._contract_generation,
                           "prepared_count": 0, "grant_count": 0, "request_count": 0,
                           "reduced": []}
            else:
                if context["owner"] != self.identity.owner:
                    raise ConsentError("foreign_context", "Consent belongs to another Brain or owner context.")
                if (context["permission_generation"] == self.identity.permission_generation
                        and context["interface_generation"] == self.identity.interface_generation
                        and context["contract_generation"] == self._contract_generation):
                    return snapshot
                context = dict(context, generation=str(uuid.uuid4()),
                               permission_generation=self.identity.permission_generation,
                               interface_generation=self.identity.interface_generation,
                               contract_generation=self._contract_generation)
            if self.store.compare_exchange(snapshot.versions, {_CONTEXT: context}):
                return self.store.snapshot((_CONTEXT,))
        raise ConsentError("conflict", "Concurrent context changes; inspect access.status and retry the request.")

    def _transaction(self, keys: tuple[str, ...], change):
        self._context()
        keys = tuple(dict.fromkeys((_CONTEXT, *keys)))
        for _ in range(8):
            snapshot = self.store.snapshot(keys)
            context = snapshot.values[_CONTEXT]
            if (context["permission_generation"] != self.identity.permission_generation
                    or context["interface_generation"] != self.identity.interface_generation
                    or context["contract_generation"] != self._contract_generation):
                self._context()
                continue
            writes, result = change(snapshot.values, context)
            if self.store.compare_exchange(snapshot.versions, writes):
                return result
        raise ConsentError("conflict", "Concurrent consent changes; inspect access.status before retrying.")

    def _permission(self, command_id: str, *, request: bool = False) -> None:
        validate_command_id(command_id)
        if command_id not in self.versions:
            raise ConsentError("unknown_command", "The requested command is not installed.")
        if command_id not in self.policy.permissions:
            raise ConsentError("permission", "Credential permissions deny this command; an authorised administrator must change permissions through CLI.")
        if request and command_id in self.policy.controls:
            raise ConsentError("control", "Session controls cannot be exceptional consent targets.")
        if request and self.policy.request_policy != "allowed":
            raise ConsentError(self.policy.request_policy,
                               "Consent requests are denied by Brain policy." if self.policy.request_policy == "denied"
                               else "Resolve the legacy approval configuration before requesting consent.")
        if request and self.identity.kind == "standalone":
            raise ConsentError("context_required", "Start an explicit job with brain session run before requesting exceptional consent.")

    def check_permission(self, command_id: str, *, request: bool = False) -> None:
        """Validate before any command-specific preparation reads resources."""
        self._context()
        self._permission(command_id, request=request)

    def _initial(self, command: str, context: dict) -> bool:
        return command in self.policy.initial and command not in context["reduced"]

    def _valid_grant(self, grant, context, command: str) -> bool:
        return bool(grant and grant["command_id"] == command
                    and grant["generation"] == context["generation"]
                    and grant["command_version"] == self.versions.get(command)
                    and grant["state"] == "authorised" and not grant.get("revoked"))

    def prepare(self, binding: OperationBinding, *, request_id: str) -> dict:
        """Retain an immutable descriptor; repeated trusted preparation is stable."""
        self.check_permission(binding.command_id, request=True)
        if binding.command_version != self.versions[binding.command_id]:
            raise ConsentError("stale_operation", "Command contract changed; prepare again.")
        if len(canonical_json({"review": binding.review_json}).encode("utf-8")) > MAX_REVIEW_BYTES:
            raise ConsentError("review_capacity", "Required review is too large; split the operation before requesting consent.")
        operation_id = "operation-" + str(uuid.uuid4())
        op_key = _key("operation", operation_id)
        retry_key = _key("preparation-request", request_id)

        prior = self.store.snapshot((retry_key,)).values.get(retry_key)
        existing_key = _key("operation", prior["operation_id"]) if prior else op_key

        def create(values, context):
            self._permission(binding.command_id, request=True)
            prior = values.get(retry_key)
            if prior is not None:
                if prior["digest"] != binding.digest or prior["generation"] != context["generation"]:
                    raise ConsentError("request_conflict", "Preparation retry differs from the original operation.")
                if _key("operation", prior["operation_id"]) != existing_key:
                    raise ConsentError("conflict", "Preparation changed concurrently; retry the preparation request.")
                existing = values.get(existing_key)
                if existing is None:
                    return {}, {"operation_id": prior["operation_id"], "digest": prior["digest"],
                                "state": "discarded"}
                return {}, self._view(existing)
            if context["prepared_count"] >= MAX_PREPARED_OPERATIONS:
                raise ConsentError("capacity", "Prepared-operation limit reached; discard owned descriptors through access.reduce.")
            self._request_capacity(context)
            operation = {"operation_id": operation_id, "binding": binding.to_wire(),
                         "digest": binding.digest, "review": binding.review_json,
                         "generation": context["generation"], "state": "prepared"}
            view = self._view(operation)
            retry = {"operation_id": operation_id, "digest": binding.digest,
                     "generation": context["generation"]}
            updated = dict(context, prepared_count=context["prepared_count"] + 1,
                           request_count=context["request_count"] + 1)
            return {_CONTEXT: updated, op_key: operation, retry_key: retry}, view

        return self._transaction((op_key, retry_key, existing_key), create)

    @staticmethod
    def _request_capacity(context):
        if context["request_count"] >= MAX_REQUEST_IDENTITIES:
            raise ConsentError("capacity", "Request-identity limit reached; start a new context. Replay protection cannot be evicted.")

    @staticmethod
    def _view(operation):
        binding = operation["binding"]
        return {"operation_id": operation["operation_id"], "command_id": binding["command_id"],
                "command_version": binding["command_version"], "digest": operation["digest"],
                "review": operation["review"], "state": operation["state"]}

    def inspect(self, operation_id: str) -> dict:
        """Read an owned descriptor; it is data, never an authority capability."""
        key = _key("operation", operation_id)

        def read(values, context):
            operation = values.get(key)
            if operation is None:
                raise ConsentError("operation_missing", "Prepared operation is absent; prepare it in this context.")
            self._permission(operation["binding"]["command_id"])
            return {}, dict(operation, valid=operation["generation"] == context["generation"])

        return self._transaction((key,), read)

    def command_review(self, command_id: str) -> str:
        validate_command_id(command_id)
        return f"Authorise {command_id} throughout this Brain for this {self.identity.kind}; consent ends with the context or explicit revocation."

    def request(self, *, request_id: str, scope: ConsentScope,
                command_id: str | None = None, operation_id: str | None = None,
                digest: str | None = None, review: str) -> ConsentDecision:
        """Grant exactly the reviewed scope without executing its operation."""
        self._context()
        if not isinstance(scope, ConsentScope):
            raise ValueError("consent scope must be operation or command")
        if scope is ConsentScope.OPERATION:
            if operation_id is None or command_id is not None:
                raise ValueError("specific consent requires only an operation selector")
            operation = self.inspect(operation_id)
            command_id = operation["binding"]["command_id"]
            if digest != operation["digest"] or review != operation["review"]:
                raise ConsentError("review_mismatch", "Consent must echo the exact prepared digest and review.")
        else:
            if command_id is None or operation_id is not None or digest is not None:
                raise ValueError("blanket consent requires one exact command")
            if review != self.command_review(command_id):
                raise ConsentError("review_mismatch", "Blanket consent must state its exact command and whole-Brain/context scope.")
        self._permission(command_id, request=True)
        request_key = _key("request", request_id)
        command_key = _key("command", command_id)
        operation_key = _key("operation", operation_id) if operation_id else None
        fingerprint = content_digest(canonical_json({"scope": scope.value, "command": command_id,
                                                     "operation": operation_id, "digest": digest, "review": review}))
        grant_id = "grant-" + str(uuid.uuid4())
        grant_key = _key("grant", grant_id)
        # Request tombstones carry enough terminal state to deny replay after
        # a descriptor or grant is discarded; they never mint a new grant.
        initial = self.store.snapshot((request_key, command_key, *(tuple([operation_key]) if operation_key else ())))
        prior = initial.values.get(request_key)
        candidate_id = (prior.get("grant_id") if prior else
                        initial.values.get(operation_key if operation_key else command_key, {}).get("grant_id"))
        candidate_key = _key("grant", candidate_id) if candidate_id else grant_key
        keys = tuple(key for key in (request_key, command_key, operation_key, grant_key, candidate_key) if key)

        def grant(values, context):
            self._permission(command_id, request=True)
            previous = values.get(request_key)
            current_source = previous or values.get(operation_key if operation_key else command_key, {})
            if current_source.get("grant_id") != candidate_id:
                raise ConsentError("conflict", "Consent changed concurrently; inspect access.status before retrying.")
            if previous is not None:
                if previous["fingerprint"] != fingerprint:
                    raise ConsentError("request_conflict", "Consent retry changed the requested scope.")
                existing = values.get(candidate_key)
                if previous["generation"] != context["generation"] or not self._valid_grant(existing, context, command_id):
                    return {}, _decision(dict(previous, state="denied", reason="spent_or_revoked"))
                return {}, _decision(previous)
            self._request_capacity(context)
            prepared = values.get(operation_key) if operation_key else None
            if operation_key:
                if (prepared is None or prepared["generation"] != context["generation"]
                        or prepared["digest"] != digest or prepared["state"] not in {"prepared", "authorised"}):
                    raise ConsentError("stale_operation", "Operation is stale or already entered; prepare a new operation.")
            existing = values.get(candidate_key)
            reusable = (self._valid_grant(existing, context, command_id)
                        and existing["scope"] == scope.value
                        and existing.get("operation_id") == operation_id)
            writes = {}
            selected_id = existing["grant_id"] if reusable else grant_id
            if not reusable:
                if context["grant_count"] >= MAX_GRANTS:
                    raise ConsentError("capacity", "Grant limit reached; reduce unused grants explicitly.")
                record = {"grant_id": grant_id, "command_id": command_id,
                          "command_version": self.versions[command_id], "scope": scope.value,
                          "operation_id": operation_id, "generation": context["generation"],
                          "state": "authorised"}
                writes[grant_key] = record
                if operation_key:
                    writes[operation_key] = dict(prepared, state="authorised", grant_id=grant_id)
                else:
                    writes[command_key] = {"grant_id": grant_id}
            result = {"state": "authorised", "command_id": command_id, "scope": scope.value,
                      "grant_id": selected_id, "operation_id": operation_id,
                      "generation": context["generation"], "fingerprint": fingerprint}
            writes[request_key] = result
            writes[_CONTEXT] = dict(context, request_count=context["request_count"] + 1,
                                    grant_count=context["grant_count"] + (not reusable))
            return writes, _decision(result, changed=not reusable)

        return self._transaction(keys, grant)

    def state(self, command_id: str, *, operation_id: str | None = None) -> str:
        """Observe access without making specific consent command-wide."""
        self._context()
        if command_id not in self.policy.permissions or command_id not in self.versions:
            return "denied"
        command_key = _key("command", command_id)
        operation_key = _key("operation", operation_id) if operation_id else None
        first = self.store.snapshot((_CONTEXT, command_key, *((operation_key,) if operation_key else ())))
        context = first.values[_CONTEXT]
        if self._initial(command_id, context):
            return "authorised"
        source = first.values.get(operation_key if operation_key else command_key, {})
        grant_id = source.get("grant_id")
        if grant_id:
            grant = self.store.snapshot((_key("grant", grant_id),)).values.get(_key("grant", grant_id))
            if self._valid_grant(grant, context, command_id):
                return "authorised"
        if self.policy.request_policy != "allowed" or command_id in self.policy.controls:
            return "denied"
        return "authorisation_required"

    def admit(self, command_id: str, command_version: int, invocation_id: str, *,
              operation_id: str | None = None, binding: OperationBinding | None = None,
              before_enter: Callable[[AdmissionProof], None] | None = None) -> AdmissionProof:
        """Reserve once at the owner guard, after validation and before operation entry."""
        self.check_permission(command_id)
        _identifier(invocation_id, "invocation")
        if self.versions.get(command_id) != command_version:
            raise ConsentError("contract_changed", "Command contract changed; start from fresh discovery.")
        command_key = _key("command", command_id)
        operation_key = _key("operation", operation_id) if operation_id else None
        first = self.store.snapshot((command_key, *((operation_key,) if operation_key else ())))
        source = first.values.get(operation_key if operation_key else command_key, {})
        grant_id = source.get("grant_id")
        grant_key = _key("grant", grant_id) if grant_id else None
        keys = tuple(key for key in (command_key, operation_key, grant_key) if key)

        def reserve(values, context):
            self._permission(command_id)
            selected = values.get(operation_key) if operation_key else None
            current_source = selected if operation_key else values.get(command_key, {})
            if (current_source or {}).get("grant_id") != grant_id:
                raise ConsentError("conflict", "Consent changed concurrently; inspect access.status before retrying.")
            if operation_key:
                if (selected is None or binding is None or selected["digest"] != binding.digest
                        or selected["generation"] != context["generation"]
                        or binding.command_id != command_id or binding.command_version != command_version):
                    raise ConsentError("stale_operation", "Prepared scope or inputs changed; prepare and request consent again.")
            initial = self._initial(command_id, context)
            grant = values.get(grant_key) if grant_key else None
            valid = self._valid_grant(grant, context, command_id)
            if not initial and selected is not None and selected["state"] != "authorised":
                raise ConsentError("operation_entered", "The specific operation is no longer available; inspect its invocation outcome.")
            if not initial and not valid:
                raise ConsentError("authorisation_required", "This operation needs authorisation; prepare it and call access.request explicitly.")
            basis = "initial" if initial else grant["scope"]
            if not initial and grant["scope"] == "operation" and (not selected or grant["operation_id"] != operation_id):
                raise ConsentError("authorisation_required", "Select the specifically authorised operation.")
            proof = AdmissionProof(command_id, command_version, invocation_id,
                                   context["generation"], basis,
                                   None if initial else grant["grant_id"], operation_id)
            writes = {}
            if not initial and grant["scope"] == "operation":
                writes[grant_key] = dict(grant, state="reserved", invocation_id=invocation_id)
                writes[operation_key] = dict(selected, state="reserved", invocation_id=invocation_id)
            return writes, proof

        proof = self._transaction(keys, reserve)
        if before_enter is not None:
            try:
                before_enter(proof)
            except Exception:
                # The operation has not entered: release only our exact reservation.
                # An unavailable owner leaves it reserved, never implicitly replayable.
                self._cancel_admission(proof)
                raise
        try:
            self._enter(proof)
        except ConsentError:
            self._cancel_admission(proof)
            raise
        return proof

    def _enter(self, proof: AdmissionProof) -> None:
        """Linearise entry after durable intent and a final authority/lifetime check."""
        grant_key = _key("grant", proof.grant_id) if proof.grant_id else None
        operation_key = _key("operation", proof.operation_id) if proof.operation_id else None
        keys = tuple(key for key in (grant_key, operation_key) if key)

        def enter(values, context):
            self._permission(proof.command_id)
            if context["generation"] != proof.generation:
                raise ConsentError("invalidated", "Authority changed before entry; prepare and request consent again.")
            if proof.basis == "initial":
                if not self._initial(proof.command_id, context):
                    raise ConsentError("revoked", "Initial authorisation was reduced before entry.")
                return {}, None
            grant = values.get(grant_key)
            if grant is None or grant.get("revoked"):
                raise ConsentError("revoked", "Consent was revoked before entry.")
            if proof.basis == "command":
                if not self._valid_grant(grant, context, proof.command_id):
                    raise ConsentError("revoked", "Command consent changed before entry.")
                return {}, None
            if grant["state"] != "reserved" or grant.get("invocation_id") != proof.invocation_id:
                raise ConsentError("invocation_conflict", "Specific reservation changed before entry.")
            operation = values.get(operation_key)
            if operation is None or operation.get("invocation_id") != proof.invocation_id:
                raise ConsentError("invocation_conflict", "Prepared operation changed before entry.")
            return {grant_key: dict(grant, state="entered"),
                    operation_key: dict(operation, state="entered")}, None

        self._transaction(keys, enter)

    def _cancel_admission(self, proof: AdmissionProof) -> None:
        if proof.basis != "operation":
            return
        grant_key = _key("grant", proof.grant_id)
        operation_key = _key("operation", proof.operation_id)

        def cancel(values, context):
            grant, operation = values.get(grant_key), values.get(operation_key)
            if (grant is None or grant["state"] != "reserved"
                    or grant.get("invocation_id") != proof.invocation_id):
                return {}, None
            state = ("authorised" if context["generation"] == proof.generation
                     and not grant.get("revoked") else "revoked")
            restored = {key: value for key, value in grant.items() if key != "invocation_id"}
            writes = {grant_key: dict(restored, state=state)}
            if operation is not None:
                restored = {key: value for key, value in operation.items() if key != "invocation_id"}
                writes[operation_key] = dict(restored, state=state)
            return writes, None

        self._transaction((grant_key, operation_key), cancel)

    def finish(self, proof: AdmissionProof) -> None:
        """Mark entered specific consent spent; never infer refund from effect class."""
        if proof.basis != "operation":
            return
        grant_key = _key("grant", proof.grant_id)
        operation_key = _key("operation", proof.operation_id)

        def finish(values, context):
            del context
            grant = values.get(grant_key)
            operation = values.get(operation_key)
            if grant is None:
                return {}, None
            if grant.get("invocation_id") != proof.invocation_id:
                raise ConsentError("invocation_conflict", "Grant reservation belongs to another invocation.")
            if grant["state"] not in {"entered", "spent"}:
                return {}, None
            writes = {grant_key: dict(grant, state="spent")}
            if operation is not None:
                writes[operation_key] = dict(operation, state="spent")
            return writes, None

        self._transaction((grant_key, operation_key), finish)

    def reduce(self, *, grant_ids: tuple[str, ...] = (),
               operation_ids: tuple[str, ...] = (), commands: tuple[str, ...] = (),
               clear_grants: bool = False) -> bool:
        """Remove owned grants/descriptors or narrow initial authorisation only."""
        grant_keys = (self._all_keys("consent/grant/") if clear_grants else
                      tuple(dict.fromkeys(_key("grant", value) for value in grant_ids)))
        operation_keys = tuple(dict.fromkeys(_key("operation", value) for value in operation_ids))
        for command in commands:
            validate_command_id(command)
        initial = self.store.snapshot(grant_keys)
        related = tuple(_key("operation", record["operation_id"])
                        for record in initial.values.values() if record.get("operation_id"))

        def reduce(values, context):
            writes = {}
            removed_grants = 0
            removed_operations = 0
            for key in grant_keys:
                grant = values.get(key)
                if grant is None:
                    continue
                # Revocation cannot discard inputs/outcome identity for entered work.
                if grant["state"] in {"reserved", "entered"}:
                    if not grant.get("revoked"):
                        writes[key] = dict(grant, revoked=True)
                    continue
                writes[key] = None
                removed_grants += 1
                if grant.get("operation_id"):
                    op_key = _key("operation", grant["operation_id"])
                    if op_key not in related:
                        raise ConsentError("conflict", "Consent changed concurrently; retry reduction.")
                    operation = values.get(op_key)
                    if operation is not None and operation.get("grant_id") == grant["grant_id"]:
                        writes[op_key] = dict(operation, state="revoked")
            for key in operation_keys:
                operation = writes.get(key, values.get(key))
                if operation is not None:
                    if operation["state"] in {"authorised", "reserved", "entered"}:
                        raise ConsentError("active_operation", "Revoke or finish the operation's grant before discarding its descriptor.")
                    writes[key] = None
                    removed_operations += 1
            reduced = sorted(set(context["reduced"]) | set(commands))
            changed = bool(writes) or reduced != context["reduced"]
            if changed:
                writes[_CONTEXT] = dict(context, reduced=reduced,
                                        grant_count=context["grant_count"] - removed_grants,
                                        prepared_count=context["prepared_count"] - removed_operations)
            return writes, changed

        return self._transaction(tuple(dict.fromkeys((*grant_keys, *operation_keys, *related))), reduce)

    def _all_keys(self, prefix: str) -> tuple[str, ...]:
        keys = []
        after = None
        revision = None
        while True:
            page = self.store.list_keys(prefix=prefix, after=after, revision=revision)
            keys.extend(page.keys)
            if page.next_after is None:
                return tuple(keys)
            after, revision = page.next_after, page.revision

    def summary(self) -> dict:
        """Return compact ownership/default facts; grant details are separate."""
        self._context()
        return {"permissions": {"ceiling": self.policy.ceiling_profile},
                "authorisation": {"initial": self.policy.initial_mode,
                                  "context": self.identity.kind, "expires": "context-end",
                                  "scopes": ["operation", "command"],
                                  "prepare": "access.prepare", "request": "access.request",
                                  "status": "access.status", "reduce": "access.reduce",
                                  "rule": "Within permissions only. New instance needs fresh consent. Request explicitly; never auto-retry a denied operation."}}

    def grant_page(self, *, after: str | None = None, revision: int | None = None,
                   limit: int = 32) -> dict:
        """Page owned grant metadata without repeating command schemas or bodies."""
        context = self._context().values[_CONTEXT]
        page = self.store.list_keys(prefix="consent/grant/", after=after,
                                    revision=revision, limit=limit)
        values = self.store.snapshot(page.keys).values
        items = []
        for key in page.keys:
            grant = values.get(key)
            if grant is not None:
                item = dict(grant)
                if item["generation"] != context["generation"] or item["command_version"] != self.versions.get(item["command_id"]):
                    item["state"] = "invalidated"
                item.pop("generation", None)
                items.append(item)
        return {"items": items, "next_after": page.next_after,
                "revision": page.revision, "context_id": self.identity.context_id,
                "reduced_commands": context["reduced"]}
