"""Behaviour of the pure workspace schema and migration boundary."""

from copy import deepcopy

import pytest

from _bootstrap.workspace_schema import (
    ManifestCompatibility,
    WorkspaceLocation,
    WorkspaceSchemaError,
    classify_workspace_manifest,
    plan_workspace_migration,
    require_current_workspace_manifest,
)


@pytest.mark.parametrize("location", [WorkspaceLocation.EXTERNAL, WorkspaceLocation.EMBEDDED])
def test_legacy_link_is_authoritative_and_preserves_descriptive_metadata(location):
    original = {
        "brain": "my-brain", "slug": "old-local",
        "links": {"workspace": "canonical", "project": "optional"},
        "defaults": {"tags": ["descriptive"]}, "extension": {"items": [1]},
    }
    saved = deepcopy(original)
    plan = plan_workspace_migration(original, location=location)
    assert (plan.classification.brain, plan.classification.slug) == ("my-brain", "canonical")
    assert plan.classification.location is location
    assert plan.payload == {**saved, "schema_version": 1, "slug": "canonical",
                            "links": {"project": "optional"}}
    assert plan.changed
    assert original == saved
    again = plan_workspace_migration(plan.payload, location=location)
    assert not again.changed
    assert again.payload == plan.payload
    plan.payload["extension"]["items"].append(2)
    assert original == saved


@pytest.mark.parametrize("manifest", [{}, {"slug": "never-adopt"},
                                     {"slug": 12, "links": {"project": "description"}}])
def test_unlinked_legacy_never_infers_identity(manifest):
    original = deepcopy(manifest)
    plan = plan_workspace_migration(manifest)
    assert not plan.classification.linked
    assert plan.classification.slug is None
    assert plan.payload == {**{k: v for k, v in manifest.items() if k != "slug"}, "schema_version": 1}
    assert manifest == original
    assert not plan_workspace_migration(plan.payload).changed


@pytest.mark.parametrize("manifest", [
    {"schema_version": 1, "brain": "brain", "slug": "shared", "links": {}},
    {"schema_version": 1, "defaults": {"parent": None}, "unknown": [1]},
])
def test_current_metadata_is_a_no_op_with_independent_payload(manifest):
    plan = plan_workspace_migration(manifest)
    assert not plan.changed
    assert plan.payload == manifest
    assert plan.payload is not manifest
    assert require_current_workspace_manifest(manifest).compatibility is ManifestCompatibility.CURRENT


def test_absent_manifest_is_not_created_or_inferred_for_embedded_folder():
    plan = plan_workspace_migration(None, location=WorkspaceLocation.EMBEDDED)
    assert plan.classification.compatibility is ManifestCompatibility.ABSENT
    assert not plan.classification.linked
    assert plan.payload is None
    assert not plan.changed


def test_vault_self_bypasses_workspace_schema_without_identity():
    plan = plan_workspace_migration({"schema_version": 999}, location=WorkspaceLocation.VAULT_SELF)
    assert plan.classification.compatibility is ManifestCompatibility.NOT_APPLICABLE
    assert not plan.classification.linked
    assert plan.payload is None
    assert not plan.changed


@pytest.mark.parametrize("version", [None, True, False, 0, -1, "1", 1.0])
def test_invalid_version_refuses_without_mutation(version):
    manifest = {"schema_version": version, "extension": [1]}
    with pytest.raises(WorkspaceSchemaError) as failure:
        plan_workspace_migration(manifest)
    assert failure.value.code == "invalid_version"
    assert manifest == {"schema_version": version, "extension": [1]}


def test_future_schema_refuses_before_interpreting_its_fields():
    manifest = {"schema_version": 2, "links": "future-shape"}
    with pytest.raises(WorkspaceSchemaError) as failure:
        plan_workspace_migration(manifest)
    assert failure.value.code == "future_version"
    assert manifest == {"schema_version": 2, "links": "future-shape"}


@pytest.mark.parametrize("manifest", [
    [], {"links": None}, {"links": []}, {"brain": "brain", "slug": "local"},
    {"links": {"workspace": "canonical"}}, {"brain": None},
    {"brain": "brain", "links": {"workspace": None}},
    {"brain": "brain", "links": {"workspace": "Invalid Key"}},
    {"schema_version": 1, "slug": "alone"},
    {"schema_version": 1, "brain": "brain"},
    {"schema_version": 1, "brain": "brain", "slug": "canonical", "links": {"workspace": "canonical"}},
])
def test_invalid_declared_linkage_never_becomes_unlinked(manifest):
    original = deepcopy(manifest)
    with pytest.raises(WorkspaceSchemaError):
        plan_workspace_migration(manifest)
    assert manifest == original


def test_recovery_reads_legacy_identity_but_ordinary_use_requires_upgrade():
    manifest = {"brain": "brain", "slug": "local", "links": {"workspace": "shared"}}
    assert classify_workspace_manifest(manifest).slug == "shared"
    with pytest.raises(WorkspaceSchemaError) as failure:
        require_current_workspace_manifest(manifest)
    assert failure.value.code == "upgrade_required"


@pytest.mark.parametrize("brain", ["123", "brain-" + "a" * 80])
def test_brain_ids_follow_registry_contract_not_artefact_key_limits(brain):
    plan = plan_workspace_migration({"brain": brain, "links": {"workspace": "shared"}})
    assert plan.payload["brain"] == brain


@pytest.mark.parametrize("brain", [True, 12, "", "Bad ID", "brain/path"])
def test_invalid_brain_id_refuses_without_inference(brain):
    with pytest.raises(WorkspaceSchemaError) as failure:
        classify_workspace_manifest({"brain": brain, "links": {"workspace": "shared"}})
    assert failure.value.code == "malformed_binding"


@pytest.mark.parametrize("version", [None, 1])
def test_brain_identity_uses_the_brain_registry_contract(version):
    manifest = {"brain": "123", "slug": "workspace"}
    if version is None:
        manifest["links"] = {"workspace": "workspace"}
    else:
        manifest["schema_version"] = version
    state = classify_workspace_manifest(manifest)
    assert state.brain == "123"
    assert state.slug == "workspace"
