"""Pure, table-driven tests for the MCP config-freshness state machine.

No server, filesystem, or threads — these exercise `on_probe` / `on_load`
over every row of the design's transition tables
(Designs/brain-mcp-server/MCP Config Freshness State Machine).
"""

from brain_mcp import _server_config_state as cs

SIG_A = ("a",)
SIG_B = ("b",)
PATHS = ("template", "vault", "local")


# ---------------------------------------------------------------------------
# on_probe — probe failure
# ---------------------------------------------------------------------------


def test_fresh_probe_failure_becomes_transient_probe_error():
    decision = cs.on_probe(cs.ConfigFresh(SIG_A), cs.ProbeFailed("boom"))
    assert decision == cs.Settled(cs.ConfigProbeError(SIG_A, "boom"), "boom")


def test_probe_error_probe_failure_keeps_original_last_good_signature():
    state = cs.ConfigProbeError(SIG_A, "first")
    decision = cs.on_probe(state, cs.ProbeFailed("second"))
    # previous_signature stays SIG_A so a later same-signature probe recovers.
    assert decision == cs.Settled(cs.ConfigProbeError(SIG_A, "second"), "second")


def test_startup_probe_error_probe_failure_keeps_none_signature():
    state = cs.ConfigProbeError(None, "config not loaded")
    decision = cs.on_probe(state, cs.ProbeFailed("still missing"))
    assert decision == cs.Settled(cs.ConfigProbeError(None, "still missing"), "still missing")


def test_load_error_survives_transient_probe_failure():
    state = cs.ConfigLoadError(SIG_A, "bad yaml")
    decision = cs.on_probe(state, cs.ProbeFailed("transient stat error"))
    # Sticky load error is kept and surfaced — not overwritten by the probe error.
    assert decision == cs.Settled(state, "bad yaml")


# ---------------------------------------------------------------------------
# on_probe — probe ok, same signature
# ---------------------------------------------------------------------------


def test_fresh_same_signature_is_noop():
    state = cs.ConfigFresh(SIG_A)
    decision = cs.on_probe(state, cs.ProbeOk(PATHS, SIG_A))
    assert decision == cs.Settled(state, None)


def test_probe_error_recovers_without_reload_on_same_signature():
    state = cs.ConfigProbeError(SIG_A, "blip")
    decision = cs.on_probe(state, cs.ProbeOk(PATHS, SIG_A))
    # Signature unchanged from last-good → lingering _config is still correct.
    assert decision == cs.Settled(cs.ConfigFresh(SIG_A), None)


def test_load_error_same_bad_signature_does_not_retry():
    state = cs.ConfigLoadError(SIG_A, "bad yaml")
    decision = cs.on_probe(state, cs.ProbeOk(PATHS, SIG_A))
    assert decision == cs.Settled(state, "bad yaml")


# ---------------------------------------------------------------------------
# on_probe — probe ok, changed signature → NeedsLoad
# ---------------------------------------------------------------------------


def test_fresh_changed_signature_needs_load():
    decision = cs.on_probe(cs.ConfigFresh(SIG_A), cs.ProbeOk(PATHS, SIG_B))
    assert decision == cs.NeedsLoad(PATHS, SIG_B)


def test_probe_error_changed_signature_needs_load():
    decision = cs.on_probe(cs.ConfigProbeError(SIG_A, "blip"), cs.ProbeOk(PATHS, SIG_B))
    assert decision == cs.NeedsLoad(PATHS, SIG_B)


def test_startup_probe_error_any_signature_needs_load():
    # previous_signature None can never equal a real probe signature.
    decision = cs.on_probe(cs.ConfigProbeError(None, "config not loaded"), cs.ProbeOk(PATHS, SIG_A))
    assert decision == cs.NeedsLoad(PATHS, SIG_A)


def test_load_error_changed_signature_needs_load():
    decision = cs.on_probe(cs.ConfigLoadError(SIG_A, "bad yaml"), cs.ProbeOk(PATHS, SIG_B))
    assert decision == cs.NeedsLoad(PATHS, SIG_B)


# ---------------------------------------------------------------------------
# on_load
# ---------------------------------------------------------------------------


def test_load_success_publishes_fresh():
    commit = cs.on_load(SIG_B, cs.LoadOk({"vault": {}}))
    assert commit == cs.LoadCommit(cs.ConfigFresh(SIG_B), publish=True, error=None)


def test_load_failure_becomes_sticky_load_error():
    commit = cs.on_load(SIG_B, cs.LoadFailed("config reload failed: bad yaml"))
    assert commit == cs.LoadCommit(
        cs.ConfigLoadError(SIG_B, "config reload failed: bad yaml"),
        publish=False,
        error="config reload failed: bad yaml",
    )
