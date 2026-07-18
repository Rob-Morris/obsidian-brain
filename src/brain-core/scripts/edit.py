#!/usr/bin/env python3
"""
edit.py — Edit, append, or prepend to vault artefacts and _Config/ resources.

Validates paths against the compiled router, then modifies file content
with frontmatter preservation. Also provides artefact type conversion.

Usage:
    python3 edit.py edit --path "Wiki/my-page.md" --target ":body" --scope "section" --body "New body"
    python3 edit.py append --path "Wiki/my-page.md" --target "## Notes" --scope "body" --body "Appended text"
    python3 edit.py prepend --path "Wiki/my-page.md" --target ":body" --scope "intro" --body "Before existing"
    python3 edit.py edit --path "Wiki/my-page.md" --target "## Notes" --scope "body" --within "# API" --within-occurrence 2 --body "New body" --vault /path --json
"""

import argparse
import json
import os
import re
import sys

from _resource_contract import RESOURCE_KINDS
from _lifecycle.derived_cache_state import load_fresh_compiled_router
from _common import (
    SELF_TAG_PREFIXES,
    apply_terminal_status_folder,
    canonical_living_artefact_key,
    check_write_allowed,
    collect_headings,
    config_resource_rel_path,
    direct_child_entries,
    descendant_entries,
    descendant_payload,
    ensure_parent_tag,
    ensure_self_tag,
    ensure_tags_list,
    extract_title,
    find_vault_root,
    derive_distinctive_slug,
    HasDescendantsError,
    is_archived_path,
    is_valid_key,
    living_key_set,
    legacy_target_migration_error,
    make_artefact_key,
    make_temp_path,
    MutationLockError,
    public_mutation_error_message,
    naming_driver_fields,
    normalize_artefact_key,
    now_iso,
    ParentChainError,
    PartialApplyError,
    parent_chain_entries,
    parse_leading_frontmatter,
    parse_frontmatter,
    prune_vacated_owner_folders,
    read_file_content,
    replace_artefact_key_references,
    reconcile_fields_for_render,
    render_filename,
    render_filename_or_default,
    resolve_folder,
    resolve_artefact_key_entry,
    resolve_and_validate_folder,
    resolve_parent_reference,
    resolve_type,
    resolve_structural_target,
    scan_artefact_key_references,
    safe_write,
    serialize_frontmatter,
    StaleArtefactIndexError,
    RequestCycleError,
    parse_structural_anchor_line,
    unique_filename,
    validate_key,
    artefact_type_prefix,
    vault_mutation_lock,
)
from rename import move_and_update_links, preflight_move_set, rename_and_update_links
import fix_links as _fix_links
from _staging import finalise_staged_body, resolve_mutation_body


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OPERATION_LABELS = {
    "edit": "Edited",
    "append": "Appended",
    "prepend": "Prepended",
    "delete_section": "Deleted section from",
    "replace_text": "Replaced text in",
}

BODY_TARGET = ":body"

_VALID_SCOPES = {
    "body": {
        "edit": {"section", "intro"},
        "append": {"section", "intro"},
        "prepend": {"section", "intro"},
    },
    "heading": {
        "edit": {"section", "body", "intro", "heading"},
        "append": {"section", "body", "intro"},
        "prepend": {"section", "body", "intro"},
    },
    "callout": {
        "edit": {"section", "body", "header"},
        "append": {"section", "body"},
        "prepend": {"section", "body"},
    },
}

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _reject_leading_body_frontmatter(body, *, resource_label):
    """Reject full-document content where a body-only payload is required."""
    if not body:
        return

    if parse_leading_frontmatter(body, allow_leading_blank_lines=True) is not None:
        raise ValueError(
            f"{resource_label} body must not start with a frontmatter block. "
            "Pass frontmatter via the dedicated frontmatter field and body content separately."
        )

def _open_artefact(vault_root, router, path):
    """Validate, read, and parse an artefact. Returns (path, abs_path, fields, body, artefact)."""
    vault_root = str(vault_root)
    path, abs_path, fields, body, art = _read_open_path(vault_root, router, path)
    check_write_allowed(path)
    return path, abs_path, fields, body, art


def _line_count(text):
    """Count body lines for response summaries."""
    return len(text.splitlines()) if text else 0


def _result_payload(path, resolved_path, operation, old_body, new_body, *,
                    resolved=None, scope=None):
    """Build the standard result payload for body mutations."""
    payload = {
        "path": path,
        "resolved_path": resolved_path,
        "operation": operation,
        "old_body_line_count": _line_count(old_body),
        "new_body_line_count": _line_count(new_body),
    }
    if resolved and scope:
        payload["structural_target"] = {
            "kind": resolved["kind"],
            "raw": resolved["raw"],
            "scope": scope,
            "display": _describe_structural_target(resolved, scope),
        }
    return payload


def _structural_target_kind(target):
    """Infer the structural target kind from the public target string."""
    stripped = (target or "").strip()
    if not stripped:
        return None
    if stripped == BODY_TARGET:
        return "body"
    if stripped.startswith("[!"):
        return "callout"
    return "heading"


def _valid_scopes_for(kind, operation):
    return sorted(_VALID_SCOPES.get(kind, {}).get(operation, set()))


# Per-scope semantic meanings, per target kind. Co-located with `_VALID_SCOPES`
# so additions to either are reviewed together. Callers that render explanatory
# UX from this table (e.g. the MCP layer's rich error messages) import it here
# rather than redefining their own copy.
_SCOPE_MEANINGS = {
    "body": {
        "section": "the entire markdown body after frontmatter",
        "intro": "the lead paragraph(s) before the first heading",
    },
    "heading": {
        "section": "the heading line plus its body (the whole subtree)",
        "body": "the body under the heading (excludes the heading line)",
        "intro": "the content under the heading before its first child heading (the whole body when the heading has no children)",
        "heading": "the heading line itself (edit-only)",
    },
    "callout": {
        "section": "the whole callout (header line plus body)",
        "body": "the callout body (excludes the header line)",
        "header": "the callout header line (edit-only)",
    },
}


def _format_scope_help(kind, valid_scopes):
    """Render `scope='X' -> meaning` lines for one target kind."""
    meanings = _SCOPE_MEANINGS.get(kind, {})
    lines = []
    for scope_name in valid_scopes:
        meaning = meanings.get(scope_name)
        if meaning:
            lines.append(f"  scope='{scope_name}' -> {meaning}")
        else:
            lines.append(f"  scope='{scope_name}'")
    return "\n".join(lines)


def brain_edit_scope_description():
    """Shared MCP-facing description of the public scope contract."""
    return (
        "Mutable range inside target. Required for edit/append/prepend; not "
        "allowed for delete_section. ':body' -> 'section' (whole body) | "
        "'intro' (before first heading); heading -> 'section' (heading + "
        "subtree) | 'body' (content under heading) | 'intro' (before first "
        "child heading, else whole body) | 'heading' (line-only, edit-only); callout -> "
        "'section' (whole callout) | 'body' (content under header) | "
        "'header' (line-only, edit-only)."
    )


class ScopeValidationError(ValueError):
    """Base class for scope-validation errors with enriched wrapper messaging."""

    def __init__(self, *, operation, kind, valid_scopes):
        self.operation = operation
        self.kind = kind
        self.valid_scopes = valid_scopes
        super().__init__(self.summary_message())

    def summary_message(self):
        raise NotImplementedError

    def details_header(self):
        raise NotImplementedError

    def detailed_message(self):
        return f"{self.details_header()}\n{_format_scope_help(self.kind, self.valid_scopes)}"


class ScopeRequiredError(ScopeValidationError):
    """Raised when a structural edit/append/prepend is missing the scope parameter.

    Carries the structural fields (operation, target, kind, valid_scopes) so a
    caller — typically an LLM-facing wrapper such as the MCP layer — can render
    a richer, schema-truncation-resilient error message. The default str() form
    is still adequate for direct CLI / Python callers.
    """
    def __init__(self, operation, target, kind, valid_scopes):
        self.target = target
        super().__init__(operation=operation, kind=kind, valid_scopes=valid_scopes)

    def summary_message(self):
        return (
            f"{self.operation} with target='{self.target}' requires scope. "
            f"Valid scopes for {self.kind} targets: {', '.join(self.valid_scopes)}"
        )

    def details_header(self):
        return (
            f"{self.operation} with target='{self.target}' requires scope. "
            f"Valid scopes for {self.kind} targets:"
        )


class InvalidScopeError(ScopeValidationError):
    """Raised when scope is not valid for the resolved operation/target kind.

    Same wrapper-enrichment rationale as ScopeRequiredError.
    """
    def __init__(self, operation, scope, kind, valid_scopes):
        self.scope = scope
        super().__init__(operation=operation, kind=kind, valid_scopes=valid_scopes)

    def summary_message(self):
        return (
            f"scope='{self.scope}' is not valid for {self.operation} on {self.kind} targets. "
            f"Valid scopes: {', '.join(self.valid_scopes)}"
        )

    def details_header(self):
        return (
            f"scope='{self.scope}' is not valid for {self.operation} on {self.kind} targets. "
            "Valid scopes:"
        )


def _validate_request_contract(operation, body_present, frontmatter_changes=None,
                               target=None, selector=None, scope=None):
    """Validate the explicit target + selector + scope contract."""
    legacy_error = legacy_target_migration_error(target)
    if legacy_error is not None:
        raise legacy_error

    if operation == "delete_section":
        if scope is not None:
            raise ValueError("delete_section does not accept scope")
        if selector is not None and not target:
            raise ValueError("selector requires target")
        if not target:
            raise ValueError("delete_section requires a target heading or callout")
        if target == BODY_TARGET:
            raise ValueError(
                "target=':body' is only valid for edit, append, or prepend with "
                "scope='section' or scope='intro'. delete_section requires a heading "
                "or callout target."
            )
        return

    if selector is not None and not target:
        raise ValueError("selector requires target")
    if scope is not None and not target:
        raise ValueError("scope requires target")

    if target:
        kind = _structural_target_kind(target)
        valid = _valid_scopes_for(kind, operation)
        if scope is None:
            raise ScopeRequiredError(operation, target, kind, valid)
        if scope not in valid:
            raise InvalidScopeError(operation, scope, kind, valid)
        if (
            operation in {"append", "prepend"}
            and not body_present
            and not frontmatter_changes
        ):
            raise ValueError(
                f"{operation} with no body and no frontmatter changes is a no-op. "
                "Pass body content, frontmatter changes, or both."
            )
        return

    if body_present:
        raise ValueError(
            "Body mutations require explicit target and scope. "
            "For the full markdown body use target=':body', scope='section'. "
            "For the lead paragraph(s) before the first heading use target=':body', scope='intro'. "
            "To target a specific heading use target='## Heading' with scope='section' "
            "(heading + body) or 'body' (under heading)."
        )

    if not frontmatter_changes:
        raise ValueError(
            f"{operation} with no body and no frontmatter changes is a no-op. "
            "Pass body content, frontmatter changes, or both."
        )


