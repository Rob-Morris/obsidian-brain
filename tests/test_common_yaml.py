"""Tests for the Brain-owned standalone YAML subset."""

from __future__ import annotations

import pytest

from _common._yaml import (
    YamlError,
    dump_mapping_text,
    load_mapping_text,
)


def test_load_mapping_text_supports_real_brain_config_shape():
    data = load_mapping_text(
        """
        # Brain vault configuration
        vault:
          brain_name: ""
          profiles:
            reader:
              allow: [brain_init, brain_session, brain_read]
          operators: []
        defaults:
          default_profile: operator
          flags:
            semantic_processing: false
            semantic_retrieval: true
          local_runtime:
            semantic_engine_installed: false
          tool_paths: {}
          exclude:
            artefact_sync: []
        """
    )

    assert data["vault"]["brain_name"] == ""
    assert data["vault"]["profiles"]["reader"]["allow"] == [
        "brain_init",
        "brain_session",
        "brain_read",
    ]
    assert data["vault"]["operators"] == []
    assert data["defaults"]["flags"]["semantic_processing"] is False
    assert data["defaults"]["flags"]["semantic_retrieval"] is True
    assert data["defaults"]["local_runtime"]["semantic_engine_installed"] is False
    assert data["defaults"]["tool_paths"] == {}
    assert data["defaults"]["exclude"]["artefact_sync"] == []


def test_load_mapping_text_supports_workspace_manifest_shape():
    data = load_mapping_text(
        """
        slug: demo-workspace
        links:
          workspace: brain-demo
        defaults:
          tags:
            - workspace/brain-demo
            - project/brain
        """
    )

    assert data == {
        "slug": "demo-workspace",
        "links": {"workspace": "brain-demo"},
        "defaults": {"tags": ["workspace/brain-demo", "project/brain"]},
    }


def test_load_mapping_text_supports_sequence_of_mappings():
    data = load_mapping_text(
        """
        vault:
          operators:
            -
              id: robs-claude
              profile: operator
              auth: {type: key, hash: "sha256:abc"}
        """
    )

    assert data["vault"]["operators"] == [
        {
            "id": "robs-claude",
            "profile": "operator",
            "auth": {"type": "key", "hash": "sha256:abc"},
        }
    ]


def test_load_mapping_text_strips_comments_but_preserves_quoted_hashes():
    data = load_mapping_text(
        """
        defaults:
          tool_paths:
            pandoc: "/tmp/#pandoc" # comment
          default_profile: operator # trailing comment
        """
    )

    assert data["defaults"]["tool_paths"]["pandoc"] == "/tmp/#pandoc"
    assert data["defaults"]["default_profile"] == "operator"


def test_load_mapping_text_keeps_leading_zero_numbers_as_strings():
    data = load_mapping_text(
        """
        defaults:
          tool_paths:
            example: 00123
        """
    )

    assert data["defaults"]["tool_paths"]["example"] == "00123"


def test_dump_mapping_text_round_trips_supported_subset():
    payload = {
        "vault": {
            "brain_name": "",
            "profiles": {
                "reader": {"allow": ["brain_init", "brain_session"]},
            },
            "operators": [
                {
                    "id": "robs-claude",
                    "profile": "operator",
                    "auth": {"type": "key", "hash": "sha256:abc"},
                }
            ],
        },
        "defaults": {
            "default_profile": "operator",
            "flags": {"semantic_retrieval": True, "semantic_processing": False},
            "tool_paths": {},
            "exclude": {"artefact_sync": []},
        },
    }

    rendered = dump_mapping_text(payload)
    reparsed = load_mapping_text(rendered)

    assert reparsed == payload


@pytest.mark.parametrize(
    "text",
    [
        "defaults:\n  flag: !custom true\n",
        "defaults:\n  flag: &anchor true\n",
        "defaults:\n  flag: *anchor\n",
        "defaults:\n  <<: {flag: true}\n",
        "defaults:\n  body: |\n    line\n",
        "---\ndefaults:\n  flag: true\n",
    ],
)
def test_load_mapping_text_rejects_unsupported_yaml_features(text):
    with pytest.raises(YamlError):
        load_mapping_text(text)


def test_load_mapping_text_rejects_invalid_root_sequence():
    with pytest.raises(YamlError):
        load_mapping_text("- value\n")
