from __future__ import annotations

from brain_lab.model import (
    BaselineRecipe,
    BaseSpec,
    EffectCertainty,
    ExecRequest,
    PreparationSpec,
    RunSpec,
    SourceSpec,
    VaultSeedSpec,
    aggregate_effect_certainty,
    validate_linux_platform,
)
from brain_lab.cli import _exit_code


def test_baseline_identity_contains_only_result_affecting_recipe_inputs():
    preparation = PreparationSpec("template", "brain-0.55-0.62", 1)
    recipe = BaselineRecipe("base-a", "source-a", "seed-a", preparation)

    assert recipe.identity() == BaselineRecipe("base-a", "source-a", "seed-a", preparation).identity()
    assert recipe.identity() != BaselineRecipe("base-a", "source-b", "seed-a", preparation).identity()

    RunSpec("image-a", "linux/arm64", network="bridge")
    ExecRequest(("echo", "hello"), timeout_seconds=5)
    assert recipe.identity() == BaselineRecipe("base-a", "source-a", "seed-a", preparation).identity()


def test_linux_platform_validator_rejects_non_linux_and_malformed_values():
    assert validate_linux_platform("linux/arm64") == "linux/arm64"
    assert validate_linux_platform("linux/arm64/v8") == "linux/arm64/v8"
    for invalid in ("darwin/arm64", "linux", "linux/arm64/", "linux/arm64/v8/extra", None):
        try:
            validate_linux_platform(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid platform was accepted: {invalid!r}")


def test_base_identity_changes_with_platform_specific_identity():
    arm = BaseSpec(
        "ubuntu:24.04",
        "linux/arm64",
        "sha256:arm",
        "ubuntu@sha256:arm",
        "dockerfile",
        "build-inputs",
    )
    amd = BaseSpec(
        "ubuntu:24.04",
        "linux/amd64",
        "sha256:amd",
        "ubuntu@sha256:amd",
        "dockerfile",
        "build-inputs",
    )

    assert arm.identity() != amd.identity()
    changed_probe = BaseSpec(
        "ubuntu:24.04",
        "linux/arm64",
        "sha256:arm",
        "ubuntu@sha256:arm",
        "dockerfile",
        "changed-build-inputs",
    )
    assert arm.identity() != changed_probe.identity()


def test_run_network_is_closed_and_typed():
    assert RunSpec("image", "linux/arm64").network == "none"

    try:
        RunSpec("image", "linux/arm64", network="host")
    except ValueError as exc:
        assert "none" in str(exc)
    else:
        raise AssertionError("host networking must be rejected")


def test_exec_request_requires_argv_and_positive_timeout():
    for value in ((), ("",)):
        try:
            ExecRequest(value)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid argv was accepted")

    try:
        ExecRequest(("true",), timeout_seconds=0)
    except ValueError:
        pass
    else:
        raise AssertionError("zero timeout was accepted")


def test_source_and_seed_identities_include_oci_platform():
    source_values = {
        "tree_sha256": "tree",
        "core_version": "0.62.0",
        "core_sha256": "core",
        "cli_version": "3.1.0",
        "commit": "abc",
        "selector": {"kind": "git"},
    }
    arm_source = SourceSpec(platform="linux/arm64", **source_values)
    amd_source = SourceSpec(platform="linux/amd64", **source_values)
    seed_values = {
        "kind": "template",
        "tree_sha256": "seed",
        "core_version": "0.62.0",
        "core_sha256": "core",
        "source_id": arm_source.identity(),
    }
    arm_seed = VaultSeedSpec(platform="linux/arm64", **seed_values)
    amd_seed = VaultSeedSpec(platform="linux/amd64", **seed_values)

    assert arm_source.identity() != amd_source.identity()
    assert arm_seed.identity() != amd_seed.identity()


def test_effect_aggregation_and_exit_codes_preserve_partial_and_unknown_truth():
    assert aggregate_effect_certainty(["none", "committed"]) is EffectCertainty.COMMITTED
    assert aggregate_effect_certainty(["committed", "partial"]) is EffectCertainty.PARTIAL
    assert aggregate_effect_certainty(["partial", "unknown"]) is EffectCertainty.UNKNOWN
    assert _exit_code({"outcome": "success", "effect_certainty": "committed"}) == 0
    assert _exit_code({"outcome": "success", "effect_certainty": "partial"}) == 1
    assert _exit_code({"outcome": "success", "effect_certainty": "unknown"}) == 4
