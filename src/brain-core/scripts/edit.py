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

import json
import os
import re
import sys

from _resource_contract import RESOURCE_KINDS
from _common import (
    SELF_TAG_PREFIXES,
    apply_terminal_status_folder,
    check_write_allowed,
    collect_headings,
    config_resource_rel_path,
    ensure_parent_tag,
    ensure_self_tag,
    ensure_tags_list,
    extract_title,
    find_vault_root,
    generate_contextual_slug,
    is_archived_path,
    is_valid_key,
    living_key_set,
    legacy_target_migration_error,
    load_compiled_router,
    make_artefact_key,
    make_temp_path,
    normalize_artefact_key,
    now_iso,
    parse_frontmatter,
    read_file_content,
    replace_artefact_key_references,
    reconcile_fields_for_render,
    render_filename,
    render_filename_or_default,
    resolve_folder,
    resolve_and_validate_folder,
    resolve_parent_reference,
    resolve_body_file,
    resolve_type,
    resolve_structural_target,
    scan_artefact_key_references,
    safe_write,
    serialize_frontmatter,
    parse_structural_anchor_line,
    unique_filename,
    validate_key,
    artefact_type_prefix,
)
from rename import rename_and_update_links
import fix_links as _fix_links


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OPERATION_LABELS = {
    "edit": "Edited",
    "append": "Appended",
    "prepend": "Prepended",
    "delete_section": "Deleted section from",
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

def _open_artefact(vault_root, router, path):
    """Validate, read, and parse an artefact. Returns (path, abs_path, fields, body, artefact)."""
    vault_root = str(vault_root)
    path, art = resolve_and_validate_folder(vault_root, router, path)
    check_write_allowed(path)
    abs_path = os.path.join(vault_root, path)
    if not os.path.isfile(abs_path):
        raise FileNotFoundError(f"File not found: {path}")
    with open(abs_path, "r", encoding="utf-8") as f:
        content = f.read()
    fields, body = parse_frontmatter(content)
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
        "intro": "the intro before the heading's first child heading",
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
        "child heading) | 'heading' (line-only, edit-only); callout -> "
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


def edit_resource(vault_root, router, resource="artefact", operation="edit",
                  path=None, name=None, body="", frontmatter_changes=None,
                  target=None, selector=None, scope=None, fix_links=False,
                  file_index=None):
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
        if operation not in {"edit", "append", "prepend", "delete_section"}:
            raise ValueError(f"Unknown operation '{operation}'")
        result = apply_to_artefact(
            operation, vault_root, router, path, body,
            frontmatter_changes=frontmatter_changes,
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

    _validate_request_contract(
        operation,
        bool(body),
        frontmatter_changes,
        target,
        selector,
        scope,
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
    while True:
        candidate = generate_contextual_slug(title)
        if candidate not in existing:
            return candidate


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


def _preflight_destination(vault_root, source_path, dest_path):
    """Raise if a planned destination already exists on disk."""
    if dest_path == source_path:
        return
    abs_source = os.path.join(vault_root, source_path)
    abs_dest = os.path.join(vault_root, dest_path)
    if not os.path.exists(abs_dest):
        return
    try:
        same = os.path.samefile(abs_source, abs_dest)
    except OSError:
        same = False
    if not same:
        raise FileExistsError(f"Destination file already exists: {dest_path}")


def _router_with_pending_artefact_key(router, old_key, new_key, entry):
    """Return a router view whose artefact index reflects an in-flight key update."""
    updated_router = dict(router)
    artefact_index = dict(router.get("artefact_index") or {})
    if old_key:
        artefact_index.pop(old_key, None)
    artefact_index[new_key] = entry
    updated_router["artefact_index"] = artefact_index
    return updated_router


def _commit_with_possible_rename(vault_root, path, new_path, fields, body):
    """Serialize + safe_write frontmatter and body, then rename if path changed.

    Caller handles _preflight_destination; this helper is the commit step.
    """
    abs_path = os.path.join(vault_root, path)
    safe_write(
        abs_path,
        serialize_frontmatter(fields, body=body),
        bounds=vault_root,
    )
    if new_path != path:
        rename_and_update_links(vault_root, path, new_path)


def _apply_reference_mutation(vault_root, router, old_key, new_key, *, skip_paths=None):
    """Rewrite canonical key references and move affected direct children."""
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
            continue
        fields, body = parse_frontmatter(content)
        if not replace_artefact_key_references(fields, old_key, new_key):
            continue
        _resolved, art = resolve_and_validate_folder(vault_root, router, rel_path)
        new_path = rel_path
        if ref.get("parent"):
            new_path, fields = _render_existing_artefact_path(
                vault_root, router, art, rel_path, fields
            )
        operations.append(
            {
                "path": rel_path,
                "new_path": new_path,
                "fields": fields,
                "body": body,
            }
        )

    for op in operations:
        _preflight_destination(vault_root, op["path"], op["new_path"])

    for op in operations:
        _commit_with_possible_rename(
            vault_root, op["path"], op["new_path"], op["fields"], op["body"]
        )

    return operations


def _maybe_restructure_living_ownership(vault_root, router, path, art, old_fields, new_fields, new_body):
    """Rewrite canonical key references and move artefacts when ownership changes."""
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

    if old_key and old_key != new_key:
        replacement = new_key if type_prefix in SELF_TAG_PREFIXES else None
        _replace_exact_tag(new_fields, old_key, replacement)
    elif new_key:
        ensure_self_tag(new_fields, type_prefix, new_key_value)

    if new_key:
        _ensure_free_artefact_key(
            vault_root, router, art, new_key_value, exclude_path=path
        )

    new_path, rendered_fields = _render_existing_artefact_path(
        vault_root, router, art, path, new_fields
    )

    mutation_router = router
    if old_key and new_key:
        # The inbound-reference scan below uses folder derivations from the
        # router's artefact_index entry for this key. Swap in a pending entry
        # under the new key so children resolve their forthcoming positions
        # (new folder, new parent pointer) rather than the now-stale old ones.
        old_entry = (router.get("artefact_index") or {}).get(old_key) or {}
        pending_entry = dict(old_entry)
        pending_entry.update(
            {
                "path": new_path,
                "type": art.get("frontmatter_type", art.get("type")),
                "type_key": art.get("key"),
                "type_prefix": type_prefix,
                "key": new_key_value,
                "parent": new_parent,
            }
        )
        mutation_router = _router_with_pending_artefact_key(
            router, old_key, new_key, pending_entry
        )
        _apply_reference_mutation(
            vault_root, mutation_router, old_key, new_key, skip_paths={path}
        )

    _preflight_destination(vault_root, path, new_path)
    _commit_with_possible_rename(
        vault_root, path, new_path, rendered_fields, new_body
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
        # to avoid nesting (e.g. +Implemented/+Superseded/ → +Superseded/)
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


def _maybe_relocate_temporal_month(vault_root, path, art, fields):
    """Relocate a temporal artefact to ``_Temporal/<Type>/yyyy-mm/`` for its ``created``.

    Returns the (possibly-updated) path. No-op for living artefacts, archived
    files, or artefacts already in the correct month folder.
    """
    if (art or {}).get("classification") != "temporal":
        return path
    if is_archived_path(path):
        return path
    try:
        target_folder = resolve_folder(art, fields=fields)
    except ValueError:
        return path
    current_folder = os.path.dirname(path)
    if current_folder == target_folder:
        return path
    new_path = os.path.join(target_folder, os.path.basename(path))
    abs_target = os.path.join(vault_root, target_folder)
    os.makedirs(abs_target, exist_ok=True)
    rename_and_update_links(vault_root, path, new_path)
    return new_path


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
    _save_artefact(abs_path, fields, new_body, vault_root)
    resolved_path = path
    if art.get("classification") == "temporal" and had_explicit_created:
        new_path = _maybe_relocate_temporal_month(vault_root, path, art, fields)
        if new_path != path:
            path = new_path
            abs_path = os.path.join(vault_root, path)
    ownership_handled = False
    if art.get("classification") == "living" and old_fields is not None:
        path, ownership_handled = _maybe_restructure_living_ownership(
            vault_root, router, path, art, old_fields, fields, new_body
        )
        abs_path = os.path.join(vault_root, path)
    if old_fields is not None and not ownership_handled:
        new_path = _maybe_rename_on_field_change(vault_root, path, art, old_fields, fields)
        if new_path != path:
            path = new_path
            abs_path = os.path.join(vault_root, path)
    terminal = (art.get("frontmatter") or {}).get("terminal_statuses")
    path = _maybe_status_move(vault_root, path, terminal, frontmatter_changes)
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

def apply_to_artefact(operation, vault_root, router, path, body="",
                      frontmatter_changes=None, target=None, selector=None,
                      scope=None):
    """Apply an edit/append/prepend/delete_section to an artefact.

    Single shared implementation for all four artefact mutations. The public
    ``edit_artefact``/``append_to_artefact``/``prepend_to_artefact``/
    ``delete_section_artefact`` functions are thin wrappers around this.
    """
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
    path, abs_path, fields, existing_body, art = _open_artefact(vault_root, router, path)
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

def convert_artefact(vault_root, router, path, target_type, parent=None):
    """Convert artefact to a different type: move to target folder, reconcile FM, update wikilinks.

    Args:
        vault_root: Absolute path to the vault root.
        router: Compiled router dict.
        path: Relative path from vault root.
        target_type: Target type key or full type (e.g. "design" or "living/design").
        parent: Optional canonical parent artefact reference. If omitted, an existing
                parent is preserved when the target contract permits it. Temporal targets
                keep their normal date-based folders.

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
            target_parent = old_parent
        if target_parent:
            fields["parent"] = target_parent
            ensure_parent_tag(fields)
        else:
            fields.pop("parent", None)
        new_key = None

    if source_art.get("frontmatter_type"):
        fields["type"] = target_art.get("frontmatter_type", target_art["type"])

    if old_key and old_key != new_key:
        if source_prefix in SELF_TAG_PREFIXES:
            replacement = new_key if new_key and target_prefix in SELF_TAG_PREFIXES else None
            _replace_exact_tag(fields, old_key, replacement)
        _apply_reference_mutation(vault_root, router, old_key, new_key, skip_paths={path})
    elif new_key:
        ensure_self_tag(fields, target_prefix, target_key)

    rendered_fields = dict(fields)
    reconcile_fields_for_render(
        rendered_fields, target_art, abs_source, os.path.basename(path)
    )
    target_folder = resolve_folder(
        target_art,
        parent=normalize_artefact_key(rendered_fields.get("parent")),
        fields=rendered_fields,
        router=router,
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
    check_write_allowed(new_path)
    _preflight_destination(vault_root, path, new_path)

    safe_write(
        abs_source,
        serialize_frontmatter(rendered_fields, body=body),
        bounds=vault_root,
    )
    links_updated = 0
    if new_path != path:
        links_updated = rename_and_update_links(vault_root, path, new_path)

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


def archive_artefact(vault_root, router, path):
    """Archive a living artefact to the top-level _Archive/ directory.

    1. Resolve path, read frontmatter, validate type has terminal statuses.
    2. Validate current status is terminal (caller must set it first).
    3. Add archiveddate if not present.
    4. Prepend yyyymmdd- date prefix to filename if not present.
    5. Move to _Archive/{type_folder}/{project}/.

    Returns dict with old_path, new_path, links_updated.
    """
    path, abs_path, fields, body, art = _open_artefact(vault_root, router, path)
    vault_root = str(vault_root)

    if is_archived_path(path):
        raise ValueError(f"'{path}' is already archived.")

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

    today = now_iso()[:10]
    if "archiveddate" not in fields:
        fields["archiveddate"] = today

    filename = os.path.basename(path)
    date_prefix = today.replace("-", "")
    if not _DATE_PREFIX_RE.match(filename):
        filename = f"{date_prefix}-{filename}"

    type_folder = art["path"]
    rel_from_type = os.path.relpath(os.path.dirname(path), type_folder)
    # Strip +Status/ folders from the path (archived files don't need them)
    parts = rel_from_type.split(os.sep)
    parts = [p for p in parts if not p.startswith("+")]
    rel_from_type = os.path.join(*parts) if parts and parts != ["."] else "."

    if rel_from_type == ".":
        dest = os.path.join("_Archive", type_folder, filename)
    else:
        dest = os.path.join("_Archive", type_folder, rel_from_type, filename)

    _save_artefact(abs_path, fields, body, vault_root)
    links_updated = rename_and_update_links(
        vault_root,
        path,
        dest,
        allow_archive_paths=True,
    )

    return {
        "old_path": path,
        "new_path": dest,
        "links_updated": links_updated,
    }


def unarchive_artefact(vault_root, router, path):
    """Restore an archived artefact from _Archive/ to its original type folder.

    1. Validate path is in _Archive/.
    2. Strip yyyymmdd- date prefix from filename.
    3. Compute original type folder destination.
    4. Remove archiveddate from frontmatter.
    5. Move via rename_and_update_links.

    Returns dict with old_path, new_path, links_updated.
    """
    vault_root = str(vault_root)

    if not is_archived_path(path):
        raise ValueError(f"'{path}' is not in _Archive/.")

    abs_path = os.path.join(vault_root, path)
    if not os.path.isfile(abs_path):
        raise FileNotFoundError(f"File not found: {path}")

    with open(abs_path, "r", encoding="utf-8") as f:
        content = f.read()
    fields, body = parse_frontmatter(content)

    filename = os.path.basename(path)
    if _DATE_PREFIX_RE.match(filename):
        filename = _DATE_PREFIX_RE.sub("", filename)

    # _Archive/Ideas/Brain/20260101-old-idea.md → Ideas/Brain/old-idea.md
    rel_from_archive = os.path.relpath(os.path.dirname(path), "_Archive")
    dest = os.path.join(rel_from_archive, filename)
    check_write_allowed(dest)

    fields.pop("archiveddate", None)
    _save_artefact(abs_path, fields, body, vault_root)
    links_updated = rename_and_update_links(
        vault_root,
        path,
        dest,
        allow_archive_paths=True,
    )

    return {
        "old_path": path,
        "new_path": dest,
        "links_updated": links_updated,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    def _parse_selector_int(arg_name, raw):
        try:
            return int(raw)
        except ValueError:
            print(f"Error: {arg_name} expects an integer", file=sys.stderr)
            sys.exit(1)

    operation = None
    path = None
    body = ""
    body_file_path = ""
    vault_arg = None
    json_mode = False
    fm_json = None
    target = None
    scope = None
    occurrence = None
    within = []

    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--path" and i + 1 < len(sys.argv):
            path = sys.argv[i + 1]
            i += 2
        elif arg == "--body" and i + 1 < len(sys.argv):
            body = sys.argv[i + 1]
            i += 2
        elif arg == "--body-file" and i + 1 < len(sys.argv):
            body_file_path = sys.argv[i + 1]
            i += 2
        elif arg == "--frontmatter" and i + 1 < len(sys.argv):
            fm_json = sys.argv[i + 1]
            i += 2
        elif arg == "--target" and i + 1 < len(sys.argv):
            target = sys.argv[i + 1]
            i += 2
        elif arg == "--scope" and i + 1 < len(sys.argv):
            scope = sys.argv[i + 1]
            i += 2
        elif arg == "--occurrence" and i + 1 < len(sys.argv):
            occurrence = _parse_selector_int("--occurrence", sys.argv[i + 1])
            i += 2
        elif arg == "--within" and i + 1 < len(sys.argv):
            within.append({"target": sys.argv[i + 1]})
            i += 2
        elif arg == "--within-occurrence" and i + 1 < len(sys.argv):
            if not within:
                print("Error: --within-occurrence requires a preceding --within", file=sys.stderr)
                sys.exit(1)
            within[-1]["occurrence"] = _parse_selector_int(
                "--within-occurrence", sys.argv[i + 1]
            )
            i += 2
        elif arg == "--vault" and i + 1 < len(sys.argv):
            vault_arg = sys.argv[i + 1]
            i += 2
        elif arg == "--json":
            json_mode = True
            i += 1
        elif arg == "--temp-path":
            suffix = ".md"
            if i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith("--"):
                suffix = sys.argv[i + 1]
            print(make_temp_path(suffix=suffix))
            sys.exit(0)
        elif not arg.startswith("--") and operation is None:
            operation = arg
            i += 1
        else:
            i += 1

    if operation not in ("edit", "append", "prepend", "delete_section") or not path:
        print(
            "Usage: edit.py edit|append|prepend|delete_section --path PATH "
            "[--target TARGET] [--scope SCOPE] [--occurrence N] "
            "[--within TARGET --within-occurrence N]... [--vault PATH] "
            "[--json] [--temp-path [SUFFIX]]",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        body, _ = resolve_body_file(body, body_file_path)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    vault_root = str(find_vault_root(vault_arg))

    router = load_compiled_router(vault_root)
    if "error" in router:
        if json_mode:
            print(json.dumps(router))
        else:
            print(f"Error: {router['error']}", file=sys.stderr)
        sys.exit(1)

    fm_changes = json.loads(fm_json) if fm_json else None
    selector = None
    if within or occurrence is not None:
        selector = {"within": within}
        if occurrence is not None:
            selector["occurrence"] = occurrence

    try:
        result = apply_to_artefact(
            operation, vault_root, router, path, body,
            frontmatter_changes=fm_changes,
            target=target, selector=selector, scope=scope,
        )
    except (ValueError, FileNotFoundError) as e:
        if json_mode:
            print(json.dumps({"error": str(e)}))
        else:
            print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if json_mode:
        print(json.dumps(result, indent=2))
    else:
        op_label = OPERATION_LABELS[operation]
        print(f"{op_label} {result['path']}", file=sys.stderr)


if __name__ == "__main__":
    main()
