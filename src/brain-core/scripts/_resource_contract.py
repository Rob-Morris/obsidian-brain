"""Shared public resource-kind contracts for MCP wrappers and scripts.

Spec is the base contract for discriminated-field validation. Tools that fan out
into per-discriminator field sets (brain_create, brain_move) define a dict of
Spec instances keyed by discriminator value. A shared validator in
brain_mcp/_server_contracts.py enforces required/optional field sets and rejects
extras, keeping all per-tool validation data-driven and extensible.
"""

from dataclasses import dataclass


RESOURCE_KINDS = (
    "artefact",
    "skill",
    "memory",
    "style",
    "template",
)


@dataclass(frozen=True)
class Spec:
    """Required and optional field contract for one discriminator value.

    required_fields: fields that must be present (non-None, non-empty-string).
    optional_fields: fields that are accepted when present but not required.
    Fields not in either set are rejected as extras.
    """
    required_fields: tuple[str, ...]
    optional_fields: tuple[str, ...] = ()


# Per-resource field contracts for brain_create.
#
# body/body_file are mutually exclusive alternatives; neither is mandatory at
# the spec layer because the handler resolves body_file → body using vault_root
# (available only at runtime). body is optional here so callers can pass
# body_file alone. The handler still enforces "at least one of body/body_file"
# for non-artefact resources after resolution.
#
# fix_links uses bool | None = None at the MCP layer (None = absent) so that
# _is_present(False) doesn't incorrectly flag the default as an extra for
# resource kinds that don't accept it.  The artefact spec lists it as optional;
# non-artefact specs omit it, and the handler treats None as False.
CREATE_SPECS: dict[str, Spec] = {
    "artefact": Spec(
        required_fields=("type", "title"),
        optional_fields=("body", "body_file", "frontmatter", "parent", "key", "fix_links"),
    ),
    "skill": Spec(
        required_fields=("name",),
        optional_fields=("body", "body_file", "frontmatter"),
    ),
    "memory": Spec(
        required_fields=("name",),
        optional_fields=("body", "body_file", "frontmatter"),
    ),
    "style": Spec(
        required_fields=("name",),
        optional_fields=("body", "body_file", "frontmatter"),
    ),
    "template": Spec(
        required_fields=("name",),
        optional_fields=("body", "body_file", "frontmatter"),
    ),
}


# Per-resource field contracts for brain_read.
#
# All resources except 'environment' and 'router' require a name parameter.
# 'compliance' accepts an optional name (used as severity filter).
# 'environment' and 'router' accept no fields at all.
READ_SPECS: dict[str, Spec] = {
    "type": Spec(required_fields=("name",)),
    "trigger": Spec(required_fields=("name",)),
    "style": Spec(required_fields=("name",)),
    "template": Spec(required_fields=("name",)),
    "skill": Spec(required_fields=("name",)),
    "plugin": Spec(required_fields=("name",)),
    "memory": Spec(required_fields=("name",)),
    "workspace": Spec(required_fields=("name",)),
    "artefact": Spec(required_fields=("name",)),
    "file": Spec(required_fields=("name",)),
    "archive": Spec(required_fields=("name",)),
    "compliance": Spec(required_fields=(), optional_fields=("name",)),
    "environment": Spec(required_fields=()),
    "router": Spec(required_fields=()),
}


# Per-resource field contracts for brain_list.
#
# 'artefact' supports full filtering; all other resources support only optional
# 'query' (substring filter on name). 'workspace' and 'archive' accept no
# filters at all.
#
# 'top_k' and 'sort' use None defaults at the MCP layer (None = absent) so that
# _is_present() correctly treats unset values as absent for non-artefact specs.
# Defaults (500 / "date_desc") are applied inside the handler after validation.
LIST_SPECS: dict[str, Spec] = {
    "artefact": Spec(
        required_fields=(),
        optional_fields=("type", "parent", "since", "until", "tag", "top_k", "sort"),
    ),
    "workspace": Spec(required_fields=()),
    "archive": Spec(required_fields=()),
    "type": Spec(required_fields=(), optional_fields=("query",)),
    "template": Spec(required_fields=(), optional_fields=("query",)),
    "skill": Spec(required_fields=(), optional_fields=("query",)),
    "trigger": Spec(required_fields=(), optional_fields=("query",)),
    "style": Spec(required_fields=(), optional_fields=("query",)),
    "plugin": Spec(required_fields=(), optional_fields=("query",)),
    "memory": Spec(required_fields=(), optional_fields=("query",)),
}


# ---------------------------------------------------------------------------
# Per-(resource, operation) field contracts for brain_edit.
#
# Two discriminators: resource (artefact vs. non-artefact config resources) and
# operation (edit/append/prepend/delete_section).
#
# fix_links is coerced to None at the MCP boundary when False (bool default) so
# that _is_present(False) does not misfire as an extra for non-artefact specs.
# Non-artefact specs omit it; artefact specs list it as optional.
#
# body/body_file are listed as optional on delete_section so the spec layer does
# not reject them — the script already ignores body for delete_section and
# preflight_request_contract enforces the scope prohibition.  The strict-extras
# rejection handles cross-discriminator mistakes (name on artefact, path on
# skill, fix_links on non-artefact, etc.).
#
# target is REQUIRED for delete_section (both artefact and non-artefact).
# For edit/append/prepend it is optional at the presence layer; the inter-field
# rules (scope requires target, target requires scope) stay in
# preflight_request_contract.
# ---------------------------------------------------------------------------

_ARTEFACT_EDIT_OPS_OPTIONAL = (
    "body", "body_file", "frontmatter", "target", "selector", "scope", "fix_links"
)
_ARTEFACT_DELETE_OPTIONAL = (
    "body", "body_file", "frontmatter", "selector", "scope", "fix_links"
)
_NON_ARTEFACT_EDIT_OPS_OPTIONAL = (
    "body", "body_file", "frontmatter", "target", "selector", "scope"
)
_NON_ARTEFACT_DELETE_OPTIONAL = (
    "body", "body_file", "frontmatter", "selector", "scope"
)

_NON_ARTEFACT_RESOURCES = ("skill", "memory", "style", "template")

EDIT_SPECS: dict[tuple[str, str], Spec] = {
    # artefact — edit / append / prepend
    **{
        ("artefact", op): Spec(
            required_fields=("path",),
            optional_fields=_ARTEFACT_EDIT_OPS_OPTIONAL,
        )
        for op in ("edit", "append", "prepend")
    },
    # artefact — delete_section (target required)
    ("artefact", "delete_section"): Spec(
        required_fields=("path", "target"),
        optional_fields=_ARTEFACT_DELETE_OPTIONAL,
    ),
    # non-artefact resources — edit / append / prepend
    **{
        (resource, op): Spec(
            required_fields=("name",),
            optional_fields=_NON_ARTEFACT_EDIT_OPS_OPTIONAL,
        )
        for resource in _NON_ARTEFACT_RESOURCES
        for op in ("edit", "append", "prepend")
    },
    # non-artefact resources — delete_section (target required)
    **{
        (resource, "delete_section"): Spec(
            required_fields=("name", "target"),
            optional_fields=_NON_ARTEFACT_DELETE_OPTIONAL,
        )
        for resource in _NON_ARTEFACT_RESOURCES
    },
}
