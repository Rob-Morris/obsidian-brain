"""Shared spec-driven validation for MCP tools with discriminated field sets.

Tools that fan out into per-discriminator field sets (brain_create by resource,
brain_move by op) validate flat MCP kwargs against a Spec via validate_spec().
The validator rejects unknown extras and missing required fields, then returns
the validated dict with absent fields omitted (None/empty-string = absent).

Future tools (brain_read, brain_list, brain_edit) will follow this same pattern:
define a <TOOL>_SPECS dict keyed by discriminator, call _build_<tool>_params()
in server.py, and delegate to validate_spec() with the appropriate label/hint.
"""

from __future__ import annotations

from _resource_contract import CREATE_SPECS, EDIT_SPECS, LIST_SPECS, READ_SPECS, Spec


def _is_present(value) -> bool:
    """Treat None/empty-string as absent for flat MCP request validation."""
    return value not in (None, "")


def contract_hint(spec: Spec, label: str, *, suffix: str = "") -> str:
    """Format a "{label} requires: A, B. Optional: C, D." contract-hint string.

    *label* is the discriminator phrase, e.g. ``"resource='artefact'"`` or
    ``"resource='artefact' op='edit'"``. *suffix* is appended verbatim after
    the trailing period for tool-specific addenda (e.g. brain_read's hint to
    redirect to brain_list).
    """
    required = ", ".join(spec.required_fields) if spec.required_fields else "(none)"
    hint = f"{label} requires: {required}"
    if spec.optional_fields:
        hint += f". Optional: {', '.join(spec.optional_fields)}."
    else:
        hint += "."
    if suffix:
        hint += f" {suffix}"
    return hint


def create_contract_hint(resource: str) -> str:
    return contract_hint(CREATE_SPECS[resource], f"resource='{resource}'")


def read_contract_hint(resource: str) -> str:
    suffix = ""
    if resource == "workspace":
        suffix = "To list all workspaces, use brain_list(resource='workspace')."
    return contract_hint(READ_SPECS[resource], f"resource='{resource}'", suffix=suffix)


def list_contract_hint(resource: str) -> str:
    return contract_hint(LIST_SPECS[resource], f"resource='{resource}'")


def edit_contract_hint(resource: str, operation: str) -> str:
    return contract_hint(
        EDIT_SPECS[(resource, operation)],
        f"resource='{resource}' op='{operation}'",
    )


def validate_spec(
    spec: Spec,
    fields: dict,
    label: str,
    hint: str,
    field_term: str = "field",
) -> dict:
    """Validate *fields* against *spec*, returning only the accepted subset.

    Args:
        spec:       Required/optional field contract.
        fields:     Flat dict of all kwargs passed by the caller (including
                    absent/defaulted values).
        label:      Human-readable discriminator description, e.g.
                    "Move op 'archive'" or "Resource 'artefact'".
        hint:       Contract-hint string appended to error messages.
        field_term: Term used in error messages, e.g. "top-level field" or
                    "params field".

    Returns:
        Dict containing only the required and optional fields that are present.

    Raises:
        ValueError: If an extra field is present or a required field is absent.
    """
    allowed = set(spec.required_fields) | set(spec.optional_fields)

    extras = [
        name for name, value in fields.items()
        if name not in allowed and _is_present(value)
    ]
    if extras:
        raise ValueError(
            f"{label} does not accept {field_term} '{extras[0]}'. "
            f"{hint}"
        )

    missing = [
        name for name in spec.required_fields
        if not _is_present(fields.get(name))
    ]
    if missing:
        raise ValueError(
            f"{label} requires {field_term} '{missing[0]}'. "
            f"{hint}"
        )

    return {
        name: fields[name]
        for name in (*spec.required_fields, *spec.optional_fields)
        if _is_present(fields.get(name))
    }
