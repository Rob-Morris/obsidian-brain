"""Pure workspace manifest compatibility rules; no filesystem or registry access.

Plans describe metadata only. Callers must verify a linked Brain and active
canonical workspace before admitting writes, retaining original bytes for recovery.
Location comes from a trusted caller, never from manifest or folder-name inference.
"""

from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
from typing import Any

from _common._slugs import is_valid_key
from vault_registry import is_valid_brain_id


SCHEMA_VERSION = 1


class WorkspaceLocation(str, Enum):
    EXTERNAL = "external"
    EMBEDDED = "embedded"
    VAULT_SELF = "vault_self"


class ManifestCompatibility(str, Enum):
    ABSENT = "absent"
    LEGACY = "legacy"
    CURRENT = "current"
    NOT_APPLICABLE = "not_applicable"


class WorkspaceSchemaError(ValueError):
    """A refusal with a stable code for caller-owned remediation."""

    def __init__(self, code: str, detail: str, *, field: str | None = None):
        self.code = code
        self.field = field
        super().__init__(detail)


@dataclass(frozen=True)
class ManifestClassification:
    compatibility: ManifestCompatibility
    location: WorkspaceLocation
    source_version: int | None = None
    brain: str | None = None
    slug: str | None = None

    @property
    def linked(self) -> bool:
        """Whether the metadata explicitly declares a complete binding."""
        return self.brain is not None


@dataclass(frozen=True)
class WorkspaceMigrationPlan:
    classification: ManifestClassification
    payload: dict[str, Any] | None
    changed: bool


def classify_workspace_manifest(
    manifest: dict[str, Any] | None,
    *,
    location: WorkspaceLocation = WorkspaceLocation.EXTERNAL,
) -> ManifestClassification:
    """Classify parsed metadata, refusing unsupported or ambiguous declarations.

    ``None`` means no manifest, not an empty manifest. Embedded location does
    not supply binding identity. Vault-self requires no workspace metadata.
    """
    location = WorkspaceLocation(location)
    if location is WorkspaceLocation.VAULT_SELF:
        return ManifestClassification(ManifestCompatibility.NOT_APPLICABLE, location)
    if manifest is None:
        return ManifestClassification(ManifestCompatibility.ABSENT, location)
    if not isinstance(manifest, dict):
        raise WorkspaceSchemaError("malformed_manifest", "Workspace manifest must be a mapping")
    version = manifest.get("schema_version")
    legacy = "schema_version" not in manifest
    if not legacy:
        if type(version) is not int or version < 1:
            raise WorkspaceSchemaError("invalid_version", "schema_version must be a positive integer")
        if version > SCHEMA_VERSION:
            raise WorkspaceSchemaError("future_version", "Workspace schema requires a compatible CLI/runtime upgrade")
    links = manifest.get("links", {})
    if not isinstance(links, dict):
        raise WorkspaceSchemaError("malformed_manifest", "Workspace links must be a mapping")
    brain = manifest.get("brain")
    identity_present = "workspace" in links if legacy else "slug" in manifest
    slug = links.get("workspace") if legacy else manifest.get("slug")
    if not legacy and "workspace" in links:
        raise WorkspaceSchemaError("malformed_binding", "Current metadata must not declare links.workspace")
    if "brain" in manifest or identity_present:
        if not is_valid_brain_id(brain):
            raise WorkspaceSchemaError("malformed_binding", "Binding requires a valid Brain ID and canonical workspace slug",
                                       field="brain")
        if not is_valid_key(slug):
            raise WorkspaceSchemaError("malformed_binding", "Binding requires a valid Brain ID and canonical workspace slug",
                                       field="links.workspace" if legacy else "slug")
    else:
        brain, slug = None, None
    return ManifestClassification(
        ManifestCompatibility.LEGACY if legacy else ManifestCompatibility.CURRENT,
        location, version, brain, slug,
    )


def plan_workspace_migration(
    manifest: dict[str, Any] | None,
    *,
    location: WorkspaceLocation = WorkspaceLocation.EXTERNAL,
) -> WorkspaceMigrationPlan:
    """Return an independent payload; never mutate input or infer a new identity.

    Absent and vault-self metadata have no payload to write. A linked legacy
    plan requires caller verification of its declared Brain/workspace identity.
    Unknown fields and descriptive links are preserved, including empty maps.
    """
    classification = classify_workspace_manifest(manifest, location=location)
    if classification.compatibility in {
        ManifestCompatibility.ABSENT, ManifestCompatibility.NOT_APPLICABLE,
    }:
        return WorkspaceMigrationPlan(classification, None, False)
    payload = deepcopy(manifest)
    if classification.compatibility is ManifestCompatibility.CURRENT:
        return WorkspaceMigrationPlan(classification, payload, False)
    payload["schema_version"] = SCHEMA_VERSION
    if classification.linked:
        payload["slug"] = classification.slug
        del payload["links"]["workspace"]
    else:
        payload.pop("slug", None)
    return WorkspaceMigrationPlan(classification, payload, True)


def require_current_workspace_manifest(
    manifest: dict[str, Any] | None,
    *,
    location: WorkspaceLocation = WorkspaceLocation.EXTERNAL,
) -> ManifestClassification:
    """Refuse legacy metadata during ordinary use; recovery uses classification.

    The caller adds its observed folder and selected Brain to the remediation
    command. Absent and vault-self metadata remain valid unscoped contexts.
    """
    classification = classify_workspace_manifest(manifest, location=location)
    if classification.compatibility is ManifestCompatibility.LEGACY:
        raise WorkspaceSchemaError(
            "upgrade_required", "Workspace metadata requires explicit workspace.upgrade to schema version 1",
        )
    return classification