def preflight_request_contract(operation, *, has_body=False,
                               frontmatter_changes=None, target=None,
                               selector=None, scope=None):
    """Cheap request-contract validation before staged body-file IO."""
    _validate_request_contract(
        operation,
        has_body,
        frontmatter_changes,
        target=target,
        selector=selector,
        scope=scope,
    )


def _merge_frontmatter(fields, changes, operation):
    """Merge frontmatter changes using operation-appropriate strategy.

    edit: overwrite all fields (set semantics).
    append/prepend: extend list fields with dedup, overwrite scalars.
    null: delete the field (all operations).

    Side-effect: sets ``statusdate`` to today when *status* actually changes.
    """
    if not changes:
        return
    # Auto-set statusdate when status actually changes value (not on deletion)
    if "status" in changes and changes["status"] is not None and changes["status"] != fields.get("status"):
        fields["statusdate"] = now_iso()[:10]
    for key, value in changes.items():
        if value is None:
            fields.pop(key, None)
        elif operation != "edit" and isinstance(value, list) and isinstance(fields.get(key), list):
            fields[key].extend(v for v in value if v not in fields[key])
        else:
            fields[key] = value


def _save_artefact(abs_path, fields, new_body, vault_root):
    """Set modified timestamp, serialize, and write."""
    fields["modified"] = now_iso()
    new_content = serialize_frontmatter(fields, body=new_body)
    safe_write(abs_path, new_content, bounds=vault_root)


# ---------------------------------------------------------------------------
# Body operation helpers (shared by artefact and resource paths)
# ---------------------------------------------------------------------------

def _describe_structural_target(resolved, scope):
    """Render a user-facing description of the resolved structural range."""
    if resolved["kind"] == "body":
        return f"body {scope}"
    if resolved["kind"] == "heading":
        label = "heading line" if scope == "heading" else f"heading {scope}"
    else:
        label = "callout header" if scope == "header" else f"callout {scope}"
    return f"{label}: {resolved['display_path']}"


def _resolve_scope_span(resolved, scope):
    """Return the concrete ``(start, end)`` span for a resolved scope."""
    try:
        return resolved["ranges"][scope]
    except KeyError as exc:
        raise ValueError(
            f"scope='{scope}' is not available for {resolved['kind']} targets"
        ) from exc


def _validate_single_structural_line(body, kind, label):
    """Validate a single heading or callout structural line."""
    line = body.strip("\n")
    if not line or "\n" in line:
        raise ValueError(f"{label} replacement must be a single {kind} line")
    anchor = parse_structural_anchor_line(line)
    if anchor is None or anchor["kind"] != kind:
        raise ValueError(f"{label} replacement must be a valid {kind} line")


def _validate_heading_section_replacement(body, resolved):
    """Whole heading-section replacement must begin with a heading line.

    Also rejects bodies whose final heading equals the original section
    boundary heading — splicing such a body would duplicate that boundary
    immediately after itself.
    """
    if not body:
        raise ValueError("scope='section' for heading targets cannot be empty")
    anchor = parse_structural_anchor_line(body)
    if anchor is None or anchor["kind"] != "heading":
        raise ValueError(
            "scope='section' for heading targets must begin with a heading line"
        )

    next_boundary_raw = resolved["next_boundary_raw"]
    if next_boundary_raw is None:
        return

    headings = collect_headings(body)
    assert headings, "body passed initial heading-line check; collect_headings must find at least one"
    _h_start, _h_level, _h_text, h_raw = headings[-1]
    if h_raw == next_boundary_raw:
        raise ValueError(
            f"scope='section' replacement body's final heading '{h_raw}' is the same "
            "as the next section boundary heading. Splicing this body would "
            "duplicate that heading. Either drop the trailing heading from the "
            "body, or widen the target so multiple sections are replaced together."
        )


def _validate_callout_section_replacement(body):
    """Whole callout-section replacement must begin with a callout header line."""
    if not body:
        raise ValueError("scope='section' for callout targets cannot be empty")
    anchor = parse_structural_anchor_line(body)
    if anchor is None or anchor["kind"] != "callout":
        raise ValueError(
            "scope='section' for callout targets must begin with a callout header line"
        )


def _validate_callout_body_payload(body):
    """Callout body scope uses raw quoted markdown lines."""
    if not body:
        return
    for line in body.splitlines():
        if not line.lstrip().startswith(">"):
            raise ValueError(
                "scope='body' for callout targets expects raw quoted markdown lines "
                "beginning with '>'"
            )


def _validate_heading_body_payload(body, resolved, scope):
    """Reject accidental section-style payloads for heading body/intro edits."""
    if not body:
        return
    anchor = parse_structural_anchor_line(body)
    if anchor is None or anchor["kind"] != "heading":
        return
    target_level = resolved["level"]
    if anchor["raw"] == resolved["raw"] or anchor["level"] <= target_level:
        raise ValueError(
            f"scope='{scope}' for heading target '{resolved['display_path']}' only "
            "replaces the content below the heading. Use scope='section' to replace "
            "the heading line too."
        )


def _validate_edit_payload(body, resolved, scope):
    """Validate payload shape for scope-specific edit replacements."""
    if resolved["kind"] == "heading":
        if scope == "section":
            _validate_heading_section_replacement(body, resolved)
        elif scope == "heading":
            _validate_single_structural_line(body, "heading", "Heading")
        elif scope in {"body", "intro"}:
            _validate_heading_body_payload(body, resolved, scope)
        return

    if resolved["kind"] == "callout":
        if scope == "section":
            _validate_callout_section_replacement(body)
        elif scope == "header":
            _validate_single_structural_line(body, "callout", "Callout header")
        elif scope == "body":
            _validate_callout_body_payload(body)
        return


def _validate_insert_payload(body, resolved, scope):
    """Validate payloads for append/prepend operations."""
    if resolved["kind"] == "callout" and scope == "body":
        _validate_callout_body_payload(body)
    elif resolved["kind"] == "heading" and scope in {"body", "intro"}:
        _validate_heading_body_payload(body, resolved, scope)


def _prepare_boundary_safe_text(existing_body, start, end, text):
    """Insert or replace ``text`` without merging with adjacent lines.

    Also restores the body-ends-with-newline invariant when the spliced text
    lands at end-of-body — otherwise EOF replacements would write files
    without a trailing newline.
    """
    if not text:
        return text
    prepared = text
    if start > 0 and existing_body[start - 1] != "\n" and not prepared.startswith("\n"):
        prepared = "\n" + prepared
    if end == len(existing_body):
        needs_trailing = True
    else:
        needs_trailing = existing_body[end] != "\n"
    if needs_trailing and not prepared.endswith("\n"):
        prepared = prepared + "\n"
    return prepared


def _replace_range(existing_body, span, body):
    """Replace an explicit character range with ``body``."""
    start, end = span
    replacement = _prepare_boundary_safe_text(existing_body, start, end, body)
    return existing_body[:start] + replacement + existing_body[end:]


def _insert_at(existing_body, pos, body):
    """Insert text at ``pos`` while avoiding merged structural lines."""
    insertion = _prepare_boundary_safe_text(existing_body, pos, pos, body)
    return existing_body[:pos] + insertion + existing_body[pos:]


def _delete_range(existing_body, span):
    """Delete a structural range and collapse the surrounding blank-line seam."""
    start, end = span
    prefix = existing_body[:start].rstrip("\n")
    suffix = existing_body[end:]
    if prefix and suffix:
        return prefix + "\n\n" + suffix
    if prefix:
        return prefix + "\n"
    return suffix.lstrip("\n")


def _apply_edit(existing_body, body, resolved, scope):
    """Apply a scope-aware edit to a body."""
    _validate_edit_payload(body, resolved, scope)
    span = _resolve_scope_span(resolved, scope)
    replacement = body
    if (
        replacement == ""
        and resolved["kind"] == "heading"
        and scope in {"body", "intro"}
        and span[1] < len(existing_body)
        and existing_body[span[1]] != "\n"
    ):
        replacement = "\n"
    return _replace_range(existing_body, span, replacement)


def _apply_append(existing_body, content, resolved, scope):
    """Append content to the resolved structural range."""
    if not content:
        return existing_body
    _validate_insert_payload(content, resolved, scope)
    _start, end = _resolve_scope_span(resolved, scope)
    return _insert_at(existing_body, end, content)


def _apply_prepend(existing_body, content, resolved, scope):
    """Prepend content to the resolved structural range."""
    if not content:
        return existing_body
    _validate_insert_payload(content, resolved, scope)
    start, _end = _resolve_scope_span(resolved, scope)
    return _insert_at(existing_body, start, content)


def _apply_delete_section(existing_body, resolved):
    """Delete the resolved heading-owned section or callout block."""
    return _delete_range(existing_body, _resolve_scope_span(resolved, "section"))


def _replace_text_matches(text, old_text):
    """Return non-overlapping exact-match start offsets."""
    matches = []
    start = 0
    while True:
        found = text.find(old_text, start)
        if found < 0:
            return matches
        matches.append(found)
        start = found + len(old_text)


