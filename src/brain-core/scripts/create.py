#!/usr/bin/env python3
"""Internal creation semantics for vault artefacts and named documents.

Artefact creation resolves type from the compiled router, reads the template,
generates a filename from the naming pattern, and writes the file with
frontmatter.

Resource creation (skill, memory, style, template) writes to the appropriate
_Config/ subfolder following each resource kind's conventions. Public callers
use the granular ``<resource>.create`` commands through ``command.py``; the
parser retained here is an internal maintenance and repository-test entry point.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from dataclasses import dataclass

from _resource_contract import RESOURCE_KINDS
from _staging import finalise_staged_body, resolve_mutation_body
from _lifecycle.derived_cache_state import load_fresh_compiled_router
from _common import (
    apply_terminal_status_folder,
    artefact_type_prefix,
    check_write_allowed,
    config_resource_rel_path,
    ensure_parent_tag,
    ensure_self_tag,
    ensure_tags_list,
    find_duplicate_basenames,
    find_vault_root,
    derive_distinctive_slug,
    has_leading_frontmatter,
    living_artefact_index_entry,
    living_key_set,
    make_temp_path,
    make_artefact_key,
    MissingFileResult,
    normalize_artefact_key,
    MutationLockError,
    public_mutation_error_message,
    parse_leading_frontmatter,
    parse_frontmatter,
    read_file_content,
    reconcile_fields_for_render,
    render_filename_or_default,
    resolve_and_validate_folder,
    resolve_artefact_key_entry,
    resolve_folder,
    resolve_parent_reference,
    resolve_type,
    safe_write,
    safe_write_artefact,
    serialize_frontmatter,
    substitute_template_vars,
    title_to_slug,
    unique_filename,
    validate_key,
    vault_mutation_lock,
)
import fix_links as _fix_links


def _reject_leading_body_frontmatter(body, *, resource_label):
    """Reject full-document content where a body-only payload is required."""
    if not body:
        return

    if parse_leading_frontmatter(body, allow_leading_blank_lines=True) is not None:
        raise ValueError(
            f"{resource_label} body must not start with a frontmatter block. "
            "Pass frontmatter via the dedicated frontmatter field and body content separately."
        )


def _require_full_document_frontmatter(body, *, resource_label):
    """Require a full-document markdown payload with leading frontmatter."""
    if not has_leading_frontmatter(body):
        raise ValueError(
            f"{resource_label} body must be a full markdown document starting with a frontmatter block."
        )


def _generate_key(vault_root, router, artefact, title, explicit=None):
    """Generate or validate a key value for a living artefact."""
    existing = living_key_set(vault_root, router, artefact)
    if explicit is not None:
        explicit = validate_key(explicit)
        if explicit in existing:
            raise ValueError(f"KEY_TAKEN: key '{explicit}' is already used")
        return explicit

    return derive_distinctive_slug(title, existing)


def _build_parent_context(router, artefact, fields, parent_key, parent_entry):
    """Build the advisory ``parent_context`` payload for create responses."""
    if "artefact_index" not in router:
        return None

    artefact_index = router.get("artefact_index") or {}
    if parent_key and parent_entry:
        related = [
            entry["path"]
            for entry in artefact_index.values()
            if entry.get("parent") == parent_key
        ][:3]
        return {
            "placed_under": parent_key,
            "parent_path": parent_entry["path"],
            "related": related,
            "hint": "Consider updating the parent artefact and any roadmap or child index it maintains.",
        }

    tagged = []
    for tag in ensure_tags_list(fields):
        normalized = normalize_artefact_key(tag)
        if normalized and normalized in artefact_index:
            tagged.append(
                {"key": normalized, "path": artefact_index[normalized]["path"]}
            )
    if tagged:
        return {
            "placed_under": None,
            "tagged_artefacts": tagged,
            "hint": "Tags reference other artefacts. If this artefact is owned by one, recreate or move it with parent set.",
        }

    candidate_count = sum(
        1
        for entry in artefact_index.values()
        if entry.get("type") == artefact["frontmatter_type"]
        and entry.get("children_count", 0) > 0
    )
    if candidate_count:
        return {
            "placed_under": None,
            "candidate_count": candidate_count,
            "hint": "This type has existing parent artefacts. If this artefact is owned by one, pass parent using canonical artefact-key form.",
        }
    return None


@dataclass(frozen=True, slots=True)
class ArtefactCreationPlan:
    path: str
    content: str
    title: str
    artefact: dict
    fields: dict
    parent: str | None
    parent_context: dict | None
    effective_at: str


def plan_artefact_creation(vault_root, router, type_key, title, body="",
                           frontmatter_overrides=None, parent=None, key=None,
                           template_vars=None, *, effective_at=None,
                           chosen_filename=None, chosen_key=None):
    """Resolve naming, template and parent inputs without creating directories."""
    vault_root = str(vault_root)
    _reject_leading_body_frontmatter(body, resource_label="Artefact")

    overrides = dict(frontmatter_overrides or {})
    if key is None and "key" in overrides:
        key = overrides.pop("key")

    artefact = resolve_type(router, type_key)
    template_fields, template_body = _read_template(vault_root, artefact)

    # Capture now once so filename, folder, and timestamps stay consistent.
    now = effective_at or datetime.now(timezone.utc).astimezone()
    if now.tzinfo is None:
        raise ValueError("creation time must be timezone-aware")
    now_iso = now.isoformat()

    # Seed frontmatter before filename generation so naming patterns can
    # reference template/frontmatter values such as {Version}, and so that
    # date tokens resolve from the reconciled ``created`` / ``date_source``
    # field rather than the wallclock.
    fields = dict(template_fields)
    if overrides:
        fields.update(overrides)

    if artefact.get("frontmatter") and artefact["frontmatter"].get("type"):
        fields["type"] = artefact["frontmatter"]["type"]
    if "created" not in fields:
        fields["created"] = now_iso
    if "modified" not in fields:
        fields["modified"] = now_iso

    resolved_parent = None
    parent_entry = None
    if parent:
        resolved_parent, parent_entry = resolve_parent_reference(
            vault_root, router, parent
        )

    if artefact.get("classification") == "living":
        generated_key = _generate_key(vault_root, router, artefact, title, explicit=key or chosen_key)
        fields["key"] = generated_key
        ensure_self_tag(fields, artefact_type_prefix(artefact), generated_key)
    elif key is not None:
        raise ValueError("key override only applies to living artefacts")

    if resolved_parent:
        fields["parent"] = resolved_parent
        ensure_parent_tag(fields)

    reconcile_fields_for_render(fields, artefact)
    filename = render_filename_or_default(artefact.get("naming"), title, fields)
    folder = resolve_folder(
        artefact,
        parent=resolved_parent or parent,
        router=router,
    )
    folder = apply_terminal_status_folder(folder, artefact, fields)

    final_body = body if body else template_body
    if not body and final_body:
        final_body = substitute_template_vars(final_body, template_vars, _now=now)

    basename_stem = os.path.splitext(filename)[0]

    abs_folder = os.path.join(vault_root, folder)
    if chosen_filename is not None:
        if os.path.basename(chosen_filename) != chosen_filename:
            raise ValueError("chosen creation filename must be a basename")
        filename = chosen_filename
    else:
        filename = unique_filename(abs_folder, basename_stem)
        duplicates = find_duplicate_basenames(vault_root, basename_stem, limit=1)
        folder_prefix = os.path.join(folder, "")
        if duplicates and not any(d.startswith(folder_prefix) for d in duplicates):
            current_stem = os.path.splitext(filename)[0]
            filename = f"{current_stem} ({artefact['key']}).md"

    rel_path = os.path.join(folder, filename)
    check_write_allowed(rel_path)
    abs_path = os.path.join(vault_root, rel_path)
    content = serialize_frontmatter(fields, body=final_body)
    parent_context = _build_parent_context(
        router, artefact, fields, resolved_parent, parent_entry
    )
    if os.path.lexists(abs_path):
        raise ValueError(f"Artefact destination already exists: {rel_path}")
    return ArtefactCreationPlan(rel_path, content, title, dict(artefact), fields,
                                resolved_parent, parent_context, now.isoformat())


def apply_artefact_creation(vault_root, router, plan, *, fix_links=False, file_index=None):
    """Apply the already resolved creation plan, without choosing a new identity."""
    vault_root = str(vault_root)
    rel_path, content, title = plan.path, plan.content, plan.title
    artefact, fields = plan.artefact, plan.fields
    resolved_parent, parent_context = plan.parent, plan.parent_context
    abs_path = os.path.join(vault_root, rel_path)
    safe_write_artefact(abs_path, content, bounds=vault_root, exclusive=True)

    artefact_index = router.get("artefact_index")
    if artefact_index is not None and artefact.get("classification") == "living":
        canonical_key = make_artefact_key(
            artefact_type_prefix(artefact), fields["key"]
        )
        entry = living_artefact_index_entry(artefact, rel_path, fields)
        artefact_index[canonical_key] = entry
        parent_key = entry.get("parent")
        if parent_key in artefact_index:
            indexed_parent = artefact_index[parent_key]
            indexed_parent["children_count"] = (
                indexed_parent.get("children_count", 0) + 1
            )

    result = {
        "path": rel_path,
        "type": fields["type"],
        "title": title,
    }
    if fields.get("key"):
        result["key"] = fields["key"]
    if resolved_parent:
        result["parent"] = resolved_parent
    if parent_context:
        result["parent_context"] = parent_context
    _fix_links.attach_wikilink_warnings(vault_root, result, apply_fixes=fix_links, file_index=file_index)
    return result


def create_artefact(vault_root, router, type_key, title, body="", frontmatter_overrides=None, parent=None, key=None, template_vars=None, fix_links=False, file_index=None):
    """Create an artefact through one shared planning and application path."""
    plan = plan_artefact_creation(vault_root, router, type_key, title, body,
                                  frontmatter_overrides, parent, key, template_vars)
    return apply_artefact_creation(vault_root, router, plan, fix_links=fix_links,
                                   file_index=file_index)




# ---------------------------------------------------------------------------
# Resource-aware creation (Phase 4)
# ---------------------------------------------------------------------------

CREATABLE_RESOURCES = RESOURCE_KINDS


def create_resource(vault_root, router, resource="artefact", **kwargs):
    """Create a vault resource. Dispatches to resource-specific creators.

    For artefacts: delegates to create_artefact() with type_key, title, body, etc.
    Artefact creation accepts an optional ``file_index`` kwarg (see
    ``create_artefact`` for details); it passes through via ``**kwargs``.
    For other resources: creates in the appropriate _Config/ subfolder.

    Args:
        vault_root: Absolute path to the vault root.
        router: Compiled router dict.
        resource: Resource kind — one of: artefact, skill, memory, style, template.
        **kwargs: Resource-specific params (see individual creators).

    Returns:
        Dict with path, resource (or type for artefacts), and name (or title).

    Raises:
        ValueError: If resource is not creatable or required params are missing.
    """
    if resource == "artefact":
        return create_artefact(vault_root, router, **kwargs)

    if resource not in CREATABLE_RESOURCES:
        raise ValueError(
            f"Resource '{resource}' is not creatable by a canonical command. "
            f"Creatable resources: {', '.join(CREATABLE_RESOURCES)}"
        )

    name = kwargs.get("name")
    body = kwargs.get("body")
    frontmatter = kwargs.get("frontmatter")

    if not name:
        raise ValueError(f"{resource}.create requires a name.")
    if not body:
        raise ValueError(f"{resource}.create requires content.")

    plan = plan_named_resource_creation(vault_root, router, resource, name, body, frontmatter)
    return apply_named_resource_creation(vault_root, plan)


@dataclass(frozen=True, slots=True)
class NamedResourceCreationPlan:
    path: str
    resource: str
    name: str
    content: str
    exclusive: bool


def plan_named_resource_creation(vault_root, router, resource, name, body, frontmatter=None):
    """Resolve one named document without writing it."""
    if resource not in _RESOURCE_PLANNERS:
        raise ValueError(f"Resource {resource!r} cannot be created")
    if not name or not body:
        raise ValueError(f"{resource}.create requires a name and content")
    return _RESOURCE_PLANNERS[resource](vault_root, router, name, body, frontmatter)


def _plan_config_resource(vault_root, resource, rel_path, name, body, frontmatter, exclusive=True):
    _reject_leading_body_frontmatter(body, resource_label=f"{resource.capitalize()} resource")
    check_write_allowed(rel_path)
    if exclusive and os.path.lexists(os.path.join(vault_root, rel_path)):
        raise ValueError(f"{resource.capitalize()} '{name}' already exists at {rel_path}")
    content = serialize_frontmatter(dict(frontmatter) if frontmatter else {}, body=body)
    return NamedResourceCreationPlan(rel_path, resource, name, content, exclusive)


def apply_named_resource_creation(vault_root, plan):
    """Write the same resolved target and bytes that were validated."""
    try:
        safe_write(os.path.join(vault_root, plan.path), plan.content,
                   bounds=vault_root, exclusive=plan.exclusive)
    except FileExistsError:
        raise ValueError(f"{plan.resource.capitalize()} '{plan.name}' already exists at {plan.path}") from None
    return {"path": plan.path, "resource": plan.resource, "name": plan.name}


def _plan_skill(vault_root, router, name, body, frontmatter):
    """Create a skill at _Config/Skills/{slug}/SKILL.md."""
    rel_path = config_resource_rel_path(router, "skill", name)
    slug = title_to_slug(name)
    return _plan_config_resource(vault_root, "skill", rel_path, slug, body, frontmatter)


def _plan_memory(vault_root, router, name, body, frontmatter):
    """Create a memory at _Config/Memories/{slug}.md."""
    rel_path = config_resource_rel_path(router, "memory", name)
    slug = title_to_slug(name)
    return _plan_config_resource(vault_root, "memory", rel_path, slug, body, frontmatter)


def _plan_style(vault_root, router, name, body, frontmatter):
    """Create a style at _Config/Styles/{slug}.md."""
    rel_path = config_resource_rel_path(router, "style", name)
    slug = title_to_slug(name)
    return _plan_config_resource(vault_root, "style", rel_path, slug, body, frontmatter)


def _plan_template(vault_root, router, name, body, frontmatter):
    """Create a template for an artefact type.

    name is the artefact type key (e.g. "wiki"). Resolves the classification
    and folder from the router to place the template at the correct path.
    """
    rel_path = config_resource_rel_path(router, "template", name)
    if frontmatter:
        raise ValueError(
            "Template resource create expects a full markdown document in body. "
            "Pass template frontmatter inside body, not via the frontmatter field."
        )
    _require_full_document_frontmatter(body, resource_label="Template resource")
    check_write_allowed(rel_path)
    abs_path = os.path.join(vault_root, rel_path)
    return NamedResourceCreationPlan(rel_path, "template", name, body, False)


_RESOURCE_PLANNERS = {
    "skill": _plan_skill,
    "memory": _plan_memory,
    "style": _plan_style,
    "template": _plan_template,
}

def _read_template(vault_root, artefact):
    """Read and parse the template file for an artefact type."""
    template_ref = artefact.get("template_file")
    if not template_ref:
        return {}, ""

    content = read_file_content(vault_root, template_ref)
    if isinstance(content, MissingFileResult):
        return {}, ""

    return parse_frontmatter(content)

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser():
    parser = argparse.ArgumentParser(
        description="Create a Brain artefact or configuration resource."
    )
    parser.add_argument("--resource", choices=RESOURCE_KINDS, default="artefact")
    parser.add_argument("--type", dest="type_key")
    parser.add_argument("--title")
    parser.add_argument("--name")
    parser.add_argument("--body", default="")
    parser.add_argument("--body-file", default="")
    parser.add_argument("--body-handle", default="")
    parser.add_argument("--frontmatter", help="JSON object with frontmatter overrides")
    parser.add_argument("--parent")
    parser.add_argument("--key")
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

    if args.resource == "artefact":
        if not args.type_key or not args.title:
            parser.error("resource='artefact' requires --type and --title")
        if args.name:
            parser.error("resource='artefact' does not accept --name")
    else:
        if not args.name:
            parser.error(f"resource='{args.resource}' requires --name")
        if args.type_key or args.title or args.parent or args.key or args.fix_links:
            parser.error(
                f"resource='{args.resource}' does not accept artefact-only options "
                "(--type, --title, --parent, --key, --fix-links)"
            )

    try:
        frontmatter = json.loads(args.frontmatter) if args.frontmatter else None
    except json.JSONDecodeError as exc:
        parser.error(f"--frontmatter must be a JSON object: {exc.msg}")
    if frontmatter is not None and not isinstance(frontmatter, dict):
        parser.error("--frontmatter must be a JSON object")

    vault_root = str(find_vault_root(args.vault))
    # Load router
    router = load_fresh_compiled_router(vault_root)
    if "error" in router:
        if args.json:
            print(json.dumps(router))
        else:
            print(f"Error: {router['error']}", file=sys.stderr)
        sys.exit(1)

    staging_warning = None
    try:
        with vault_mutation_lock(vault_root):
            body, _staged_handle = resolve_mutation_body(
                vault_root,
                body=args.body,
                body_file=args.body_file,
                body_handle=args.body_handle,
            )
            if args.resource == "artefact":
                result = create_resource(
                    vault_root,
                    router,
                    resource="artefact",
                    type_key=args.type_key,
                    title=args.title,
                    body=body,
                    frontmatter_overrides=frontmatter,
                    parent=args.parent,
                    key=args.key,
                    fix_links=args.fix_links,
                )
            else:
                result = create_resource(
                    vault_root,
                    router,
                    resource=args.resource,
                    name=args.name,
                    body=body,
                    frontmatter=frontmatter,
                )
            staging_warning = finalise_staged_body(vault_root, args.body_handle)
    except (MutationLockError, ValueError) as e:
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
        print(f"Created {result['path']}", file=sys.stderr)


if __name__ == "__main__":
    main()
