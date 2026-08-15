#!/usr/bin/env python3
"""Check deterministic contributor and release invariants.

The default mode validates the working tree. ``--staged`` validates the Git
index and adds predicates that depend on the change relative to ``HEAD``.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import posixpath
from pathlib import Path
import re
import subprocess
import sys
from typing import Iterable, Protocol
from urllib.parse import unquote


REPO_ROOT = Path(__file__).resolve().parents[2]
BRAIN_SCRIPTS = REPO_ROOT / "src" / "brain-core" / "scripts"
if str(BRAIN_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(BRAIN_SCRIPTS))

from _common import (  # noqa: E402
    AGENT_INSTRUCTION_RE,
    CANONICAL_KEY_PATTERN,
    FM_RE,
    decode_definition_manifest,
    resolve_structural_target,
)
from _common._yaml import YamlError, load_mapping_text  # noqa: E402
from _common._frontmatter import parse_frontmatter  # noqa: E402
from compile_router import naming_storage_root, parse_taxonomy_content  # noqa: E402


VERSION_PATH = "src/brain-core/VERSION"
README_PATH = "README.md"
LIBRARY_ROOT = "src/brain-core/artefact-library"
DECISIONS_ROOT = "docs/architecture/decisions"
VERSION_RE = re.compile(
    r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
)
DD_PATH_RE = re.compile(r"docs/architecture/decisions/dd-(\d{3})-[^/]+\.md")
DD_ROW_LINK_RE = re.compile(r"\[dd-(\d{3})\]\((dd-(\d{3})-[^)]+\.md)\)")
MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")


class RepositoryView(Protocol):
    """Read-only view over either the checkout or the staged snapshot."""

    def read_text(self, path: str) -> str: ...

    def files(self, prefix: str) -> set[str]: ...

    def exists(self, path: str) -> bool: ...


@dataclass(frozen=True)
class WorkingTreeView:
    root: Path

    def read_text(self, path: str) -> str:
        return (self.root / path).read_text(encoding="utf-8")

    def files(self, prefix: str) -> set[str]:
        base = self.root / prefix
        if base.is_file():
            return {prefix}
        if not base.is_dir():
            return set()
        return {
            path.relative_to(self.root).as_posix()
            for path in base.rglob("*")
            if path.is_file()
        }

    def exists(self, path: str) -> bool:
        return (self.root / path).is_file()


class GitIndexView:
    """Repository content exactly as it would be committed."""

    def __init__(self, root: Path):
        self.root = root
        output = _git_bytes(root, "ls-files", "-s", "-z")
        path_oids: dict[str, str] = {}
        for record in output.split(b"\0"):
            if not record:
                continue
            metadata, raw_path = record.split(b"\t", 1)
            _mode, raw_oid, _stage = metadata.split(b" ", 2)
            path_oids[raw_path.decode("utf-8")] = raw_oid.decode("ascii")
        self._paths = set(path_oids)
        text_paths = {
            path: oid
            for path, oid in path_oids.items()
            if path == VERSION_PATH
            or path.startswith("docs/")
            or path.startswith(f"{LIBRARY_ROOT}/")
        }
        self._text = _read_git_blobs(root, text_paths)

    def read_text(self, path: str) -> str:
        cached = self._text.get(path)
        if cached is not None:
            return cached
        return _git_text(self.root, "show", f":{path}")

    def files(self, prefix: str) -> set[str]:
        prefix = prefix.rstrip("/")
        return {
            path
            for path in self._paths
            if path == prefix or path.startswith(f"{prefix}/")
        }

    def exists(self, path: str) -> bool:
        return path in self._paths


def _git_text(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _git_bytes(root: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        input=input_bytes,
    )
    return result.stdout


def _read_git_blobs(root: Path, path_oids: dict[str, str]) -> dict[str, str]:
    """Read a bounded set of index blobs through one Git process."""
    if not path_oids:
        return {}
    unique_oids = list(dict.fromkeys(path_oids.values()))
    output = _git_bytes(
        root,
        "cat-file",
        "--batch",
        input_bytes=("\n".join(unique_oids) + "\n").encode("ascii"),
    )
    by_oid: dict[str, str] = {}
    offset = 0
    for expected_oid in unique_oids:
        header_end = output.index(b"\n", offset)
        header = output[offset:header_end].decode("ascii")
        actual_oid, object_type, raw_size = header.split()
        if actual_oid != expected_oid or object_type != "blob":
            raise RuntimeError(f"unexpected git cat-file response: {header}")
        size = int(raw_size)
        start = header_end + 1
        end = start + size
        by_oid[expected_oid] = output[start:end].decode("utf-8")
        offset = end + 1
    return {path: by_oid[oid] for path, oid in path_oids.items()}


def _read(view: RepositoryView, path: str, errors: list[str]) -> str | None:
    try:
        return view.read_text(path)
    except (OSError, UnicodeError, subprocess.CalledProcessError) as exc:
        errors.append(f"{path}: cannot read: {exc}")
        return None


def validate_release_contract(view: RepositoryView) -> list[str]:
    """Validate VERSION and its canonical changelog summary."""
    errors: list[str] = []
    raw_version = _read(view, VERSION_PATH, errors)
    if raw_version is None:
        return errors
    version = raw_version.strip()
    if not VERSION_RE.fullmatch(version):
        return [f"{VERSION_PATH}: invalid semantic version {version!r}"]

    entry_path = f"docs/changelog/v{version}.md"
    if not view.exists(entry_path):
        return [f"{entry_path}: missing changelog entry for VERSION {version}"]
    entry = _read(view, entry_path, errors)
    index = _read(view, "docs/CHANGELOG.md", errors)
    if entry is None or index is None:
        return errors

    summary = _version_summary(entry, version)
    if summary is None:
        errors.append(
            f"{entry_path}: first content after '# v{version}' must be the top-line Summary"
        )
        return errors

    rows = []
    row_re = re.compile(
        rf"^\|\s*\[v{re.escape(version)}\]\(changelog/v{re.escape(version)}\.md\)"
        r"\s*\|\s*[^|]+\|\s*(.*?)\s*\|\s*$",
        re.MULTILINE,
    )
    rows = row_re.findall(index)
    if len(rows) != 1:
        errors.append(
            f"docs/CHANGELOG.md: expected exactly one row for v{version}, found {len(rows)}"
        )
        return errors

    index_summary = _plain_summary(rows[0])
    if summary != index_summary:
        errors.append(
            f"changelog Summary drift for v{version}: entry has {summary!r}, "
            f"index has {index_summary!r}"
        )

    version_rows = re.findall(
        r"^\|\s*\[v(\d+\.\d+\.\d+)\]\(changelog/v[^)]+\)",
        index,
        re.MULTILINE,
    )
    if not version_rows or version_rows[0] != version:
        first = version_rows[0] if version_rows else "none"
        errors.append(
            f"docs/CHANGELOG.md: first version row is {first}, expected VERSION {version}"
        )

    if summary.endswith("."):
        errors.append(f"{entry_path}: Summary must not end with a period")
    if re.search(r"\s+(?:as\s+)?\(?v\d+\.\d+\.\d+\)?$", summary, re.I):
        errors.append(f"{entry_path}: Summary must not carry a version suffix")
    if re.match(r"^BREAKING\b", summary) and not summary.startswith("BREAKING — "):
        errors.append(f"{entry_path}: use the exact 'BREAKING —' prefix")
    return errors


def validate_readme_version_badge(view: RepositoryView) -> list[str]:
    """Require the public README badge to display the canonical VERSION."""
    if not view.exists(README_PATH):
        return [f"{README_PATH}: missing repository README"]
    errors: list[str] = []
    raw_version = _read(view, VERSION_PATH, errors)
    if raw_version is None:
        return errors
    version = raw_version.strip()
    if not VERSION_RE.fullmatch(version):
        return []
    readme = _read(view, README_PATH, errors)
    if readme is None:
        return errors
    badges = re.findall(r"version-(\d+\.\d+\.\d+)-blue", readme)
    if len(badges) != 1:
        return [f"{README_PATH}: expected exactly one version badge, found {len(badges)}"]
    if badges[0] != version:
        return [
            f"{README_PATH}: version badge is {badges[0]}, expected VERSION {version}"
        ]
    return []


def _version_summary(entry: str, version: str) -> str | None:
    lines = entry.splitlines()
    try:
        heading_index = lines.index(f"# v{version}")
    except ValueError:
        return None
    for line in lines[heading_index + 1 :]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("**Summary:**"):
            return stripped.removeprefix("**Summary:**").strip()
        if stripped.startswith("Summary:"):
            return stripped.removeprefix("Summary:").strip()
        if stripped.startswith("**") and stripped.endswith("**"):
            return stripped[2:-2]
        return None
    return None


def _plain_summary(summary: str) -> str:
    summary = summary.strip()
    if summary.startswith("**") and summary.endswith("**"):
        summary = summary[2:-2]
    return re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", summary)


def validate_decision_index(view: RepositoryView) -> list[str]:
    """Require a one-to-one, sequential DD file/index mapping."""
    errors: list[str] = []
    all_decision_files = {
        path
        for path in view.files(DECISIONS_ROOT)
        if path.endswith(".md") and Path(path).name != "README.md"
    }
    malformed = sorted(path for path in all_decision_files if not DD_PATH_RE.fullmatch(path))
    errors.extend(f"{path}: decision filename must match dd-NNN-slug.md" for path in malformed)

    by_number: dict[int, list[str]] = {}
    for path in sorted(all_decision_files - set(malformed)):
        number = int(DD_PATH_RE.fullmatch(path).group(1))  # type: ignore[union-attr]
        by_number.setdefault(number, []).append(path)
    for number, paths in by_number.items():
        if len(paths) > 1:
            errors.append(f"DD-{number:03d}: number is used by {', '.join(paths)}")

    if by_number:
        expected = set(range(1, max(by_number) + 1))
        missing = sorted(expected - set(by_number))
        if missing:
            rendered = ", ".join(f"DD-{number:03d}" for number in missing)
            errors.append(f"{DECISIONS_ROOT}: decision sequence has gaps: {rendered}")

    index_path = f"{DECISIONS_ROOT}/README.md"
    index = _read(view, index_path, errors)
    if index is None:
        return errors
    indexed_by_number: dict[int, list[str]] = {}
    chronological = _markdown_section(index, "## Chronological Index")
    for line in chronological.splitlines():
        if not re.match(r"^\|\s*DD-\d{3}\s*\|", line):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        row_number = re.fullmatch(r"DD-(\d{3})", cells[0])
        link = DD_ROW_LINK_RE.fullmatch(cells[-1])
        if row_number is None or link is None:
            errors.append(f"{index_path}: malformed chronological row: {line}")
            continue
        row_value = row_number.group(1)
        label_number, filename, path_number = link.groups()
        if len({row_value, label_number, path_number}) != 1:
            errors.append(
                f"{index_path}: row DD-{row_value}, link DD-{label_number}, "
                f"and filename DD-{path_number} must match"
            )
        indexed_by_number.setdefault(int(row_value), []).append(
            f"{DECISIONS_ROOT}/{filename}"
        )
    for number, paths in indexed_by_number.items():
        if len(paths) > 1:
            errors.append(f"{index_path}: DD-{number:03d} has {len(paths)} index rows")

    file_mapping = {number: paths[0] for number, paths in by_number.items() if len(paths) == 1}
    index_mapping = {
        number: paths[0]
        for number, paths in indexed_by_number.items()
        if len(paths) == 1
    }
    for number in sorted(set(file_mapping) | set(index_mapping)):
        file_path = file_mapping.get(number)
        index_path_value = index_mapping.get(number)
        if file_path is None:
            errors.append(f"DD-{number:03d}: indexed as {index_path_value} but file is missing")
        elif index_path_value is None:
            errors.append(f"{file_path}: missing from decisions/README.md")
        elif file_path != index_path_value:
            errors.append(
                f"DD-{number:03d}: file/index mismatch: {file_path} != {index_path_value}"
            )
    return errors


def validate_type_library(view: RepositoryView) -> list[str]:
    """Validate artefact-library counts, indexes, manifests, and schemas."""
    errors: list[str] = []
    readme_path = f"{LIBRARY_ROOT}/README.md"
    readme = _read(view, readme_path, errors)
    specification = _read(view, "docs/contributor/specification.md", errors)
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

        section = _markdown_section(readme, f"### {classification.title()}")
        indexed_keys = re.findall(rf"\({classification}/([^/)]+)/\)", section)
        if set(indexed_keys) != set(keys) or len(indexed_keys) != len(keys):
            errors.append(
                f"{readme_path}: {classification} index keys differ from directories: "
                f"index={indexed_keys!r}, directories={keys!r}"
            )
        marked_defaults = {
            match.group(1)
            for line in section.splitlines()
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
            "docs/contributor/specification.md: missing canonical starter/library type-count sentence"
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
            errors.append(f"{type_key}: template schema-required field {field!r} is empty")
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
    schema_statuses = status_rule.get("enum", []) if isinstance(status_rule, dict) else []
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


def _markdown_section(text: str, heading: str) -> str:
    try:
        resolved = resolve_structural_target(text, heading)
    except ValueError:
        return ""
    start, end = resolved["ranges"]["body"]
    return text[start:end]


def validate_docs_reachability(view: RepositoryView) -> list[str]:
    """Require every docs Markdown file to be reachable from docs/README.md."""
    docs_files = {path for path in view.files("docs") if path.endswith(".md")}
    root = "docs/README.md"
    if root not in docs_files:
        return [f"{root}: documentation root index is missing"]

    reachable: set[str] = set()
    pending = [root]
    while pending:
        current = pending.pop()
        if current in reachable:
            continue
        reachable.add(current)
        if not _is_documentation_router(current):
            continue
        text = view.read_text(current)
        for target in _local_markdown_targets(current, text, docs_files):
            if target not in reachable:
                pending.append(target)

    return [
        f"{path}: not reachable from docs/README.md through documentation indexes"
        for path in sorted(docs_files - reachable)
    ]


def _is_documentation_router(path: str) -> bool:
    return path.endswith("/README.md") or path == "docs/CHANGELOG.md"


def _local_markdown_targets(
    source: str,
    text: str,
    docs_files: set[str],
) -> Iterable[str]:
    source_dir = posixpath.dirname(source)
    for raw_target in MARKDOWN_LINK_RE.findall(text):
        raw_target = raw_target.strip()
        if raw_target.startswith("<") and ">" in raw_target:
            raw_target = raw_target[1 : raw_target.index(">")]
        else:
            raw_target = raw_target.split(maxsplit=1)[0]
        if raw_target.startswith(("#", "/")) or "://" in raw_target or raw_target.startswith("mailto:"):
            continue
        raw_target = unquote(raw_target.split("#", 1)[0])
        if not raw_target:
            continue
        target = posixpath.normpath(posixpath.join(source_dir, raw_target))
        if target in docs_files:
            yield target
        elif f"{target.rstrip('/')}/README.md" in docs_files:
            yield f"{target.rstrip('/')}/README.md"


@dataclass(frozen=True)
class GitChange:
    status: str
    paths: tuple[str, ...]


def _staged_changes(root: Path) -> list[GitChange]:
    output = _git_bytes(
        root,
        "diff",
        "--cached",
        "--name-status",
        "--find-renames",
        "-z",
    )
    fields = [field.decode("utf-8") for field in output.split(b"\0") if field]
    changes: list[GitChange] = []
    index = 0
    while index < len(fields):
        status = fields[index]
        path_count = 2 if status.startswith(("R", "C")) else 1
        paths = tuple(fields[index + 1 : index + 1 + path_count])
        if len(paths) != path_count:
            raise RuntimeError(f"truncated git name-status record for {status}")
        changes.append(GitChange(status=status, paths=paths))
        index += path_count + 1
    return changes


def _head_oid(root: Path) -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    if "Needed a single revision" in result.stderr or "unknown revision" in result.stderr:
        return None
    raise subprocess.CalledProcessError(
        result.returncode,
        result.args,
        output=result.stdout,
        stderr=result.stderr,
    )


def _head_version(root: Path) -> str | None:
    if _head_oid(root) is None:
        return None
    tracked = _git_text(root, "ls-tree", "--name-only", "HEAD", "--", VERSION_PATH).strip()
    if not tracked:
        return None
    return _git_text(root, "show", f"HEAD:{VERSION_PATH}").strip()


def validate_staged_version_bump(root: Path, view: GitIndexView) -> list[str]:
    """Require a staged VERSION change whenever staged brain-core content changes."""
    paths = {path for change in _staged_changes(root) for path in change.paths}
    core_changes = {
        path
        for path in paths
        if path.startswith("src/brain-core/") and path != VERSION_PATH
    }
    if not core_changes:
        return []
    head_version = _head_version(root)
    staged_version = view.read_text(VERSION_PATH).strip()
    if head_version is not None and (
        not VERSION_RE.fullmatch(head_version)
        or not VERSION_RE.fullmatch(staged_version)
        or _version_tuple(staged_version) <= _version_tuple(head_version)
    ):
        sample = ", ".join(sorted(core_changes)[:3])
        suffix = " ..." if len(core_changes) > 3 else ""
        return [
            f"{VERSION_PATH}: must increase when src/brain-core content changes "
            f"({sample}{suffix})"
        ]
    return []


def _version_tuple(version: str) -> tuple[int, int, int]:
    return tuple(int(part) for part in version.split("."))  # type: ignore[return-value]


def validate_staged_decision_history(root: Path) -> list[str]:
    """Prevent deletion or reuse of an established DD number."""
    changes = [
        change
        for change in _staged_changes(root)
        if any(path.startswith(f"{DECISIONS_ROOT}/") for path in change.paths)
    ]
    errors: list[str] = []
    for change in changes:
        if change.status.startswith("R") and len(change.paths) == 2:
            old_match = DD_PATH_RE.fullmatch(change.paths[0])
            new_match = DD_PATH_RE.fullmatch(change.paths[1])
            if old_match and new_match and old_match.group(1) != new_match.group(1):
                errors.append(
                    f"{change.paths[1]}: rename changes permanent DD-{old_match.group(1)} number"
                )
        elif change.status == "D" and DD_PATH_RE.fullmatch(change.paths[0]):
            errors.append(
                f"{change.paths[0]}: decision records and their numbers are permanent"
            )

    head_paths = _git_text(
        root,
        "ls-tree",
        "-r",
        "--name-only",
        "HEAD",
        "--",
        DECISIONS_ROOT,
    ).splitlines()
    head_by_number = {
        match.group(1): path
        for path in head_paths
        if (match := DD_PATH_RE.fullmatch(path))
    }
    staged_paths = _git_text(root, "ls-files", DECISIONS_ROOT).splitlines()
    for path in staged_paths:
        match = DD_PATH_RE.fullmatch(path)
        if not match:
            continue
        previous = head_by_number.get(match.group(1))
        if previous and previous != path:
            rename_line = any(
                change.status.startswith("R")
                and change.paths == (previous, path)
                for change in changes
            )
            if not rename_line:
                errors.append(
                    f"{path}: DD-{match.group(1)} is already assigned to {previous}"
                )
    return errors


def validate_repository(view: RepositoryView) -> list[str]:
    validators = (
        validate_release_contract,
        validate_readme_version_badge,
        validate_decision_index,
        validate_type_library,
        validate_docs_reachability,
    )
    return [error for validator in validators for error in validator(view)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--staged",
        action="store_true",
        help="validate the Git index and staged-change predicates",
    )
    args = parser.parse_args(argv)

    view: RepositoryView
    if args.staged:
        staged_view = GitIndexView(REPO_ROOT)
        view = staged_view
        errors = validate_staged_version_bump(REPO_ROOT, staged_view)
        errors.extend(validate_staged_decision_history(REPO_ROOT))
    else:
        view = WorkingTreeView(REPO_ROOT)
        errors = []
    errors.extend(validate_repository(view))

    if errors:
        print("repository contracts failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print("repository contracts: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