def _apply_replace_text(existing_body, old_text, new_text, *, target=None,
                        selector=None, scope=None, match_occurrence=None,
                        replace_all=False):
    """Apply a fail-safe exact-text replacement and return result metadata."""
    if not old_text:
        raise ValueError("replace_text requires non-empty old_text")
    if new_text is None:
        raise ValueError("replace_text requires new_text (use an empty string to delete)")
    if match_occurrence is not None and match_occurrence < 1:
        raise ValueError("match_occurrence must be a positive integer")
    if replace_all and match_occurrence is not None:
        raise ValueError("replace_all and match_occurrence are mutually exclusive")
    if bool(target) != bool(scope):
        raise ValueError("replace_text target and scope must be supplied together")
    if selector and not target:
        raise ValueError("replace_text selector requires target and scope")

    resolved = None
    start, end = 0, len(existing_body)
    if target:
        resolved = resolve_structural_target(existing_body, target, selector=selector)
        valid = _valid_scopes_for(resolved["kind"], "edit")
        if scope not in valid:
            raise InvalidScopeError("replace_text", scope, resolved["kind"], valid)
        start, end = _resolve_scope_span(resolved, scope)

    selected = existing_body[start:end]
    matches = _replace_text_matches(selected, old_text)
    count = len(matches)
    if count == 0:
        target_label = _describe_structural_target(resolved, scope) if resolved else "body"
        raise ValueError(f"replace_text found no exact match in {target_label}")

    if replace_all:
        replaced = selected.replace(old_text, new_text)
        replacement_count = count
    else:
        if match_occurrence is None and count != 1:
            raise ValueError(
                f"replace_text found {count} exact matches; pass match_occurrence "
                "or set replace_all=true"
            )
        occurrence = match_occurrence or 1
        if occurrence > count:
            raise ValueError(
                f"replace_text match_occurrence={occurrence} exceeds {count} exact matches"
            )
        match_start = matches[occurrence - 1]
        match_end = match_start + len(old_text)
        replaced = selected[:match_start] + new_text + selected[match_end:]
        replacement_count = 1

    new_body = existing_body[:start] + replaced + existing_body[end:]
    return new_body, resolved, replacement_count, count


def _apply_body_operation(existing_body, operation, body, *, target=None,
                          selector=None, scope=None):
    """Apply the requested body mutation and return ``(new_body, resolved)``."""
    if operation == "delete_section":
        resolved = resolve_structural_target(existing_body, target, selector=selector)
        return _apply_delete_section(existing_body, resolved), resolved

    if not target:
        return existing_body, None

    if operation in {"append", "prepend"} and not body:
        resolve_structural_target(existing_body, target, selector=selector)
        return existing_body, None

    resolved = resolve_structural_target(existing_body, target, selector=selector)

    if operation == "edit":
        return _apply_edit(existing_body, body, resolved, scope), resolved
    if operation == "append":
        return _apply_append(existing_body, body, resolved, scope), resolved
    if operation == "prepend":
        return _apply_prepend(existing_body, body, resolved, scope), resolved

    raise ValueError(f"Unknown operation '{operation}'")


# ---------------------------------------------------------------------------
# Resource-aware editing (Phase 5)
# ---------------------------------------------------------------------------

EDITABLE_RESOURCES = RESOURCE_KINDS

_LIFECYCLE_FIELD_COMMANDS = {
    "parent": "brain_reparent",
    "key": "brain_set_key",
    "status": "brain_set_status",
}


def handler_owned_frontmatter_fields(art):
    """Map protected metadata fields to their explicit public command."""
    result = dict(_LIFECYCLE_FIELD_COMMANDS)
    for field in naming_driver_fields((art or {}).get("naming")):
        result.setdefault(field, "brain_set_naming_field")
    return result


def _reject_handler_owned_frontmatter(art, changes):
    if not changes:
        return
    handlers = handler_owned_frontmatter_fields(art)
    protected = [field for field in changes if field in handlers]
    if not protected:
        return
    field = protected[0]
    raise ValueError(
        f"frontmatter.{field} is lifecycle-owned and cannot be changed with "
        f"brain_edit. Use {handlers[field]} so Brain can preflight and preserve "
        "derived paths, ownership, links, and indexes."
    )


def update_lifecycle_field(vault_root, router, path, field, value):
    """Apply one explicit lifecycle field change through the invariant engine."""
    resolved_path, _abs_path, fields, _body, art = _open_artefact(
        vault_root, router, path
    )
    handlers = handler_owned_frontmatter_fields(art)
    if field not in handlers:
        raise ValueError(
            f"frontmatter.{field} is not lifecycle-owned for '{resolved_path}'"
        )
    if field == "status":
        if value in (None, ""):
            raise ValueError("status cannot be empty; pass a valid lifecycle status")
        valid = ((art.get("frontmatter") or {}).get("status_enum")) or []
        if valid and value not in valid:
            raise ValueError(
                f"Invalid status '{value}' for {art.get('frontmatter_type') or art.get('key')}. "
                f"Valid statuses: {', '.join(valid)}"
            )
    if field == "key" and value in (None, ""):
        raise ValueError("key cannot be empty")
    naming = art.get("naming") or {}
    if field in naming_driver_fields(naming):
        candidate_fields = dict(fields)
        if value is None:
            candidate_fields.pop(field, None)
        else:
            candidate_fields[field] = value
        # Naming-driven mutations must be proven valid before apply_to_artefact
        # persists metadata or performs any derived move.
        _render_existing_artefact_path(
            vault_root, router, art, resolved_path, candidate_fields
        )
    result = apply_to_artefact(
        "edit",
        vault_root,
        router,
        resolved_path,
        "",
        frontmatter_changes={field: value},
    )
    result["lifecycle_field"] = field
    result["old_value"] = fields.get(field)
    result["new_value"] = value
    result["command"] = handlers[field]
    return result


def plan_parent_projection_repair(vault_root, router, path, *, reference_index=None):
    """Plan filesystem moves that reconcile a valid authoritative parent field."""
    path, _abs_path, fields, _body, art = _open_artefact(vault_root, router, path)
    parent = normalize_artefact_key(fields.get("parent"))
    if not parent:
        raise ValueError(
            f"'{path}' has no valid parent metadata; structure cannot be used to infer it"
        )
    expected_path, _rendered_fields = _render_existing_artefact_path(
        vault_root, router, art, path, fields
    )
    moves = [{"source": path, "dest": expected_path}]
    if art.get("classification") == "living":
        key_value = fields.get("key")
        if not is_valid_key(key_value):
            raise ValueError(f"'{path}' has no valid living key")
        artefact_key = make_artefact_key(artefact_type_prefix(art), key_value)
        moves.extend(
            _plan_descendant_moves(
                vault_root,
                router,
                artefact_key,
                router,
                [],
                operation="parent projection repair",
            )
        )
        moves.extend(
            _plan_temporal_reference_moves(
                vault_root,
                router,
                artefact_key,
                router,
                [],
                operation="parent projection repair",
                reference_index=reference_index,
            )
        )
    unique = []
    seen = set()
    for move in moves:
        pair = (move["source"], move["dest"])
        if pair in seen or move["source"] == move["dest"]:
            continue
        seen.add(pair)
        unique.append(move)
    preflight_move_set(vault_root, unique)
    return {
        "path": path,
        "parent": parent,
        "moves": unique,
        "files_affected": len(unique),
    }


def edit_resource(vault_root, router, resource="artefact", operation="edit",
                  path=None, name=None, body="", frontmatter_changes=None,
                  target=None, selector=None, scope=None, fix_links=False,
                  file_index=None, old_text=None, new_text=None,
                  match_occurrence=None, replace_all=False):
    """Edit a vault resource. Dispatches to the appropriate handler.

    For artefacts: delegates to existing edit/append/prepend/delete_section functions.
    For other resources: resolves path via _Config/ conventions, applies the
    same edit operations without artefact-specific behavior (no terminal status
    auto-move, no modified timestamp injection).

    Args:
        vault_root: Absolute path to the vault root.
        router: Compiled router dict.
        resource: Resource kind — one of: artefact, skill, memory, style, template.
        operation: "edit", "append", "prepend", or "delete_section".
        path: Relative path (artefacts only).
        name: Resource name (non-artefact resources only).
        body: Content for the operation.
        frontmatter_changes: Optional dict of frontmatter field changes.
        target: Optional body, heading, or callout target.
        selector: Optional duplicate/ancestor disambiguation object.
        scope: Optional mutable range within the resolved structural target.
        file_index: Optional pre-built vault file index (dict). When supplied,
                    the wikilink-warning step skips ``build_vault_file_index``.
                    Pass ``None`` (default) for legacy behaviour (vault walk).

    Returns:
        Dict with path and operation.
    """
    vault_root = str(vault_root)

    if resource == "artefact":
        if not path:
            raise ValueError("path is required when resource='artefact'")
        if operation not in {"edit", "append", "prepend", "delete_section", "replace_text"}:
            raise ValueError(f"Unknown operation '{operation}'")
        if operation == "replace_text":
            if frontmatter_changes:
                raise ValueError("replace_text does not accept frontmatter changes")
            result = replace_text_in_artefact(
                vault_root,
                router,
                path,
                old_text=old_text,
                new_text=new_text,
                target=target,
                selector=selector,
                scope=scope,
                match_occurrence=match_occurrence,
                replace_all=replace_all,
            )
        else:
            if frontmatter_changes:
                body, scope = _prepare_artefact_operation(
                    operation,
                    body,
                    frontmatter_changes,
                    target,
                    selector,
                    scope,
                )
                opened = _open_artefact(
                    vault_root, router, path
                )
                art = opened[4]
                _reject_handler_owned_frontmatter(art, frontmatter_changes)
                result = _apply_to_open_artefact(
                    operation,
                    vault_root,
                    router,
                    opened,
                    body,
                    frontmatter_changes=frontmatter_changes,
                    target=target,
                    selector=selector,
                    scope=scope,
                )
            else:
                result = apply_to_artefact(
                    operation, vault_root, router, path, body,
                    target=target, selector=selector, scope=scope,
                )
        _fix_links.attach_wikilink_warnings(vault_root, result, apply_fixes=fix_links, file_index=file_index)
        return result

    if resource not in EDITABLE_RESOURCES:
        raise ValueError(
            f"Resource '{resource}' is not editable via brain_edit. "
            f"Editable resources: {', '.join(EDITABLE_RESOURCES)}"
        )

    if not name:
        raise ValueError(f"brain_edit(resource='{resource}') requires name.")

    # Resolve and read config resource
    rel_path = config_resource_rel_path(router, resource, name)
    check_write_allowed(rel_path)
    abs_path = os.path.join(vault_root, rel_path)
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        raise FileNotFoundError(
            f"{resource.capitalize()} '{name}' not found at {rel_path}"
        ) from None
    fields, existing_body = parse_frontmatter(content)

    if operation == "replace_text":
        if frontmatter_changes:
            raise ValueError("replace_text does not accept frontmatter changes")
        new_body, resolved, replacement_count, match_count = _apply_replace_text(
            existing_body,
            old_text,
            new_text,
            target=target,
            selector=selector,
            scope=scope,
            match_occurrence=match_occurrence,
            replace_all=replace_all,
        )
        safe_write(
            abs_path,
            serialize_frontmatter(fields, body=new_body),
            bounds=vault_root,
        )
        result = _result_payload(
            rel_path,
            rel_path,
            operation,
            existing_body,
            new_body,
            resolved=resolved,
            scope=scope,
        )
        result["match_count"] = match_count
        result["replacement_count"] = replacement_count
        return result

    _validate_request_contract(
        operation,
        bool(body),
        frontmatter_changes,
        target,
        selector,
        scope,
    )
    _reject_leading_body_frontmatter(
        body, resource_label=f"{resource.capitalize()} resource"
    )
    fm_mode = "edit" if operation in ("edit", "delete_section") else operation
    _merge_frontmatter(fields, frontmatter_changes, fm_mode)

    new_body, resolved = _apply_body_operation(
        existing_body,
        operation,
        body,
        target=target,
        selector=selector,
        scope=scope,
    )

    # Save without artefact-specific behavior (no modified auto-set, no status move)
    new_content = serialize_frontmatter(fields, body=new_body)
    safe_write(abs_path, new_content, bounds=vault_root)

    result_scope = "section" if operation == "delete_section" and resolved else scope
    return _result_payload(
        rel_path,
        rel_path,
        operation,
        existing_body,
        new_body,
        resolved=resolved,
        scope=result_scope,
    )


