#!/usr/bin/env python3
"""Guarded authoring workflows for Brain types, triggers, and plugins.

These resources define runtime behaviour and therefore do not use the generic
single-file edit surface. Replacements require an optimistic SHA-256
precondition; trigger updates identify the exact existing condition/target
pair. All paths are derived from validated slugs inside fixed definition roots.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys

import compile_router
from _common import (
    find_vault_root,
    MutationLockError,
    public_mutation_error_message,
    ROUTER_REL_PATH,
    markdown_rel_path,
    plugin_skill_rel_path,
    resolve_and_check_bounds,
    safe_write,
    slug_to_title,
    title_to_slug,
    taxonomy_rel_path,
    template_dir,
    vault_mutation_lock,
)


_CONDITIONAL_HEADING = "Conditional:"
_TRIGGER_LINE_RE = re.compile(r"^-\s+(.+?)\s+→\s+\[\[([^\]|]+)(?:\|[^\]]+)?\]\]\s*$")


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _require_within_vault(vault_root: str, path: str, label: str) -> None:
    try:
        resolve_and_check_bounds(path, vault_root)
    except ValueError as exc:
        raise ValueError(f"{label} path escapes the vault: {path}") from exc


def _normalise_document(content: str, label: str) -> str:
    if not isinstance(content, str) or not content.strip():
        raise ValueError(f"{label} definition must be non-empty markdown.")
    return content if content.endswith("\n") else content + "\n"


def _definition_path(vault_root: str, kind: str, name: str, classification=None):
    if kind == "type":
        slug = title_to_slug(name)
        if not slug or slug != name:
            raise ValueError(
                f"Type name must already be a lowercase hyphenated slug; suggested '{slug}'."
            )
        if classification not in {"living", "temporal"}:
            raise ValueError("Type classification must be 'living' or 'temporal'.")
        rel = taxonomy_rel_path(classification, slug)
    elif kind == "plugin":
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _-]*", name)
            or name in {".", ".."}
        ):
            raise ValueError(
                "Plugin name must be one safe directory name using letters, numbers, spaces, hyphens, or underscores."
            )
        rel = plugin_skill_rel_path(name)
    else:
        raise ValueError(f"Unsupported definition kind '{kind}'.")
    return rel, os.path.join(vault_root, rel)


def _validate_type_document(content: str, classification: str, name: str) -> dict:
    """Parse a candidate type document and validate its domain identity."""
    parsed = compile_router.parse_taxonomy_content(content)

    frontmatter = parsed.get("frontmatter") or {}
    type_key = frontmatter.get("type")
    if not type_key:
        raise ValueError("Type definition must declare `type:` in its ## Frontmatter YAML block.")
    prefix, separator, _leaf = type_key.partition("/")
    if separator != "/" or prefix != classification:
        raise ValueError(
            f"Type definition declares '{type_key}', but classification is '{classification}'."
        )
    if not parsed.get("naming"):
        raise ValueError("Type definition must include a parseable ## Naming contract.")
    template_path = parsed.get("template_file")
    expected_prefix = template_dir(classification) + os.sep
    if not template_path or not template_path.startswith(expected_prefix):
        raise ValueError(
            f"Type definition must link a template below {expected_prefix} in ## Template."
        )
    return {
        "type": type_key,
        "status_enum": frontmatter.get("status_enum", []),
        "template_path": markdown_rel_path(template_path),
    }


def _read_existing(path: str, rel_path: str, label: str) -> tuple[str, str]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            content = handle.read()
    except FileNotFoundError as exc:
        raise ValueError(f"{label} does not exist at {rel_path}; use create.") from exc
    return content, _sha256(content)


def _require_hash(actual: str, expected: str | None, rel_path: str) -> None:
    if not expected:
        raise ValueError(
            f"Replacing {rel_path} requires expected SHA-256 '{actual}'. "
            "Read/review the current definition, then retry with that precondition."
        )
    if expected != actual:
        raise ValueError(
            f"Definition changed at {rel_path}: expected SHA-256 {expected}, current is {actual}."
        )


def _write_type_bundle(
    vault_root: str,
    *,
    operation: str,
    name: str,
    classification: str,
    definition: str,
    template: str | None,
    expected_sha256: str | None,
    expected_template_sha256: str | None,
) -> dict:
    definition = _normalise_document(definition, "Type")
    template = _normalise_document(template, "Type template")
    rel_path, abs_path = _definition_path(vault_root, "type", name, classification)
    details = _validate_type_document(definition, classification, name)
    template_rel = details.pop("template_path")
    template_abs = os.path.join(vault_root, template_rel)
    _require_within_vault(vault_root, abs_path, "Type definition")
    _require_within_vault(vault_root, template_abs, "Type template")
    artefact_rel = (
        slug_to_title(name)
        if classification == "living"
        else os.path.join("_Temporal", slug_to_title(name))
    )
    artefact_abs = os.path.join(vault_root, artefact_rel)
    _require_within_vault(vault_root, artefact_abs, "Artefact folder")

    definition_exists = os.path.isfile(abs_path)
    template_exists = os.path.isfile(template_abs)
    if operation == "create":
        if definition_exists or template_exists:
            occupied = rel_path if definition_exists else template_rel
            raise ValueError(f"Type '{name}' already has a definition component at {occupied}.")
        if expected_sha256 or expected_template_sha256:
            raise ValueError("Expected hashes are only valid for replace.")
        before_definition = before_template = None
        before_hash = before_template_hash = None
    else:
        before_definition, before_hash = _read_existing(abs_path, rel_path, "Type definition")
        before_template, before_template_hash = _read_existing(
            template_abs, template_rel, "Type template"
        )
        _require_hash(before_hash, expected_sha256, rel_path)
        _require_hash(before_template_hash, expected_template_sha256, template_rel)

    created_folder = False
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    os.makedirs(os.path.dirname(template_abs), exist_ok=True)
    if not os.path.isdir(artefact_abs):
        os.makedirs(artefact_abs)
        created_folder = True

    try:
        safe_write(
            abs_path,
            definition,
            bounds=vault_root,
            follow_symlinks=True,
            exclusive=operation == "create",
        )
        try:
            safe_write(
                template_abs,
                template,
                bounds=vault_root,
                follow_symlinks=True,
                exclusive=operation == "create",
            )
        except BaseException:
            if operation == "replace":
                safe_write(abs_path, before_definition, bounds=vault_root)
            else:
                try:
                    os.unlink(abs_path)
                except OSError:
                    pass
            raise
    except BaseException:
        if created_folder:
            try:
                os.rmdir(artefact_abs)
            except OSError:
                pass
        raise

    return {
        "kind": "type",
        "operation": operation,
        "name": name,
        "path": rel_path,
        "template_path": template_rel,
        "artefact_folder": artefact_rel,
        "before_sha256": before_hash,
        "before_template_sha256": before_template_hash,
        "sha256": _sha256(definition),
        "template_sha256": _sha256(template),
        **details,
    }


def write_definition(
    vault_root: str,
    *,
    kind: str,
    operation: str,
    name: str,
    definition: str,
    template: str | None = None,
    classification: str | None = None,
    expected_sha256: str | None = None,
    expected_template_sha256: str | None = None,
) -> dict:
    """Create or optimistically replace one type/plugin definition."""
    if operation not in {"create", "replace"}:
        raise ValueError("Definition operation must be 'create' or 'replace'.")
    if kind == "type":
        return _write_type_bundle(
            vault_root,
            operation=operation,
            name=name,
            classification=classification,
            definition=definition,
            template=template,
            expected_sha256=expected_sha256,
            expected_template_sha256=expected_template_sha256,
        )
    if template is not None or expected_template_sha256 is not None:
        raise ValueError("Template fields are only valid for type definitions.")
    content = _normalise_document(definition, kind.capitalize())
    rel_path, abs_path = _definition_path(vault_root, kind, name, classification)
    _require_within_vault(vault_root, abs_path, f"{kind.capitalize()} definition")

    exists = os.path.isfile(abs_path)
    if operation == "create" and exists:
        raise ValueError(f"{kind.capitalize()} '{name}' already exists at {rel_path}.")
    if operation == "replace" and not exists:
        raise ValueError(f"{kind.capitalize()} '{name}' does not exist at {rel_path}; use create.")

    before_hash = None
    if exists:
        with open(abs_path, "r", encoding="utf-8") as handle:
            before = handle.read()
        before_hash = _sha256(before)
        _require_hash(before_hash, expected_sha256, rel_path)
    elif expected_sha256:
        raise ValueError("expected_sha256 is only valid for replace.")

    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    safe_write(
        abs_path,
        content,
        bounds=vault_root,
        follow_symlinks=True,
        exclusive=operation == "create",
    )
    return {
        "kind": kind,
        "operation": operation,
        "name": name,
        "path": rel_path,
        "before_sha256": before_hash,
        "sha256": _sha256(content),
    }


def _router_path(vault_root: str) -> tuple[str, str]:
    rel = ROUTER_REL_PATH
    return rel, os.path.join(vault_root, rel)


def _validate_trigger_target(vault_root: str, target: str) -> str:
    target = target.strip().removesuffix(".md")
    if not target or target.startswith(("/", ".")) or ".." in target.split("/"):
        raise ValueError("Trigger target must be a safe vault-relative wikilink target.")
    candidates = [os.path.join(vault_root, target), os.path.join(vault_root, target + ".md")]
    if not any(os.path.isfile(path) for path in candidates):
        raise ValueError(f"Trigger target does not exist: {target}")
    return target


def update_trigger(
    vault_root: str,
    *,
    operation: str,
    condition: str,
    target: str | None = None,
    new_condition: str | None = None,
    new_target: str | None = None,
) -> dict:
    """Create, replace, or delete one exact router Conditional entry."""
    if operation not in {"create", "replace", "delete"}:
        raise ValueError("Trigger operation must be create, replace, or delete.")
    condition = condition.strip()
    if not condition or "\n" in condition or "→" in condition:
        raise ValueError("Trigger condition must be one non-empty line without the → delimiter.")
    if operation in {"create", "replace"} and not (new_target or target):
        raise ValueError(f"Trigger {operation} requires target.")
    if operation != "replace" and (new_condition or new_target):
        raise ValueError("new_condition/new_target are only valid for replace.")

    rel_path, abs_path = _router_path(vault_root)
    _require_within_vault(vault_root, abs_path, "Router definition")
    try:
        with open(abs_path, "r", encoding="utf-8") as handle:
            original = handle.read()
    except FileNotFoundError as exc:
        raise ValueError(f"Router definition is missing at {rel_path}.") from exc

    lines = original.splitlines()
    try:
        heading_index = lines.index(_CONDITIONAL_HEADING)
    except ValueError as exc:
        raise ValueError(f"Router has no '{_CONDITIONAL_HEADING}' section.") from exc

    entries = []
    end_index = len(lines)
    for index in range(heading_index + 1, len(lines)):
        line = lines[index]
        match = _TRIGGER_LINE_RE.match(line)
        if match:
            entries.append((index, match.group(1).strip(), match.group(2).strip()))
            continue
        if line.strip() and not line.startswith((" ", "-")):
            end_index = index
            break

    matches = [entry for entry in entries if entry[1] == condition]
    if operation == "create":
        if matches:
            raise ValueError(f"Trigger condition already exists: {condition}")
        resolved_target = _validate_trigger_target(vault_root, target)
        lines.insert(end_index, f"- {condition} → [[{resolved_target}]]")
        result_condition, result_target = condition, resolved_target
    else:
        if len(matches) != 1:
            raise ValueError(
                f"Trigger condition must match exactly once for {operation}; found {len(matches)}."
            )
        index, _, current_target = matches[0]
        if target is not None and current_target != target.strip().removesuffix(".md"):
            raise ValueError(
                f"Trigger target precondition failed: expected '{target}', current is '{current_target}'."
            )
        if operation == "delete":
            lines.pop(index)
            result_condition, result_target = condition, None
        else:
            result_condition = (new_condition or condition).strip()
            if not result_condition or "\n" in result_condition or "→" in result_condition:
                raise ValueError("New trigger condition must be one non-empty line without →.")
            if result_condition != condition and any(entry[1] == result_condition for entry in entries):
                raise ValueError(f"Trigger condition already exists: {result_condition}")
            result_target = _validate_trigger_target(vault_root, new_target or current_target)
            lines[index] = f"- {result_condition} → [[{result_target}]]"

    updated = "\n".join(lines) + ("\n" if original.endswith("\n") else "")
    safe_write(abs_path, updated, bounds=vault_root, follow_symlinks=True)
    return {
        "kind": "trigger",
        "operation": operation,
        "condition": result_condition,
        "target": result_target,
        "path": rel_path,
        "before_sha256": _sha256(original),
        "sha256": _sha256(updated),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Guarded Brain definition workflows.")
    parser.add_argument("--vault")
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="kind", required=True)

    for kind in ("type", "plugin"):
        command = sub.add_parser(kind)
        command.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
        command.add_argument("operation", choices=("create", "replace"))
        command.add_argument("--name", required=True)
        command.add_argument("--definition-file", required=True)
        command.add_argument("--expected-sha256")
        if kind == "type":
            command.add_argument("--classification", choices=("living", "temporal"), required=True)
            command.add_argument("--template-file", required=True)
            command.add_argument("--expected-template-sha256")

    trigger = sub.add_parser("trigger")
    trigger.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    trigger.add_argument("operation", choices=("create", "replace", "delete"))
    trigger.add_argument("--condition", required=True)
    trigger.add_argument("--target")
    trigger.add_argument("--new-condition")
    trigger.add_argument("--new-target")
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    vault_root = str(find_vault_root(args.vault))
    try:
        with vault_mutation_lock(vault_root):
            if args.kind == "trigger":
                result = update_trigger(
                    vault_root,
                    operation=args.operation,
                    condition=args.condition,
                    target=args.target,
                    new_condition=args.new_condition,
                    new_target=args.new_target,
                )
            else:
                with open(args.definition_file, "r", encoding="utf-8") as handle:
                    definition = handle.read()
                template = None
                if args.kind == "type":
                    with open(args.template_file, "r", encoding="utf-8") as handle:
                        template = handle.read()
                result = write_definition(
                    vault_root,
                    kind=args.kind,
                    operation=args.operation,
                    name=args.name,
                    definition=definition,
                    template=template,
                    classification=getattr(args, "classification", None),
                    expected_sha256=args.expected_sha256,
                    expected_template_sha256=getattr(args, "expected_template_sha256", None),
                )
    except (MutationLockError, OSError, ValueError) as exc:
        message = public_mutation_error_message(exc)
        if args.json:
            print(json.dumps({"error": message}))
        else:
            print(f"Error: {message}", file=sys.stderr)
        raise SystemExit(1)

    print(json.dumps(result) if args.json else f"{args.operation}: {result['path']}")


if __name__ == "__main__":
    main()
