"""Artefact-library manifest, taxonomy, schema, template, and count policy."""

from __future__ import annotations

import re

from _common import (
    AGENT_INSTRUCTION_RE,
    CANONICAL_KEY_PATTERN,
    FM_RE,
    decode_definition_manifest,
)
from _common._frontmatter import parse_frontmatter
from _common._yaml import YamlError, load_mapping_text
from compile_router import naming_storage_root, parse_taxonomy_content

from .markdown import section as markdown_section
from .view import RepositoryView, read


LIBRARY_ROOT = "src/brain-core/artefact-library"


def validate_type_library(view: RepositoryView) -> list[str]:
    """Validate artefact-library counts, indexes, manifests, and schemas."""
    errors: list[str] = []
    readme_path = f"{LIBRARY_ROOT}/README.md"
    readme = read(view, readme_path, errors)
    specification = read(view, "docs/contributor/specification.md", errors)
    if readme is None or specification is None:
        return errors

    library_counts: dict[str, int] = {}
    default_counts: dict[str, int] = {}
    all_manifest_targets: dict[str, tuple[str, str]] = {}
    for classification in ("living", "temporal"):
        class_prefix = f"{LIBRARY_ROOT}/{classification}"
        class_files = view.files(class_prefix)
        keys = sorted(
            {
                path.removeprefix(f"{class_prefix}/").split("/", 1)[0]
                for path in class_files
                if path.startswith(f"{class_prefix}/")
                and "/" in path.removeprefix(f"{class_prefix}/")
            }
        )
        library_counts[classification] = len(keys)

        library_section = markdown_section(
            readme, f"### {classification.title()}"
        )
        indexed_keys = re.findall(
            rf"\({classification}/([^/)]+)/\)", library_section
        )
        if set(indexed_keys) != set(keys) or len(indexed_keys) != len(keys):
            errors.append(
                f"{readme_path}: {classification} index keys differ from directories: "
                f"index={indexed_keys!r}, directories={keys!r}"
            )
        marked_defaults = {
            match.group(1)
            for line in library_section.splitlines()
            if "**Template vault default.**" in line
            and (match := re.search(rf"\({classification}/([^/)]+)/\)", line))
        }
        installed_defaults = {
            key
            for key in keys
            if view.exists(
                f"template-vault/_Config/Taxonomy/{classification.title()}/{key}.md"
            )
        }
        default_counts[classification] = len(installed_defaults)
        if marked_defaults != installed_defaults:
            errors.append(
                f"{readme_path}: {classification} default markers differ from "
                f"template-vault taxonomies: marked={sorted(marked_defaults)!r}, "
                f"installed={sorted(installed_defaults)!r}"
            )
        for key in keys:
            _validate_type_directory(
                view,
                classification,
                key,
                all_manifest_targets,
                errors,
            )

    count_match = re.search(
        r"starter vault ships (\d+) defaults \((\d+) living \+ (\d+) temporal\) "
        r"out of (\d+) in the library",
        specification,
        re.IGNORECASE,
    )
    if not count_match:
        errors.append(
            "docs/contributor/specification.md: missing canonical "
            "starter/library type-count sentence"
        )
    else:
        stated_total, stated_living, stated_temporal, stated_library = map(
            int, count_match.groups()
        )
        actual_living = default_counts["living"]
        actual_temporal = default_counts["temporal"]
        actual_total = actual_living + actual_temporal
        actual_library = library_counts["living"] + library_counts["temporal"]
        comparisons = (
            ("total defaults", stated_total, actual_total),
            ("living defaults", stated_living, actual_living),
            ("temporal defaults", stated_temporal, actual_temporal),
            ("library types", stated_library, actual_library),
        )
        for label, stated, actual in comparisons:
            if stated == actual:
                continue
            errors.append(
                f"artefact type-count drift: specification states {stated} {label}, "
                f"repository contains {actual}"
            )
    return errors