def _replace_exact_tag(fields, old_tag, new_tag=None):
    """Replace or remove an exact tag match, preserving order."""
    tags = ensure_tags_list(fields)
    updated = []
    changed = False
    for tag in tags:
        if tag != old_tag:
            updated.append(tag)
            continue
        changed = True
        if new_tag and new_tag not in updated:
            updated.append(new_tag)
    if new_tag and new_tag not in updated:
        updated.append(new_tag)
        changed = True
    fields["tags"] = updated
    return changed


def _derive_title_from_path(art, fields, path):
    """Resolve a human title for filename rendering."""
    title = fields.get("title")
    if title:
        return title
    stem = os.path.splitext(os.path.basename(path))[0]
    return extract_title(art.get("naming"), fields, stem) or stem


def _render_existing_artefact_path(vault_root, router, art, path, fields):
    """Render the canonical path for an existing artefact from its fields."""
    current_basename = os.path.basename(path)
    abs_path = os.path.join(vault_root, path)
    title = _derive_title_from_path(art, fields, path)
    rendered_fields = dict(fields)
    reconcile_fields_for_render(rendered_fields, art, abs_path, current_basename)
    folder = resolve_folder(
        art,
        parent=normalize_artefact_key(rendered_fields.get("parent")),
        fields=rendered_fields,
        router=router,
    )
    folder = apply_terminal_status_folder(folder, art, rendered_fields)
    basename = render_filename_or_default(art.get("naming"), title, rendered_fields)
    return os.path.join(folder, basename), rendered_fields


def _ensure_free_artefact_key(vault_root, router, art, key, *, exclude_path=None):
    """Fail if ``key`` is already used by another artefact of this type."""
    existing = living_key_set(vault_root, router, art, exclude_path=exclude_path)
    if key in existing:
        raise ValueError(f"KEY_TAKEN: key '{key}' is already used")


def _choose_living_key(vault_root, router, art, title, key=None, *, exclude_path=None):
    """Return a collision-free living key for ``art``."""
    existing = living_key_set(vault_root, router, art, exclude_path=exclude_path)
    if key is not None:
        key = validate_key(key)
        if key in existing:
            raise ValueError(f"KEY_TAKEN: key '{key}' is already used")
        return key
    return derive_distinctive_slug(title, existing)


def _normalise_ownership_changes(vault_root, router, art, frontmatter_changes):
    """Canonicalise key and parent changes before merging frontmatter."""
    if not frontmatter_changes:
        return frontmatter_changes

    changes = dict(frontmatter_changes)
    classification = art.get("classification")

    if "key" in changes:
        if classification != "living":
            raise ValueError("key changes only apply to living artefacts")
        if changes["key"] in (None, ""):
            raise ValueError("key cannot be removed from a living artefact")
        changes["key"] = validate_key(changes["key"])

    if "parent" in changes:
        if changes["parent"] in (None, ""):
            changes["parent"] = None
        else:
            resolved_parent, _entry = resolve_parent_reference(
                vault_root, router, changes["parent"]
            )
            changes["parent"] = resolved_parent

    return changes


def _router_with_pending_ownership(router, old_key, new_key, entry):
    """Return a router view for in-flight owner key/parent updates."""
    updated_router = dict(router)
    artefact_index = {
        key: dict(value)
        for key, value in (router.get("artefact_index") or {}).items()
    }
    if old_key:
        artefact_index.pop(old_key, None)
    if new_key:
        artefact_index[new_key] = dict(entry)
    if old_key and old_key != new_key:
        for value in artefact_index.values():
            if normalize_artefact_key(value.get("parent")) == old_key:
                value["parent"] = new_key
    updated_router["artefact_index"] = artefact_index
    return updated_router

def _plan_reference_mutation(
    vault_root,
    router,
    old_key,
    new_key,
    *,
    skip_paths=None,
    operation,
):
    """Plan canonical key reference rewrites without moving files."""
    if not old_key or old_key == new_key:
        return []

    skip_paths = set(skip_paths or [])
    operations = []
    for ref in scan_artefact_key_references(vault_root, router, old_key):
        rel_path = ref["path"]
        if rel_path in skip_paths:
            continue
        content = read_file_content(vault_root, rel_path)
        if content.startswith("Error:"):
            _raise_stale_index_missing(rel_path, operation)
        fields, body = parse_frontmatter(content)
        if not replace_artefact_key_references(fields, old_key, new_key):
            continue
        _resolved, art = resolve_and_validate_folder(vault_root, router, rel_path)
        operations.append(
            {
                "path": rel_path,
                "fields": fields,
                "body": body,
                "art": art,
                "parent_reference": bool(ref.get("parent")),
            }
        )

    return operations


def _write_frontmatter_mutations(vault_root, operations, *, operation):
    """Write planned frontmatter mutations and report the committed set."""
    written = []
    for op in operations:
        try:
            safe_write(
                os.path.join(vault_root, op["path"]),
                serialize_frontmatter(op["fields"], body=op["body"]),
                bounds=vault_root,
            )
        except OSError as exc:
            if not written:
                raise OSError(
                    f"{operation} failed before writing {op['path']}: {exc}"
                ) from exc
            raise PartialApplyError(
                f"{operation} partially applied — "
                f"files written {written}, failed at {op['path']}: {exc}"
            ) from exc
        written.append(op["path"])
    return written


def _raise_stale_index_missing(rel_path, operation):
    raise StaleArtefactIndexError(
        f"{operation} found indexed artefact {rel_path} missing on disk; "
        "materialise cloud placeholder files if needed"
    )


def _raise_unindexed_parent_reference(rel_path, old_key, operation):
    raise StaleArtefactIndexError(
        f"{operation} found a parent reference to {old_key} in {rel_path}, "
        "but that child is absent from the compiled living index"
    )


def _validate_preserved_parent(router, parent):
    if parent:
        parent_chain_entries(router, parent)


def _validate_not_parented_to_descendant(router, source_key, target_parent, operation, *, aliases=()):
    target_key = normalize_artefact_key(target_parent)
    if not source_key or not target_key:
        return
    forbidden_self_keys = {normalize_artefact_key(source_key)}
    forbidden_self_keys.update(
        key for key in (normalize_artefact_key(alias) for alias in aliases) if key
    )
    if target_key in forbidden_self_keys:
        raise RequestCycleError(
            f"{operation} would make {source_key} parent itself via {target_key}"
        )
    for entry in descendant_entries(router, source_key):
        if entry.get("artefact_key") == target_key:
            raise RequestCycleError(
                f"{operation} would make {source_key} a child of descendant {target_key}"
            )


def _plan_descendant_moves(
    vault_root,
    router,
    old_key,
    mutation_router,
    reference_ops,
    *,
    operation,
):
    reference_by_path = {op["path"]: op for op in reference_ops}
    moves = []
    if not old_key:
        return moves
    index = router.get("artefact_index") or {}
    indexed_descendants = descendant_entries(router, old_key)
    indexed_descendant_paths = {entry["path"] for entry in indexed_descendants}
    for op in reference_ops:
        is_living_parent_ref = (
            op.get("parent_reference")
            and (op.get("art") or {}).get("classification") == "living"
        )
        if is_living_parent_ref and op["path"] not in indexed_descendant_paths:
            _raise_unindexed_parent_reference(op["path"], old_key, operation)
    for entry in indexed_descendants:
        rel_path = entry["path"]
        op = reference_by_path.get(rel_path)
        if op is not None:
            fields = op["fields"]
            desc_art = op["art"]
        else:
            content = read_file_content(vault_root, rel_path)
            if content.startswith("Error:"):
                _raise_stale_index_missing(rel_path, operation)
            fields, _body = parse_frontmatter(content)
            _resolved, desc_art = resolve_and_validate_folder(
                vault_root, router, rel_path
            )
        desc_path, desc_fields = _render_existing_artefact_path(
            vault_root, mutation_router, desc_art, rel_path, fields
        )
        if op is not None:
            op["fields"] = desc_fields
        moves.append({"source": rel_path, "dest": desc_path})
    return moves


