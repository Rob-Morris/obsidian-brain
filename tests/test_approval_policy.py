"""Policy and release-contract behaviour independent of client syntax."""

from dataclasses import replace
import json
from pathlib import Path

import pytest

from _bootstrap.approval_policy import CommandFact, decision, desired_policy, read_snapshot, snapshot
from approval_contract import build_contract, build_launcher_contract


def fact(classification="content", version=1):
    return CommandFact("document.structured-edit", version, "application", classification,
                       "document_structured-edit", ("document", "structured-edit"))


def test_normal_versions_follow_policy_not_fingerprint():
    assert desired_policy(((fact(),), (fact(version=2),)), "mcp") == {"document_structured-edit": "allow"}


def test_mixed_classification_requires_review():
    assert desired_policy(((fact(),), (fact("exceptional"),)), "mcp") == {"document_structured-edit": "prompt"}


def test_unknown_never_acquires_approval():
    assert desired_policy(((fact(),), (fact("future"),)), "mcp") == {"document_structured-edit": "unknown"}


def test_contract_roundtrip_and_tampering():
    value = snapshot((fact(),))
    assert read_snapshot(value) == (fact(),)
    value["entries"][0]["classification"] = "observation"
    with pytest.raises(ValueError, match="fingerprint"):
        read_snapshot(value)


def test_shipped_contract_matches_canonical_catalogue():
    path = Path(__file__).resolve().parents[1] / "src/brain-core/approval-contract.json"
    assert json.loads(path.read_text()) == build_contract()
    launcher = Path(__file__).resolve().parents[1] / "cli/approval-contract.json"
    assert json.loads(launcher.read_text()) == build_launcher_contract()


def test_released_commands_all_have_explicit_policy_classification():
    facts = read_snapshot(build_contract())
    assert not [f.command_id for f in facts if decision(f) == "unknown"]
    desired = desired_policy((facts,), "mcp")
    assert desired["access_request"] == "prompt"
    assert desired["brain_proxy_restart"] == "prompt"
    assert desired["brain_proxy_status"] == "allow"
    assert desired["document_structured-edit"] == "allow"


def test_duplicate_native_identity_is_rejected():
    with pytest.raises(ValueError, match="duplicate native"):
        desired_policy(((fact(), replace(fact(), command_id="other.command")),), "mcp")


def test_privileged_launcher_output_retains_review_even_without_file_writes():
    assert decision(CommandFact("operator.generate-key", 1, "launcher", "none", None,
                                ("operator", "generate-key"), "operator")) == "prompt"