def _validate_type_directory(
    view: RepositoryView,
    classification: str,
    key: str,
    all_manifest_targets: dict[str, tuple[str, str]],
    errors: list[str],
) -> None:
    type_key = f"{classification}/{key}"
    prefix = f"{LIBRARY_ROOT}/{type_key}"
    required_files = {
        "README.md",
        "manifest.yaml",
        "schema.yaml",
        "taxonomy.md",
        "template.md",
    }
    filenames = {
        path.removeprefix(f"{prefix}/")
        for path in view.files(prefix)
        if "/" not in path.removeprefix(f"{prefix}/")
    }
    for filename in sorted(required_files - filenames):
        errors.append(f"{prefix}/{filename}: required type metadata is missing")
    if required_files - filenames:
        return

    manifest_path = f"{prefix}/manifest.yaml"
    schema_path = f"{prefix}/schema.yaml"
    taxonomy_path = f"{prefix}/taxonomy.md"
    try:
        manifest = decode_definition_manifest(
            load_mapping_text(view.read_text(manifest_path), source=manifest_path)
        )
        schema = load_mapping_text(view.read_text(schema_path), source=schema_path)
        taxonomy_text = view.read_text(taxonomy_path)
        taxonomy = parse_taxonomy_content(taxonomy_text)
        template_text = view.read_text(f"{prefix}/template.md")
        template_fields, _template_body = parse_frontmatter(template_text)
    except (OSError, UnicodeError, YamlError, ValueError) as exc:
        errors.append(f"{prefix}: cannot parse type metadata: {exc}")
        return

    manifest_files = manifest["files"]
    sources = {
        metadata.get("source")
        for metadata in manifest_files.values()
        if isinstance(metadata, dict)
    }
    installable_files = filenames - {"README.md", "manifest.yaml", "schema.yaml"}
    if sources != installable_files:
        errors.append(
            f"{manifest_path}: source files differ from installable files: "
            f"manifest={sorted(str(item) for item in sources)!r}, "
            f"directory={sorted(installable_files)!r}"
        )

    expected_taxonomy_target = (
        f"_Config/Taxonomy/{classification.title()}/{key}.md"
    )
    taxonomy_target = _manifest_target(manifest_files, "taxonomy")
    if taxonomy_target != expected_taxonomy_target:
        errors.append(
            f"{manifest_path}: taxonomy target is {taxonomy_target!r}, "
            f"expected {expected_taxonomy_target!r}"
        )
    template_target = _manifest_target(manifest_files, "template")
    taxonomy_template = taxonomy.get("template_file")
    if template_target is None or template_target.removesuffix(".md") != taxonomy_template:
        errors.append(
            f"{type_key}: manifest template target {template_target!r} does not match "
            f"taxonomy template link {taxonomy_template!r}"
        )

    manifest_folders = manifest["folders"]
    naming = taxonomy.get("naming") or {}
    naming_folder = naming.get("folder") if isinstance(naming, dict) else None
    expected_storage_root = naming_storage_root(naming_folder)
    if expected_storage_root is None:
        errors.append(f"{type_key}: taxonomy naming folder is missing or invalid")
    elif expected_storage_root not in manifest_folders:
        errors.append(
            f"{manifest_path}: folders omit taxonomy storage root "
            f"{expected_storage_root!r}"
        )

    for role, metadata in manifest_files.items():
        target = metadata["target"]
        owner = (type_key, role)
        previous = all_manifest_targets.setdefault(target, owner)
        if previous != owner:
            errors.append(
                f"{manifest_path}: target {target!r} is also owned by "
                f"{previous[0]} role {previous[1]!r}"
            )

    frontmatter = taxonomy.get("frontmatter") or {}
    schema_required = schema.get("required", {})
    schema_optional = schema.get("optional", {})
    if not isinstance(schema_required, dict) or not isinstance(schema_optional, dict):
        errors.append(f"{schema_path}: required and optional must be mappings")
        return
    schema_fields = set(schema_required) | set(schema_optional)
    for field in sorted(schema_fields):
        if not re.search(rf"(?<![\w-]){re.escape(field)}(?![\w-])", taxonomy_text):
            errors.append(
                f"{type_key}: schema field {field!r} is not documented by taxonomy.md"
            )

    if classification == "living":
        key_rule = schema_required.get("key")
        if (
            not isinstance(key_rule, dict)
            or key_rule.get("type") != "string"
            or key_rule.get("pattern") != CANONICAL_KEY_PATTERN
        ):
            errors.append(
                f"{schema_path}: required key pattern must equal the canonical key contract"
            )

    template_frontmatter = FM_RE.match(template_text)
    if template_frontmatter and re.search(
        r"\{[^{}\n]+\}", template_frontmatter.group(1)
    ):
        errors.append(f"{type_key}: template frontmatter contains a brace placeholder")
    hint_text = " ".join(AGENT_INSTRUCTION_RE.findall(template_text))
    hinted_fields = set(re.findall(r"`([a-z_][a-z0-9_-]*):`", hint_text))
    rendered_fields = set(
        re.findall(r"\{\{([a-z_][a-z0-9_-]*):", template_text)
    )
    deferred_fields = hinted_fields | rendered_fields
    missing_required = set(schema_required) - set(template_fields) - hinted_fields
    for field in sorted(missing_required):
        errors.append(f"{type_key}: template omits schema-required field {field!r}")

    type_rule = schema_required.get("type", {})
    schema_type = type_rule.get("const") if isinstance(type_rule, dict) else None
    if schema_type != frontmatter.get("type"):
        errors.append(
            f"{type_key}: schema type {schema_type!r} does not match "
            f"taxonomy type {frontmatter.get('type')!r}"
        )
    template_type = template_fields.get("type")
    if template_type != schema_type:
        errors.append(
            f"{type_key}: template type {template_type!r} does not match "
            f"schema type {schema_type!r}"
        )

    for field, value in template_fields.items():
        if field == "type":
            continue
        rule = schema_required.get(field, schema_optional.get(field))
        if not isinstance(rule, dict):
            continue
        if (
            field in schema_required
            and field not in deferred_fields
            and value in (None, "", [])
        ):
            errors.append(
                f"{type_key}: template schema-required field {field!r} is empty"
            )
            continue
        if "const" in rule and value != rule["const"]:
            errors.append(
                f"{type_key}: template {field} value {value!r} does not match "
                f"schema const {rule['const']!r}"
            )
        if "enum" in rule and value not in ([], "") and value not in rule["enum"]:
            errors.append(
                f"{type_key}: template {field} value {value!r} is outside "
                f"schema enum {rule['enum']!r}"
            )
        if "contains" in rule and (
            not isinstance(value, list) or rule["contains"] not in value
        ):
            errors.append(
                f"{type_key}: template {field} omits schema-required "
                f"value {rule['contains']!r}"
            )

    taxonomy_statuses = frontmatter.get("status_enum") or []
    status_rule = schema_required.get("status", schema_optional.get("status"))
    schema_statuses = (
        status_rule.get("enum", []) if isinstance(status_rule, dict) else []
    )
    if taxonomy_statuses != schema_statuses:
        errors.append(
            f"{type_key}: schema status enum {schema_statuses!r} does not match "
            f"taxonomy lifecycle {taxonomy_statuses!r}"
        )


def _manifest_target(manifest_files: dict, role: str) -> str | None:
    metadata = manifest_files.get(role)
    if not isinstance(metadata, dict):
        return None
    target = metadata.get("target")
    return target if isinstance(target, str) else None