def _plan_temporal_reference_moves(
    vault_root,
    router,
    affected_key,
    mutation_router,
    reference_ops,
    *,
    operation,
    reference_index=None,
):
    """Plan owner-scope relocation for temporal children of an ownership edit."""
    if not affected_key:
        return []
    reference_by_path = {op["path"]: op for op in reference_ops}
    moves = []
    references = (
        reference_index.get(affected_key, [])
        if reference_index is not None
        else scan_artefact_key_references(vault_root, router, affected_key)
    )
    for ref in references:
        if not ref.get("parent"):
            continue
        rel_path = ref["path"]
        op = reference_by_path.get(rel_path)
        if op is not None:
            fields = op["fields"]
            art = op.get("art") or {}
        else:
            content = read_file_content(vault_root, rel_path)
            if content.startswith("Error:"):
                _raise_stale_index_missing(rel_path, operation)
            fields, _body = parse_frontmatter(content)
            _resolved, art = resolve_and_validate_folder(vault_root, router, rel_path)
        if art.get("classification") != "temporal":
            continue
        dest_path = _temporal_month_relocation_path(
            mutation_router,
            rel_path,
            art,
            fields,
        )
        if dest_path != rel_path:
            moves.append({"source": rel_path, "dest": dest_path})
    return moves


def _maybe_restructure_living_ownership(vault_root, router, path, art, old_fields, new_fields, new_body):
    """Rewrite key references and move the affected subtree when ownership changes.

    This operation shares the move-set engine's non-atomic contract: frontmatter
    writes happen before the batch move, and a later move failure propagates
    with the move-set engine's partial-apply context.
    """
    old_key_value = old_fields.get("key")
    new_key_value = new_fields.get("key")
    old_parent = normalize_artefact_key(old_fields.get("parent"))
    new_parent = normalize_artefact_key(new_fields.get("parent"))
    type_prefix = artefact_type_prefix(art)
    old_key = (
        make_artefact_key(type_prefix, old_key_value)
        if is_valid_key(old_key_value)
        else None
    )
    new_key = (
        make_artefact_key(type_prefix, new_key_value)
        if is_valid_key(new_key_value)
        else None
    )

    ownership_changed = old_key != new_key or old_parent != new_parent
    if not ownership_changed:
        return path, False
    _validate_not_parented_to_descendant(
        router,
        old_key,
        new_parent,
        "edit",
        aliases=[new_key],
    )

    if old_key and old_key != new_key:
        replacement = new_key if type_prefix in SELF_TAG_PREFIXES else None
        _replace_exact_tag(new_fields, old_key, replacement)
    elif new_key:
        ensure_self_tag(new_fields, type_prefix, new_key_value)

    if new_key:
        _ensure_free_artefact_key(
            vault_root, router, art, new_key_value, exclude_path=path
        )

    pending_entry = {
        "path": path,
        "type": art.get("frontmatter_type", art.get("type")),
        "classification": "living",
        "type_key": art.get("key"),
        "type_prefix": type_prefix,
        "key": new_key_value,
        "parent": new_parent,
    }
    mutation_router = _router_with_pending_ownership(
        router, old_key, new_key, pending_entry
    )
    new_path, rendered_fields = _render_existing_artefact_path(
        vault_root, mutation_router, art, path, new_fields
    )
    if new_key:
        mutation_router["artefact_index"][new_key]["path"] = new_path

    reference_ops = []
    if old_key and old_key != new_key:
        reference_ops = _plan_reference_mutation(
            vault_root,
            router,
            old_key,
            new_key,
            skip_paths={path},
            operation="edit",
        )

    moves = [{"source": path, "dest": new_path}]
    moves.extend(
        _plan_descendant_moves(
            vault_root,
            router,
            old_key,
            mutation_router,
            reference_ops,
            operation="edit",
        )
    )
    moves.extend(
        _plan_temporal_reference_moves(
            vault_root,
            router,
            old_key,
            mutation_router,
            reference_ops,
            operation="edit",
        )
    )

    preflight_move_set(vault_root, moves)
    rendered_fields["modified"] = now_iso()
    write_ops = [
        {"path": path, "fields": rendered_fields, "body": new_body},
        *reference_ops,
    ]
    _write_frontmatter_mutations(
        vault_root, write_ops, operation="ownership mutation"
    )
    real_moves = [move for move in moves if move["source"] != move["dest"]]
    if real_moves:
        try:
            result = move_and_update_links(vault_root, real_moves)
        except PartialApplyError as exc:
            metadata_written = [op["path"] for op in write_ops]
            raise PartialApplyError(
                "ownership mutation partially applied — "
                f"metadata files written {metadata_written}; move failure: {exc}"
            ) from exc
        prune_vacated_owner_folders(
            vault_root,
            [move["source"] for move in result.get("applied", [])],
            router,
        )
    if new_path != path:
        return new_path, True
    return path, True


def _maybe_status_move(vault_root, path, terminal_statuses, frontmatter_changes):
    """If frontmatter_changes sets a terminal status, move file to +Status/ folder.

    Returns new path if moved, or original path if not.
    """
    if not frontmatter_changes or "status" not in frontmatter_changes:
        return path

    if not terminal_statuses:
        return path

    if is_archived_path(path):
        return path  # _Archive/ is a manual location; auto-move does not apply

    new_status = frontmatter_changes["status"]
    parent_dir = os.path.dirname(path)
    filename = os.path.basename(path)
    parent_name = os.path.basename(parent_dir)

    if new_status in terminal_statuses:
        # Terminal → move into +Status/ folder
        status_folder = f"+{new_status.capitalize()}"
        if parent_name == status_folder:
            return path  # already in correct folder
        # If already inside a +Status/ folder, resolve relative to grandparent
        # to avoid nesting (e.g. +Implemented/+Deprecated/ → +Deprecated/)
        base_dir = os.path.dirname(parent_dir) if parent_name.startswith("+") else parent_dir
        new_path = os.path.join(base_dir, status_folder, filename)
    elif parent_name.startswith("+"):
        # Non-terminal and currently in a +Status/ folder → move out
        grandparent = os.path.dirname(parent_dir)
        new_path = os.path.join(grandparent, filename)
    else:
        return path

    rename_and_update_links(vault_root, path, new_path)

    # Clean up empty +Status/ folder after revive
    if parent_name.startswith("+"):
        abs_old_dir = os.path.join(vault_root, parent_dir)
        try:
            os.rmdir(abs_old_dir)  # only removes if empty
        except OSError:
            pass

    return new_path


def _apply_status_change_hooks(fields, old_fields, art):
    """Apply ``{status}_at`` convention and ``on_status_change`` hooks.

    When ``status`` changes value, set ``{status}_at = now()`` (ISO date) for
    the new status unless the type's ``on_status_change`` hook overrides the
    field name. Also backfills ``{status}_at`` when a status is observed for
    the first time without its timestamp (reconcile path).
    """
    new_status = fields.get("status")
    old_status = (old_fields or {}).get("status")
    if not new_status:
        return
    changed = new_status != old_status
    if not changed:
        return
    today = now_iso()[:10]
    hook = ((art or {}).get("on_status_change") or {}).get(new_status) or {}
    set_map = hook.get("set") or {}
    for field_name, raw_value in set_map.items():
        if fields.get(field_name):
            continue
        value = today if str(raw_value).lower() in ("now", "today") else raw_value
        fields[field_name] = value
    default_field = f"{new_status}_at"
    if default_field not in set_map and not fields.get(default_field):
        fields[default_field] = today


def _temporal_month_relocation_path(router, path, art, fields):
    """Return the owner-scoped month path for a temporal artefact.

    Returns the original path for living artefacts, archived files, artefacts
    already in the correct month folder, or non-parent render errors that
    historically meant "do not relocate".
    """
    if (art or {}).get("classification") != "temporal":
        return path
    if is_archived_path(path):
        return path
    try:
        target_folder = resolve_folder(
            art,
            parent=normalize_artefact_key(fields.get("parent")),
            fields=fields,
            router=router,
        )
    except ParentChainError:
        raise
    except ValueError:
        return path
    current_folder = os.path.dirname(path)
    if current_folder == target_folder:
        return path
    return os.path.join(target_folder, os.path.basename(path))


def _maybe_rename_on_field_change(vault_root, path, art, old_fields, new_fields):
    """Rename artefact file if frontmatter changes imply a new basename.

    Extracts the title from the current basename using the rule selected for
    the *old* fields, then re-renders using the *new* fields. If the resulting
    basename differs, rename in place (same directory) and update wikilinks.
    Archived files are exempt (they carry an archival prefix outside the
    naming contract).
    """
    naming = art.get("naming")
    if not naming:
        return path
    if is_archived_path(path):
        return path
    current_basename = os.path.basename(path)
    title = extract_title(naming, old_fields, current_basename)
    if title is None:
        title = os.path.splitext(current_basename)[0]
    try:
        new_basename = render_filename(naming, title, new_fields)
    except ValueError:
        return path
    if new_basename == current_basename:
        return path
    new_path = os.path.join(os.path.dirname(path), new_basename)
    rename_and_update_links(vault_root, path, new_path)
    return new_path


def _finish_artefact(vault_root, router, abs_path, fields, old_body, new_body, path, art,
                     frontmatter_changes, operation, *, old_fields=None,
                     resolved=None, scope=None):
    """Save artefact, rename on name-driving change, status-move, return result."""
    _apply_status_change_hooks(fields, old_fields, art)
    had_explicit_created = bool((old_fields or {}).get("created")) or bool(
        (frontmatter_changes or {}).get("created")
    )
    reconcile_fields_for_render(fields, art, abs_path, os.path.basename(path))
    resolved_path = path
    ownership_handled = False
    temporal_relocation_path = path
    should_check_temporal_relocation = (
        art.get("classification") == "temporal"
        and (had_explicit_created or normalize_artefact_key(fields.get("parent")))
    )
    if should_check_temporal_relocation:
        temporal_relocation_path = _temporal_month_relocation_path(
            router, path, art, fields
        )
    if art.get("classification") == "living" and old_fields is not None:
        path, ownership_handled = _maybe_restructure_living_ownership(
            vault_root, router, path, art, old_fields, fields, new_body
        )
        abs_path = os.path.join(vault_root, path)
    if not ownership_handled:
        _save_artefact(abs_path, fields, new_body, vault_root)
    try:
        if should_check_temporal_relocation and temporal_relocation_path != path:
            rename_and_update_links(vault_root, path, temporal_relocation_path)
            path = temporal_relocation_path
            abs_path = os.path.join(vault_root, path)
        if old_fields is not None and not ownership_handled:
            new_path = _maybe_rename_on_field_change(vault_root, path, art, old_fields, fields)
            if new_path != path:
                path = new_path
                abs_path = os.path.join(vault_root, path)
        terminal = (art.get("frontmatter") or {}).get("terminal_statuses")
        path = _maybe_status_move(vault_root, path, terminal, frontmatter_changes)
    except (PartialApplyError, FileNotFoundError, FileExistsError, ValueError, OSError) as exc:
        raise PartialApplyError(
            f"{operation} partially applied — metadata file written {path}; "
            f"move failure: {exc}"
        ) from exc
    return _result_payload(
        path,
        resolved_path,
        operation,
        old_body,
        new_body,
        resolved=resolved,
        scope=scope,
    )


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------

