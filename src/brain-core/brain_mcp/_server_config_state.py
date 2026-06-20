"""Explicit state machine for MCP config freshness.

This module models the question *"is the published config fresh and healthy?"*
as one explicit state value with two pure transitions. It deliberately knows
nothing about the config dict itself (the server holds that in `_config`); it
only tracks the input signature and any user-facing error.

Pure by contract: imports nothing from the server package, touches no globals,
threads, or filesystem. All I/O — probing the signature, loading config — is
performed by the impure caller, which feeds the *outcomes* in here. Error
strings arrive pre-formatted (the caller owns phase wording such as
"...during startup"); the transitions store and return them verbatim.

Distinctions the four old globals conflated, now explicit:
  - probe failure (transient) vs load failure (sticky)
  - last-good signature vs no config ever loaded (previous_signature is None)
  - same signature vs changed signature
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConfigFresh:
    """`_config` is loaded and current at this input signature."""

    signature: tuple


@dataclass(frozen=True)
class ConfigProbeError:
    """Transient: the cheap signature probe failed.

    `_config` lingers as last-good. `previous_signature` is the signature the
    last-good config was loaded at (None when no config has ever loaded, e.g.
    a startup probe failure). Clears when a later probe succeeds at the same
    `previous_signature` — no reload required.
    """

    previous_signature: tuple | None
    error: str


@dataclass(frozen=True)
class ConfigLoadError:
    """Sticky: a *changed* config failed to load (YAML/validation).

    `_config` lingers as last-good. Retries only when the signature changes
    again (a different `bad_signature`); a probe at the same `bad_signature`
    stays failed without re-attempting the load.
    """

    bad_signature: tuple
    error: str


ConfigState = ConfigFresh | ConfigProbeError | ConfigLoadError


# ---------------------------------------------------------------------------
# Outcomes fed in by the impure caller
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProbeOk:
    paths: tuple
    signature: tuple


@dataclass(frozen=True)
class ProbeFailed:
    error: str


ProbeOutcome = ProbeOk | ProbeFailed


@dataclass(frozen=True)
class LoadOk:
    config: dict


@dataclass(frozen=True)
class LoadFailed:
    error: str


LoadOutcome = LoadOk | LoadFailed


# ---------------------------------------------------------------------------
# Decisions returned to the caller
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Settled:
    """No load needed. Commit `next_state` and return `error` (None when ok)."""

    next_state: ConfigState
    error: str | None


@dataclass(frozen=True)
class NeedsLoad:
    """Signature changed. Caller must load `paths`, then call `on_load`."""

    paths: tuple
    signature: tuple


ProbeDecision = Settled | NeedsLoad


@dataclass(frozen=True)
class LoadCommit:
    """Result of a load. Commit `next_state`; publish side effects iff `publish`."""

    next_state: ConfigState
    publish: bool
    error: str | None


# ---------------------------------------------------------------------------
# Pure transitions
# ---------------------------------------------------------------------------


def _comparison_signature(state: ConfigState) -> tuple | None:
    """The signature a fresh probe is compared against to detect change."""
    if isinstance(state, ConfigFresh):
        return state.signature
    if isinstance(state, ConfigProbeError):
        return state.previous_signature
    return state.bad_signature  # ConfigLoadError


def on_probe(state: ConfigState, outcome: ProbeOutcome) -> ProbeDecision:
    """Decide what a probe outcome means for the current state."""
    if isinstance(outcome, ProbeFailed):
        if isinstance(state, ConfigLoadError):
            # Sticky load error survives a transient probe failure: keep it,
            # and surface the load error, not the probe error.
            return Settled(state, state.error)
        if isinstance(state, ConfigFresh):
            return Settled(
                ConfigProbeError(state.signature, outcome.error), outcome.error
            )
        # Already a probe error: keep the original last-good signature so a
        # later same-signature probe can still recover without a reload.
        return Settled(
            ConfigProbeError(state.previous_signature, outcome.error), outcome.error
        )

    # ProbeOk
    if outcome.signature == _comparison_signature(state):
        if isinstance(state, ConfigFresh):
            return Settled(state, None)
        if isinstance(state, ConfigProbeError):
            # Recovered: signature is unchanged from last-good, so the lingering
            # `_config` is still correct. No reload. (previous_signature is a
            # real tuple here — a None previous can never equal a probe signature.)
            return Settled(ConfigFresh(state.previous_signature), None)
        # ConfigLoadError at the same bad signature: do not retry the load.
        return Settled(state, state.error)
    return NeedsLoad(outcome.paths, outcome.signature)


def on_load(signature: tuple, outcome: LoadOutcome) -> LoadCommit:
    """Decide what a load outcome means for the changed signature."""
    if isinstance(outcome, LoadOk):
        return LoadCommit(ConfigFresh(signature), publish=True, error=None)
    return LoadCommit(
        ConfigLoadError(signature, outcome.error), publish=False, error=outcome.error
    )
