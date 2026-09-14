"""Private authenticated-principal binding at trusted process composition."""

from __future__ import annotations

from dataclasses import asdict

import config as brain_config


_AUTH_KEY = "identity/authentication"
_AUTH_SCHEMA = "brain.owner-authentication/1"


class OwnerAuthenticationError(RuntimeError):
    """The private channel cannot establish the selected Brain's pinned principal."""


class OwnerAuthentication:
    """Bind one authenticated identity without storing its plaintext credential.

    This adapter reads only an actual private owner port supplied by composition.
    A public context identifier or authentication fingerprint cannot construct a
    usable session. Only the supervisor/initial child may seed an empty owner.
    """

    def __init__(self, store, identity, *, brain_id: str, kind: str):
        self.store = store
        self.owner = {
            "schema": _AUTH_SCHEMA, "brain_id": brain_id,
            "vault_root": identity.vault_root, "context_id": identity.context_id,
            "kind": kind,
        }

    def _snapshot(self):
        snapshot = self.store.snapshot((_AUTH_KEY,))
        record = snapshot.values.get(_AUTH_KEY)
        if record is not None:
            if (not isinstance(record, dict)
                    or set(record) != {*self.owner, "binding", "state"}
                    or any(record[name] != value for name, value in self.owner.items())):
                raise OwnerAuthenticationError("Private authentication belongs to another Brain or process context.")
            if record["state"] != "active":
                raise OwnerAuthenticationError("This context's authentication has been revoked; start a new instance.")
        return snapshot, record

    def binding(self, *, required=True):
        """Require the existing supervisor/initial-child seed, never create one."""
        _snapshot, record = self._snapshot()
        if record is None and not required:
            return None
        if record is None:
            raise OwnerAuthenticationError("This job has no authenticated owner; start it with brain session run.")
        raw = record["binding"]
        if not isinstance(raw, dict):
            raise OwnerAuthenticationError("Private authentication binding is malformed.")
        try:
            return brain_config.OperatorAuthenticationBinding(**raw)
        except (TypeError, ValueError) as exc:
            raise OwnerAuthenticationError("Private authentication binding is malformed.") from exc

    def seed(self, binding) -> None:
        """Initial composition only: compare-and-set one immutable active identity."""
        value = {**self.owner, "binding": asdict(binding), "state": "active"}
        for _ in range(8):
            snapshot, record = self._snapshot()
            if record is not None:
                if record != value:
                    raise OwnerAuthenticationError("An existing owner cannot authenticate as a different principal.")
                return
            if self.store.compare_exchange(snapshot.versions, {_AUTH_KEY: value}):
                return
        raise OwnerAuthenticationError("Concurrent owner authentication could not be resolved.")

    def revoke(self) -> None:
        """Record positively established credential revocation; never a caller typo."""
        for _ in range(8):
            snapshot = self.store.snapshot((_AUTH_KEY,))
            record = snapshot.values.get(_AUTH_KEY)
            if record is None or record.get("state") == "revoked":
                return
            if any(record.get(name) != value for name, value in self.owner.items()):
                raise OwnerAuthenticationError("Private authentication ownership changed.")
            if self.store.compare_exchange(snapshot.versions,
                                           {_AUTH_KEY: dict(record, state="revoked")}):
                return
        raise OwnerAuthenticationError("Authentication was revoked but its terminal marker could not be recorded.")