def _prepare_artefact_operation(
    operation, body, frontmatter_changes, target, selector, scope
):
    """Validate and normalise one artefact mutation request before file access."""
    if operation == "delete_section":
        body = ""
        scope = None
    _validate_request_contract(
        operation,
        bool(body),
        frontmatter_changes,
        target,
        selector,
        scope,
    )
    _reject_leading_body_frontmatter(body, resource_label="Artefact")
    return body, scope


def _apply_to_open_artefact(operation, vault_root, router, opened, body="",
                            frontmatter_changes=None, target=None, selector=None,
                            scope=None):
    """Apply one validated mutation to state returned by ``_open_artefact``."""
    path, abs_path, fields, existing_body, art = opened
    old_fields = dict(fields)
    frontmatter_changes = _normalise_ownership_changes(
        vault_root, router, art, frontmatter_changes
    )
    fm_mode = "edit" if operation == "delete_section" else operation
    _merge_frontmatter(fields, frontmatter_changes, fm_mode)
    ensure_parent_tag(fields)
    new_body, resolved = _apply_body_operation(
        existing_body,
        operation,
        body,
        target=target,
        selector=selector,
        scope=scope,
    )
    result_scope = "section" if operation == "delete_section" else scope
    return _finish_artefact(
        vault_root,
        router,
        abs_path,
        fields,
        existing_body,
        new_body,
        path,
        art,
        frontmatter_changes,
        operation,
        old_fields=old_fields,
        resolved=resolved,
        scope=result_scope,
    )


def apply_to_artefact(operation, vault_root, router, path, body="",
                      frontmatter_changes=None, target=None, selector=None,
                      scope=None):
    """Apply an edit/append/prepend/delete_section to an artefact.

    Single shared implementation for all four artefact mutations. The public
    ``edit_artefact``/``append_to_artefact``/``prepend_to_artefact``/
    ``delete_section_artefact`` functions are thin wrappers around this.
    """
    body, scope = _prepare_artefact_operation(
        operation,
        body,
        frontmatter_changes,
        target,
        selector,
        scope,
    )
    opened = _open_artefact(vault_root, router, path)
    return _apply_to_open_artefact(
        operation,
        vault_root,
        router,
        opened,
        body,
        frontmatter_changes=frontmatter_changes,
        target=target,
        selector=selector,
        scope=scope,
    )


def replace_text_in_artefact(vault_root, router, path, *, old_text, new_text,
                             target=None, selector=None, scope=None,
                             match_occurrence=None, replace_all=False):
    """Replace exact text in an artefact body without rewriting its surroundings."""
    path, abs_path, fields, existing_body, art = _open_artefact(vault_root, router, path)
    old_fields = dict(fields)
    new_body, resolved, replacement_count, match_count = _apply_replace_text(
        existing_body,
        old_text,
        new_text,
        target=target,
        selector=selector,
        scope=scope,
        match_occurrence=match_occurrence,
        replace_all=replace_all,
    )
    result = _finish_artefact(
        vault_root,
        router,
        abs_path,
        fields,
        existing_body,
        new_body,
        path,
        art,
        None,
        "replace_text",
        old_fields=old_fields,
        resolved=resolved,
        scope=scope,
    )
    result["match_count"] = match_count
    result["replacement_count"] = replacement_count
    return result


def edit_artefact(vault_root, router, path, body="", frontmatter_changes=None,
                  target=None, selector=None, scope=None):
    """Replace a structural range in an artefact's body."""
    return apply_to_artefact(
        "edit", vault_root, router, path, body,
        frontmatter_changes=frontmatter_changes,
        target=target, selector=selector, scope=scope,
    )


def delete_section_artefact(vault_root, router, path, target=None, selector=None,
                            frontmatter_changes=None):
    """Delete a heading-owned section or callout block from an artefact."""
    return apply_to_artefact(
        "delete_section", vault_root, router, path, "",
        frontmatter_changes=frontmatter_changes,
        target=target, selector=selector, scope=None,
    )


def append_to_artefact(vault_root, router, path, content="", frontmatter_changes=None,
                       target=None, selector=None, scope=None):
    """Append content into a structural range of an artefact."""
    return apply_to_artefact(
        "append", vault_root, router, path, content,
        frontmatter_changes=frontmatter_changes,
        target=target, selector=selector, scope=scope,
    )


def prepend_to_artefact(vault_root, router, path, content="", frontmatter_changes=None,
                        target=None, selector=None, scope=None):
    """Prepend content into a structural range of an artefact."""
    return apply_to_artefact(
        "prepend", vault_root, router, path, content,
        frontmatter_changes=frontmatter_changes,
        target=target, selector=selector, scope=scope,
    )


# ---------------------------------------------------------------------------
# Type conversion
# ---------------------------------------------------------------------------

def convert_artefact(vault_root, router, path, target_type, parent=None, recursive=False):
    """Convert artefact to a different type: move to target folder, reconcile FM, update wikilinks.

    Args:
        vault_root: Absolute path to the vault root.
        router: Compiled router dict.
        path: Relative path from vault root.
        target_type: Target type key or full type (e.g. "design" or "living/design").
        parent: Optional canonical parent artefact reference. If omitted, an existing
                parent is preserved when the target contract permits it. Temporal targets
                file under their owner chain before the date folder when parent is set.
        recursive: Required to convert a living parent with living descendants
                into a temporal artefact, because descendants are deparented.

    Returns:
        Dict with old_path, new_path, type, and links_updated.

    Raises:
        ValueError: If source or target type resolution fails.
        FileNotFoundError: If the source file does not exist.
    """
    vault_root = str(vault_root)

    path, source_art = resolve_and_validate_folder(vault_root, router, path)

    if is_archived_path(path):
        raise ValueError(
            f"Cannot convert archived file '{path}'. "
            f"Un-archive it first by moving it out of _Archive/."
        )

    target_art = resolve_type(router, target_type)
    abs_source = os.path.join(vault_root, path)
    if not os.path.isfile(abs_source):
        raise FileNotFoundError(f"File not found: {path}")

    with open(abs_source, "r", encoding="utf-8") as f:
        content = f.read()
    fields, body = parse_frontmatter(content)
    title = _derive_title_from_path(source_art, fields, path)

    source_prefix = artefact_type_prefix(source_art)
    target_prefix = artefact_type_prefix(target_art)
    source_key_value = fields.get("key")
    old_key = (
        make_artefact_key(source_prefix, source_key_value)
        if source_art.get("classification") == "living" and is_valid_key(source_key_value)
        else None
    )
    old_parent = normalize_artefact_key(fields.get("parent"))
    descendants = []
    if (
        source_art.get("classification") == "living"
        and target_art.get("classification") == "temporal"
        and old_key
    ):
        descendants = descendant_entries(router, old_key)
        if descendants and not recursive:
            raise HasDescendantsError(
                "convert",
                {"key": old_key, "path": path},
                descendant_payload(descendants),
            )

    if target_art.get("classification") == "living":
        target_key = _choose_living_key(
            vault_root,
            router,
            target_art,
            title,
            key=source_key_value if is_valid_key(source_key_value) else None,
            exclude_path=path,
        )
        target_parent = None
        if parent is not None:
            target_parent, _parent_entry = resolve_parent_reference(
                vault_root, router, parent
            )
        elif old_parent:
            _validate_preserved_parent(router, old_parent)
            target_parent = old_parent

        fields["key"] = target_key
        if target_parent:
            fields["parent"] = target_parent
            ensure_parent_tag(fields)
        else:
            fields.pop("parent", None)
        new_key = make_artefact_key(target_prefix, target_key)
    else:
        fields.pop("key", None)
        if parent is not None:
            target_parent, _parent_entry = resolve_parent_reference(
                vault_root, router, parent
            )
        else:
            _validate_preserved_parent(router, old_parent)
            target_parent = old_parent
        if target_parent:
            fields["parent"] = target_parent
            ensure_parent_tag(fields)
        else:
            fields.pop("parent", None)
        new_key = None

    _validate_not_parented_to_descendant(
        router,
        old_key,
        target_parent,
        "convert",
        aliases=[new_key],
    )

    if source_art.get("frontmatter_type"):
        fields["type"] = target_art.get("frontmatter_type", target_art["type"])

    reference_ops = []
    if old_key and old_key != new_key:
        if source_prefix in SELF_TAG_PREFIXES:
            replacement = new_key if new_key and target_prefix in SELF_TAG_PREFIXES else None
            _replace_exact_tag(fields, old_key, replacement)
        reference_ops = _plan_reference_mutation(
            vault_root,
            router,
            old_key,
            new_key,
            skip_paths={path},
            operation="convert",
        )
    elif new_key:
        ensure_self_tag(fields, target_prefix, target_key)

    rendered_fields = dict(fields)
    reconcile_fields_for_render(
        rendered_fields, target_art, abs_source, os.path.basename(path)
    )
    pending_entry = None
    if new_key:
        pending_entry = {
            "path": path,
            "type": target_art.get("frontmatter_type", target_art.get("type")),
            "classification": "living",
            "type_key": target_art.get("key"),
            "type_prefix": target_prefix,
            "key": fields.get("key"),
            "parent": normalize_artefact_key(rendered_fields.get("parent")),
        }
    mutation_router = _router_with_pending_ownership(
        router, old_key, new_key, pending_entry
    )
    target_folder = resolve_folder(
        target_art,
        parent=normalize_artefact_key(rendered_fields.get("parent")),
        fields=rendered_fields,
        router=mutation_router,
    )
    target_basename = render_filename_or_default(
        target_art.get("naming"), title, rendered_fields
    )
    new_path = os.path.join(target_folder, target_basename)
    if new_path != path:
        stem, ext = os.path.splitext(os.path.basename(new_path))
        folder = os.path.dirname(new_path)
        target_abs_folder = os.path.join(vault_root, folder)
        unique_name = unique_filename(target_abs_folder, stem, ext or ".md")
        new_path = os.path.join(folder, unique_name)
    if new_key:
        mutation_router["artefact_index"][new_key]["path"] = new_path
    check_write_allowed(new_path)

    moves = [{"source": path, "dest": new_path}]
    moves.extend(
        _plan_descendant_moves(
            vault_root,
            router,
            old_key,
            mutation_router,
            reference_ops,
            operation="convert",
        )
    )

    preflight_move_set(vault_root, moves)

    write_ops = [
        {"path": path, "fields": rendered_fields, "body": body},
        *reference_ops,
    ]
    _write_frontmatter_mutations(
        vault_root, write_ops, operation="convert mutation"
    )
    links_updated = 0
    real_moves = [move for move in moves if move["source"] != move["dest"]]
    if real_moves:
        try:
            result = move_and_update_links(vault_root, real_moves)
        except PartialApplyError as exc:
            metadata_written = [op["path"] for op in write_ops]
            raise PartialApplyError(
                "convert mutation partially applied — "
                f"metadata files written {metadata_written}; move failure: {exc}"
            ) from exc
        links_updated = result["links_updated"]
        prune_vacated_owner_folders(
            vault_root,
            [move["source"] for move in result.get("applied", [])],
            router,
        )

    return {
        "old_path": path,
        "new_path": new_path,
        "type": target_art["type"],
        "links_updated": links_updated,
    }


# ---------------------------------------------------------------------------
# Archiving
# ---------------------------------------------------------------------------

_DATE_PREFIX_RE = re.compile(r"^\d{8}-")


def _living_descendants_or_empty(router, source_key):
    if not source_key:
        return []
    return descendant_entries(router, source_key)


def _refuse_living_descendants(operation, source, descendants):
    if descendants:
        raise HasDescendantsError(operation, source, descendant_payload(descendants))


def _archive_destination(path, art, today):
    filename = os.path.basename(path)
    date_prefix = today.replace("-", "")
    if not _DATE_PREFIX_RE.match(filename):
        filename = f"{date_prefix}-{filename}"

    type_folder = art["path"]
    rel_from_type = os.path.relpath(os.path.dirname(path), type_folder)
    # Strip +Status/ folders from the path (archived files don't need them).
    parts = rel_from_type.split(os.sep)
    parts = [p for p in parts if not p.startswith("+")]
    rel_from_type = os.path.join(*parts) if parts and parts != ["."] else "."

    if rel_from_type == ".":
        return os.path.join("_Archive", type_folder, filename)
    return os.path.join("_Archive", type_folder, rel_from_type, filename)


def _read_open_path(vault_root, router, path):
    resolved_path, art = resolve_and_validate_folder(vault_root, router, path)
    abs_path = os.path.join(vault_root, resolved_path)
    if not os.path.isfile(abs_path):
        raise FileNotFoundError(f"File not found: {resolved_path}")
    with open(abs_path, "r", encoding="utf-8") as f:
        content = f.read()
    fields, body = parse_frontmatter(content)
    return resolved_path, abs_path, fields, body, art


def _plan_archive_entry(vault_root, router, path, today, *, require_terminal):
    path, abs_path, fields, body, art = _read_open_path(vault_root, router, path)
    if is_archived_path(path):
        raise ValueError(f"'{path}' is already archived.")
    check_write_allowed(path)

    if require_terminal:
        terminal = art.get("frontmatter", {}).get("terminal_statuses") or []
        if not terminal:
            raise ValueError(
                f"Type '{art['type']}' has no terminal statuses — cannot archive."
            )

        status = fields.get("status", "")
        if status not in terminal:
            raise ValueError(
                f"Cannot archive '{path}': status '{status}' is not terminal. "
                f"Terminal statuses for {art['type']}: {', '.join(terminal)}"
            )

    archived_fields = dict(fields)
    if "archiveddate" not in archived_fields:
        archived_fields["archiveddate"] = today

    return {
        "path": path,
        "abs_path": abs_path,
        "fields": archived_fields,
        "body": body,
        "dest": _archive_destination(path, art, today),
    }


def _move_plan_for_reparent(vault_root, router, child_entries, target_parent):
    mutation_router = dict(router)
    artefact_index = {
        key: dict(value)
        for key, value in (router.get("artefact_index") or {}).items()
    }
    for child in child_entries:
        child_key = child["artefact_key"]
        artefact_index[child_key]["parent"] = target_parent
    mutation_router["artefact_index"] = artefact_index

    moves = []
    write_ops = []
    seen = set()
    for child in child_entries:
        entries = [child] + descendant_entries(router, child["artefact_key"])
        for entry in entries:
            key = entry["artefact_key"]
            if key in seen:
                continue
            seen.add(key)
            rel_path = entry["path"]
            content = read_file_content(vault_root, rel_path)
            if content.startswith("Error:"):
                _raise_stale_index_missing(rel_path, "reparent")
            fields, body = parse_frontmatter(content)
            _resolved, art = resolve_and_validate_folder(
                vault_root, router, rel_path
            )
            if key == child["artefact_key"]:
                old_parent = normalize_artefact_key(fields.get("parent"))
                if old_parent:
                    replace_artefact_key_references(fields, old_parent, target_parent)
                if target_parent:
                    fields["parent"] = target_parent
                    ensure_parent_tag(fields)
                else:
                    fields.pop("parent", None)
            dest, rendered_fields = _render_existing_artefact_path(
                vault_root, mutation_router, art, rel_path, fields
            )
            moves.append({"source": rel_path, "dest": dest})
            if key == child["artefact_key"]:
                write_ops.append({
                    "path": rel_path,
                    "fields": rendered_fields,
                    "body": body,
                })

    preflight_move_set(vault_root, moves)
    return moves, write_ops


def reparent_children(vault_root, router, source, to_marker=None, *, to_provided=False):
    """Reparent all direct living children of ``source`` in one move set.

    ``to_provided=False`` dissolves the source by lifting children to the
    source's own parent.  ``to_marker`` set to ``None`` or ``""`` clears the
    children to top-level.  Otherwise it is resolved as the new parent.
    """
    vault_root = str(vault_root)
    source_path, _abs_path, source_fields, _body, source_art = _read_open_path(
        vault_root, router, source
    )
    source_key = canonical_living_artefact_key(source_art, source_fields)
    if not source_key:
        raise ValueError(
            "Cannot reparent children of non-living/keyless artefact: "
            f"{source_path}"
        )
    if source_key not in (router.get("artefact_index") or {}):
        raise StaleArtefactIndexError(
            f"source artefact key is not in the compiled living index: {source_key}"
        )

    if to_provided:
        if to_marker in (None, ""):
            target_parent = None
        else:
            target_parent, _target_entry = resolve_parent_reference(
                vault_root, router, to_marker
            )
    else:
        target_parent = normalize_artefact_key(source_fields.get("parent"))
    _validate_not_parented_to_descendant(
        router,
        source_key,
        target_parent,
        "reparent",
    )

    children = direct_child_entries(router, source_key)
    moves, write_ops = _move_plan_for_reparent(vault_root, router, children, target_parent)

    _write_frontmatter_mutations(vault_root, write_ops, operation="reparent")
    written = [op["path"] for op in write_ops]

    real_moves = [move for move in moves if move["source"] != move["dest"]]
    links_updated = 0
    if real_moves:
        try:
            result = move_and_update_links(vault_root, real_moves)
        except PartialApplyError as exc:
            raise PartialApplyError(
                "reparent partially applied — "
                f"metadata files written {written}; move failure: {exc}"
            ) from exc
        links_updated = result["links_updated"]
        prune_vacated_owner_folders(
            vault_root,
            [move["source"] for move in result.get("applied", [])],
            router,
        )

    return {
        "source": source_path,
        "to": target_parent,
        "children": [
            {
                "key": child["artefact_key"],
                "old_path": child["path"],
            }
            for child in children
        ],
        "moves": moves,
        "links_updated": links_updated,
    }


def archive_artefact(vault_root, router, path, recursive=False):
    """Archive a living artefact to the top-level _Archive/ directory.

    1. Resolve path, read frontmatter, validate type has terminal statuses.
    2. Validate current status is terminal (caller must set it first).
    3. Add archiveddate if not present.
    4. Prepend yyyymmdd- date prefix to filename if not present.
    5. Move to _Archive/{type_folder}/{project}/.

    Returns dict with old_path, new_path, links_updated.
    """
    vault_root = str(vault_root)
    path, abs_path, fields, body, art = _open_artefact(vault_root, router, path)

    source_key = canonical_living_artefact_key(art, fields)
    descendants = _living_descendants_or_empty(router, source_key)
    if not recursive:
        _refuse_living_descendants("archive", path, descendants)

    today = now_iso()[:10]
    archive_paths = [path] + [entry["path"] for entry in descendants]
    plans = [
        _plan_archive_entry(
            vault_root,
            router,
            rel_path,
            today,
            require_terminal=(idx == 0),
        )
        for idx, rel_path in enumerate(archive_paths)
    ]
    moves = [{"source": plan["path"], "dest": plan["dest"]} for plan in plans]
    preflight_move_set(
        vault_root,
        moves,
        allow_archive_paths=True,
    )

    write_ops = [
        {"path": plan["path"], "fields": plan["fields"], "body": plan["body"]}
        for plan in plans
    ]
    _write_frontmatter_mutations(vault_root, write_ops, operation="archive")
    written = [op["path"] for op in write_ops]

    try:
        result = move_and_update_links(
            vault_root,
            moves,
            allow_archive_paths=True,
        )
    except PartialApplyError as exc:
        raise PartialApplyError(
            "archive partially applied — "
            f"metadata files written {written}; move failure: {exc}"
        ) from exc
    prune_vacated_owner_folders(
        vault_root,
        [move["source"] for move in result.get("applied", [])],
        router,
    )

    return {
        "old_path": path,
        "new_path": plans[0]["dest"],
        "links_updated": result["links_updated"],
        "archived": [
            {"old_path": plan["path"], "new_path": plan["dest"]}
            for plan in plans
        ],
    }


def _plan_unarchive_entry(vault_root, router, path):
    """Return metadata and derived destination for one archived artefact."""
    abs_path = os.path.join(vault_root, path)
    if not os.path.isfile(abs_path):
        raise FileNotFoundError(f"File not found: {path}")
    with open(abs_path, "r", encoding="utf-8") as handle:
        fields, body = parse_frontmatter(handle.read())
    art = resolve_type(router, fields.get("type"))
    filename = _DATE_PREFIX_RE.sub("", os.path.basename(path))
    restored_fields = dict(fields)
    restored_fields.pop("archiveddate", None)
    restored_parent = normalize_artefact_key(restored_fields.get("parent"))
    if restored_parent and resolve_artefact_key_entry(router, restored_parent):
        provisional = os.path.join(art["path"], filename)
        dest, restored_fields = _render_existing_artefact_path(
            vault_root, router, art, provisional, restored_fields
        )
    else:
        # Archive placement is the transaction's restoration record when old
        # metadata has no authoritative parent. Preserve that recorded path;
        # doctor will separately report any missing-parent contract violation.
        rel_from_archive = os.path.relpath(os.path.dirname(path), "_Archive")
        dest = os.path.join(rel_from_archive, filename)
    return {
        "path": path,
        "abs_path": abs_path,
        "fields": restored_fields,
        "body": body,
        "dest": dest,
        "art": art,
    }


def _archived_living_descendant_paths(vault_root, router, source_plan):
    source_fields = source_plan["fields"]
    source_key = canonical_living_artefact_key(source_plan["art"], source_fields)
    if not source_key:
        return [], []
    entries = []
    uninspected = []
    archive_root = os.path.join(vault_root, "_Archive")
    for root, _dirs, files in os.walk(archive_root):
        for filename in files:
            if not filename.endswith(".md"):
                continue
            rel_path = os.path.relpath(os.path.join(root, filename), vault_root)
            if rel_path == source_plan["path"]:
                continue
            try:
                plan = _plan_unarchive_entry(vault_root, router, rel_path)
            except (ValueError, OSError) as exc:
                uninspected.append({"path": rel_path, "reason": str(exc)})
                continue
            key = canonical_living_artefact_key(plan["art"], plan["fields"])
            if key:
                entries.append((key, normalize_artefact_key(plan["fields"].get("parent")), plan))
    children_by_parent = {}
    for key, parent, plan in entries:
        children_by_parent.setdefault(parent, []).append((key, plan))

    wanted = {source_key}
    descendants = []
    pending = [source_key]
    cursor = 0
    while cursor < len(pending):
        parent = pending[cursor]
        cursor += 1
        for key, plan in children_by_parent.get(parent, []):
            if key not in wanted:
                wanted.add(key)
                descendants.append(plan)
                pending.append(key)
    return descendants, uninspected


def unarchive_artefact(vault_root, router, path, recursive=False):
    """Restore one archived artefact or subtree through current metadata projection."""
    vault_root = str(vault_root)

    if not is_archived_path(path):
        raise ValueError(f"'{path}' is not in _Archive/.")

    plans = [_plan_unarchive_entry(vault_root, router, path)]
    uninspected = []
    if recursive:
        descendants, uninspected = _archived_living_descendant_paths(
            vault_root, router, plans[0]
        )
        plans.extend(descendants)
    moves = [{"source": plan["path"], "dest": plan["dest"]} for plan in plans]
    for plan in plans:
        check_write_allowed(plan["dest"])
    preflight_move_set(
        vault_root,
        moves,
        allow_archive_paths=True,
    )
    written = []
    for plan in plans:
        try:
            _save_artefact(
                plan["abs_path"], plan["fields"], plan["body"], vault_root
            )
            written.append(plan["path"])
        except OSError as exc:
            if not written:
                raise
            raise PartialApplyError(
                f"unarchive partially applied — metadata files written {written}; "
                f"write failure at {plan['path']}: {exc}"
            ) from exc
    try:
        result = move_and_update_links(
            vault_root, moves,
            allow_archive_paths=True,
        )
    except (PartialApplyError, FileNotFoundError, FileExistsError, ValueError, OSError) as exc:
        raise PartialApplyError(
            "unarchive partially applied — "
            f"metadata files written {written}; move failure: {exc}"
        ) from exc

    return {
        "old_path": path,
        "new_path": plans[0]["dest"],
        "links_updated": result["links_updated"],
        "restored": [
            {"old_path": plan["path"], "new_path": plan["dest"]}
            for plan in plans
        ],
        "uninspected": uninspected,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class _WithinAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        within = list(getattr(namespace, self.dest, None) or [])
        within.append({"target": values})
        setattr(namespace, self.dest, within)


class _WithinOccurrenceAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        within = list(getattr(namespace, "within", None) or [])
        if not within:
            parser.error("--within-occurrence requires a preceding --within")
        within[-1]["occurrence"] = values
        setattr(namespace, "within", within)


def _build_parser():
    parser = argparse.ArgumentParser(
        description="Edit a Brain artefact or configuration resource."
    )
    parser.add_argument(
        "operation",
        choices=("edit", "append", "prepend", "delete_section", "replace_text"),
        nargs="?",
    )
    parser.add_argument("--resource", choices=RESOURCE_KINDS, default="artefact")
    parser.add_argument("--path")
    parser.add_argument("--name")
    parser.add_argument("--body", default="")
    parser.add_argument("--body-file", default="")
    parser.add_argument("--body-handle", default="")
    parser.add_argument("--old-text")
    parser.add_argument("--new-text")
    parser.add_argument("--match-occurrence", type=int)
    parser.add_argument("--replace-all", action="store_true")
    parser.add_argument("--frontmatter", help="JSON object with frontmatter changes")
    parser.add_argument("--target")
    parser.add_argument(
        "--scope", choices=("section", "intro", "body", "heading", "header")
    )
    parser.add_argument("--occurrence", type=int)
    parser.add_argument("--within", action=_WithinAction, default=[])
    parser.add_argument("--within-occurrence", type=int, action=_WithinOccurrenceAction)
    parser.add_argument("--fix-links", action="store_true")
    parser.add_argument("--vault")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--temp-path",
        nargs="?",
        const=".md",
        metavar="SUFFIX",
        help="create a temporary body file and exit",
    )
    return parser


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.temp_path is not None:
        print(make_temp_path(suffix=args.temp_path))
        raise SystemExit(0)

    if args.operation is None:
        parser.error("an operation is required")
    if args.resource == "artefact":
        if not args.path:
            parser.error("resource='artefact' requires --path")
        if args.name:
            parser.error("resource='artefact' does not accept --name")
    else:
        if not args.name:
            parser.error(f"resource='{args.resource}' requires --name")
        if args.path or args.fix_links:
            parser.error(
                f"resource='{args.resource}' does not accept artefact-only options "
                "(--path, --fix-links)"
            )

    replace_fields_used = (
        args.old_text is not None
        or args.new_text is not None
        or args.match_occurrence is not None
        or args.replace_all
    )
    if args.operation == "replace_text":
        if not args.old_text:
            parser.error("replace_text requires --old-text")
        if args.new_text is None:
            parser.error("replace_text requires --new-text (use '' to delete)")
        if args.body or args.body_file or args.body_handle or args.frontmatter:
            parser.error("replace_text does not accept --body, --body-file, --body-handle, or --frontmatter")
    elif replace_fields_used:
        parser.error(
            "--old-text, --new-text, --match-occurrence, and --replace-all "
            "are accepted only by replace_text"
        )

    vault_root = str(find_vault_root(args.vault))
    router = load_fresh_compiled_router(vault_root)
    if "error" in router:
        if args.json:
            print(json.dumps(router))
        else:
            print(f"Error: {router['error']}", file=sys.stderr)
        sys.exit(1)

    try:
        fm_changes = json.loads(args.frontmatter) if args.frontmatter else None
    except json.JSONDecodeError as exc:
        parser.error(f"--frontmatter must be a JSON object: {exc.msg}")
    if fm_changes is not None and not isinstance(fm_changes, dict):
        parser.error("--frontmatter must be a JSON object")

    selector = None
    if args.within or args.occurrence is not None:
        selector = {"within": args.within}
        if args.occurrence is not None:
            selector["occurrence"] = args.occurrence

    staging_warning = None
    try:
        with vault_mutation_lock(vault_root):
            body, _staged_handle = resolve_mutation_body(
                vault_root,
                body=args.body,
                body_file=args.body_file,
                body_handle=args.body_handle,
            )
            result = edit_resource(
                vault_root,
                router,
                resource=args.resource,
                operation=args.operation,
                path=args.path,
                name=args.name,
                body=body,
                frontmatter_changes=fm_changes,
                target=args.target,
                selector=selector,
                scope=args.scope,
                fix_links=args.fix_links,
                old_text=args.old_text,
                new_text=args.new_text,
                match_occurrence=args.match_occurrence,
                replace_all=args.replace_all,
            )
            staging_warning = finalise_staged_body(vault_root, args.body_handle)
    except (MutationLockError, ValueError, FileNotFoundError, PartialApplyError) as e:
        message = public_mutation_error_message(e)
        if args.json:
            print(json.dumps({"error": message}))
        else:
            print(f"Error: {message}", file=sys.stderr)
        sys.exit(1)

    if staging_warning:
        result["staging_warning"] = staging_warning
        print(f"Warning: {staging_warning}", file=sys.stderr)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        op_label = OPERATION_LABELS[args.operation]
        print(f"{op_label} {result['path']}", file=sys.stderr)


if __name__ == "__main__":
    main()
