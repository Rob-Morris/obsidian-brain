"""Executable specifications for deterministic repository contracts."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import sys

import check_repository_contracts as contracts
import pytest


@dataclass
class MemoryView:
    content: dict[str, str]

    def read_text(self, path: str) -> str:
        return self.content[path]

    def files(self, prefix: str) -> set[str]:
        prefix = prefix.rstrip("/")
        return {
            path
            for path in self.content
            if path == prefix or path.startswith(f"{prefix}/")
        }

    def exists(self, path: str) -> bool:
        return path in self.content


class UnreadableReadmeView(MemoryView):
    def read_text(self, path: str) -> str:
        if path == contracts.README_PATH:
            raise UnicodeError("invalid UTF-8")
        return super().read_text(path)


class UnreadableVersionView(MemoryView):
    def read_text(self, path: str) -> str:
        if path == contracts.VERSION_PATH:
            raise OSError("VERSION unavailable")
        return super().read_text(path)


def test_checkout_satisfies_all_repository_contracts():
    view = contracts.WorkingTreeView(contracts.REPO_ROOT)

    assert contracts.validate_repository(view) == []


def test_release_summary_must_match_changelog_index_verbatim():
    view = MemoryView(
        {
            contracts.VERSION_PATH: "1.2.3\n",
            "docs/changelog/v1.2.3.md": "# v1.2.3\n\n**Make release facts canonical**\n",
            "docs/CHANGELOG.md": (
                "| Version | Date | Summary |\n"
                "|---|---|---|\n"
                "| [v1.2.3](changelog/v1.2.3.md) | 2026-08-14 | Make release facts drift |\n"
            ),
        }
    )

    errors = contracts.validate_release_contract(view)

    assert errors == [
        "changelog Summary drift for v1.2.3: entry has "
        "'Make release facts canonical', index has 'Make release facts drift'"
    ]


@pytest.mark.parametrize(
    ("summary", "expected"),
    (
        ("Make release facts canonical.", "Summary must not end with a period"),
        ("Make release facts canonical (v1.2.3)", "Summary must not carry a version suffix"),
        ("Make release facts canonical v1.2.3", "Summary must not carry a version suffix"),
        ("BREAKING - Change release facts", "use the exact 'BREAKING —' prefix"),
        ("BREAKING: Change release facts", "use the exact 'BREAKING —' prefix"),
    ),
)
def test_release_summary_rejects_mechanical_format_drift(summary, expected):
    view = MemoryView(
        {
            contracts.VERSION_PATH: "1.2.3\n",
            "docs/changelog/v1.2.3.md": f"# v1.2.3\n\n**{summary}**\n",
            "docs/CHANGELOG.md": (
                "| Version | Date | Summary |\n"
                "|---|---|---|\n"
                f"| [v1.2.3](changelog/v1.2.3.md) | 2026-08-14 | {summary} |\n"
            ),
        }
    )

    assert any(expected in error for error in contracts.validate_release_contract(view))


def test_readme_badge_must_match_version():
    view = MemoryView(
        {
            contracts.VERSION_PATH: "1.2.3\n",
            "README.md": "![Version](https://img.shields.io/badge/version-1.2.2-blue)\n",
        }
    )

    assert contracts.validate_readme_version_badge(view) == [
        "README.md: version badge is 1.2.2, expected VERSION 1.2.3"
    ]


def test_readme_badge_read_failure_is_not_reported_as_success():
    view = UnreadableReadmeView(
        {
            contracts.VERSION_PATH: "1.2.3\n",
            contracts.README_PATH: "present but unreadable",
        }
    )

    assert contracts.validate_readme_version_badge(view) == [
        "README.md: cannot read: invalid UTF-8"
    ]


def test_readme_badge_version_read_failure_is_not_reported_as_success():
    view = UnreadableVersionView(
        {
            contracts.VERSION_PATH: "1.2.3\n",
            contracts.README_PATH: "![Version](version-1.2.3-blue)",
        }
    )

    assert contracts.validate_readme_version_badge(view) == [
        "src/brain-core/VERSION: cannot read: VERSION unavailable"
    ]


def test_release_version_must_have_entry_row_and_newest_position():
    missing_entry = MemoryView(
        {
            contracts.VERSION_PATH: "1.2.3\n",
            "docs/CHANGELOG.md": "# Changelog\n",
        }
    )
    assert contracts.validate_release_contract(missing_entry) == [
        "docs/changelog/v1.2.3.md: missing changelog entry for VERSION 1.2.3"
    ]

    stale_index = MemoryView(
        {
            contracts.VERSION_PATH: "1.2.3\n",
            "docs/changelog/v1.2.3.md": "# v1.2.3\n\n**Make facts canonical**\n",
            "docs/CHANGELOG.md": (
                "| Version | Date | Summary |\n"
                "|---|---|---|\n"
                "| [v1.2.2](changelog/v1.2.2.md) | 2026-08-13 | Older |\n"
                "| [v1.2.3](changelog/v1.2.3.md) | 2026-08-14 | Make facts canonical |\n"
            ),
        }
    )
    assert contracts.validate_release_contract(stale_index) == [
        "docs/CHANGELOG.md: first version row is 1.2.2, expected VERSION 1.2.3"
    ]


def test_decision_files_and_index_are_bidirectionally_complete():
    view = MemoryView(
        {
            "docs/architecture/decisions/README.md": (
                "## Chronological Index\n\n"
                "| DD | Summary | Status | File |\n"
                "|---|---|---|---|\n"
                "| DD-001 | One | Accepted | [dd-001](dd-001-one.md) |\n"
                "\n## Topic Map\n"
            ),
            "docs/architecture/decisions/dd-001-one.md": "# DD-001: One\n",
            "docs/architecture/decisions/dd-002-two.md": "# DD-002: Two\n",
        }
    )

    assert contracts.validate_decision_index(view) == [
        "docs/architecture/decisions/dd-002-two.md: missing from decisions/README.md"
    ]


def test_decision_numbers_must_be_unique_and_sequential():
    view = MemoryView(
        {
            "docs/architecture/decisions/README.md": "# Decisions\n",
            "docs/architecture/decisions/dd-002-two.md": "# DD-002: Two\n",
            "docs/architecture/decisions/dd-002-other.md": "# DD-002: Other\n",
        }
    )

    errors = contracts.validate_decision_index(view)

    assert any("DD-002: number is used" in error for error in errors)
    assert any("decision sequence has gaps: DD-001" in error for error in errors)


def test_decision_prose_link_does_not_substitute_for_chronological_row():
    view = MemoryView(
        {
            "docs/architecture/decisions/README.md": (
                "See [dd-001](dd-001-one.md).\n\n"
                "## Chronological Index\n\n"
                "| DD | Summary | Status | File |\n"
                "|---|---|---|---|\n\n"
                "## Topic Map\n"
            ),
            "docs/architecture/decisions/dd-001-one.md": "# DD-001: One\n",
        }
    )

    assert contracts.validate_decision_index(view) == [
        "docs/architecture/decisions/dd-001-one.md: missing from decisions/README.md"
    ]


def test_type_library_metadata_and_counts_are_derived_from_directories():
    view = _minimal_type_library()

    assert contracts.validate_type_library(view) == []

    del view.content[
        "src/brain-core/artefact-library/temporal/events/schema.yaml"
    ]
    errors = contracts.validate_type_library(view)
    assert errors == [
        "src/brain-core/artefact-library/temporal/events/schema.yaml: "
        "required type metadata is missing"
    ]


def test_type_schema_type_must_match_taxonomy():
    view = _minimal_type_library()
    schema_path = "src/brain-core/artefact-library/living/things/schema.yaml"
    view.content[schema_path] = view.content[schema_path].replace(
        'const: "living/thing"', 'const: "living/wrong"'
    )

    assert contracts.validate_type_library(view) == [
        "living/things: schema type 'living/wrong' does not match taxonomy type "
        "'living/thing'",
        "living/things: template type 'living/thing' does not match schema type "
        "'living/wrong'",
    ]


def test_living_key_schema_must_use_canonical_pattern():
    view = _minimal_type_library()
    schema_path = "src/brain-core/artefact-library/living/things/schema.yaml"
    view.content[schema_path] = view.content[schema_path].replace(
        contracts.CANONICAL_KEY_PATTERN,
        "^[a-z0-9]+(-[a-z0-9]+)*$",
    )

    assert contracts.validate_type_library(view) == [
        f"{schema_path}: required key pattern must equal the canonical key contract"
    ]


def test_type_template_type_must_match_schema():
    view = _minimal_type_library()
    template_path = "src/brain-core/artefact-library/living/things/template.md"
    view.content[template_path] = view.content[template_path].replace(
        "type: living/thing", "type: living/wrong"
    )

    assert contracts.validate_type_library(view) == [
        "living/things: template type 'living/wrong' does not match schema type "
        "'living/thing'",
    ]


def test_type_index_and_manifest_sources_must_cover_directories():
    view = _minimal_type_library()
    readme_path = "src/brain-core/artefact-library/README.md"
    view.content[readme_path] = view.content[readme_path].replace(
        "[Events](temporal/events/)", "[Wrong](temporal/wrong/)"
    )
    manifest_path = "src/brain-core/artefact-library/living/things/manifest.yaml"
    view.content[manifest_path] = view.content[manifest_path].replace(
        "  template:\n    source: template.md\n"
        "    target: _Config/Templates/Living/Things.md\n",
        "",
    )

    errors = contracts.validate_type_library(view)

    assert any("temporal index keys differ from directories" in error for error in errors)
    assert any("source files differ from installable files" in error for error in errors)
    assert any("manifest template target None" in error for error in errors)


def test_type_count_in_specification_must_match_library_directories():
    view = _minimal_type_library()
    view.content["docs/contributor/specification.md"] = (
        "The starter vault ships 2 defaults (1 living + 1 temporal) "
        "out of 3 in the library.\n"
    )

    assert contracts.validate_type_library(view) == [
        "artefact type-count drift: specification states 3 library types, "
        "repository contains 2"
    ]


@pytest.mark.parametrize(
    ("sentence", "expected"),
    (
        (
            "The starter vault ships 3 defaults (1 living + 1 temporal) out of 2 in the library.\n",
            "specification states 3 total defaults, repository contains 2",
        ),
        (
            "The starter vault ships 2 defaults (2 living + 1 temporal) out of 2 in the library.\n",
            "specification states 2 living defaults, repository contains 1",
        ),
        (
            "The starter vault ships 2 defaults (1 living + 2 temporal) out of 2 in the library.\n",
            "specification states 2 temporal defaults, repository contains 1",
        ),
    ),
)
def test_every_stated_default_count_is_checked(sentence, expected):
    view = _minimal_type_library()
    view.content["docs/contributor/specification.md"] = sentence

    assert any(expected in error for error in contracts.validate_type_library(view))


def test_default_markers_must_match_installed_template_taxonomies():
    view = _minimal_type_library()
    del view.content["template-vault/_Config/Taxonomy/Living/things.md"]

    assert any(
        "living default markers differ from template-vault taxonomies" in error
        for error in contracts.validate_type_library(view)
    )


def test_schema_required_template_fields_cannot_be_missing_or_empty():
    view = _minimal_type_library()
    template_path = "src/brain-core/artefact-library/temporal/events/template.md"
    original = view.content[template_path]
    view.content[template_path] = original.replace("tags:\n  - event\n", "")
    assert any(
        "template omits schema-required field 'tags'" in error
        for error in contracts.validate_type_library(view)
    )

    view.content[template_path] = original.replace("tags:\n  - event\n", "tags: []\n")
    assert any(
        "template schema-required field 'tags' is empty" in error
        for error in contracts.validate_type_library(view)
    )


def test_template_frontmatter_rejects_brace_placeholders():
    view = _minimal_type_library()
    template_path = "src/brain-core/artefact-library/living/things/template.md"
    view.content[template_path] = view.content[template_path].replace(
        "type: living/thing\n", "type: living/thing\nkey: {key}\n"
    )

    assert any(
        "template frontmatter contains a brace placeholder" in error
        for error in contracts.validate_type_library(view)
    )


def test_only_canonical_self_tag_types_may_omit_generated_tag():
    view = _minimal_type_library()
    prefix = "src/brain-core/artefact-library/living/things"
    view.content[f"{prefix}/schema.yaml"] = view.content[
        f"{prefix}/schema.yaml"
    ].replace('contains: "thing"', 'contains: "thing/{key}"')
    view.content[f"{prefix}/template.md"] = view.content[
        f"{prefix}/template.md"
    ].replace("tags:\n  - thing\n", "")

    assert any(
        "template omits schema-required field 'tags'" in error
        for error in contracts.validate_type_library(view)
    )


def test_duplicate_targets_within_one_manifest_are_rejected():
    view = _minimal_type_library()
    manifest_path = "src/brain-core/artefact-library/living/things/manifest.yaml"
    view.content[manifest_path] = view.content[manifest_path].replace(
        "target: _Config/Templates/Living/Things.md",
        "target: _Config/Taxonomy/Living/things.md",
    )

    assert any(
        "is also owned by living/things role 'taxonomy'" in error
        for error in contracts.validate_type_library(view)
    )


@pytest.mark.parametrize(
    "replacement",
    ("folders:\n  - Wrong/\n", ""),
)
def test_manifest_folders_must_include_taxonomy_storage_root(replacement):
    view = _minimal_type_library()
    manifest_path = "src/brain-core/artefact-library/living/things/manifest.yaml"
    view.content[manifest_path] = view.content[manifest_path].replace(
        "folders:\n  - Things/\n", replacement
    )

    errors = contracts.validate_type_library(view)
    assert any(
        "folders omit taxonomy storage root 'Things/'" in error
        or "folders must be a list of paths" in error
        for error in errors
    )


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    (
        (
            "target: _Config/Templates/Living/Things.md",
            "target: ../../outside.md",
            "invalid target '../../outside.md'",
        ),
        (
            "folders:\n  - Things/",
            "folders:\n  - ../Outside/",
            "invalid folder '../Outside/'",
        ),
    ),
)
def test_manifest_paths_must_be_portable_and_relative(old, new, expected):
    view = _minimal_type_library()
    manifest_path = "src/brain-core/artefact-library/living/things/manifest.yaml"
    view.content[manifest_path] = view.content[manifest_path].replace(old, new)

    assert any(
        expected in error for error in contracts.validate_type_library(view)
    )


def test_pre_commit_uses_project_python_when_path_python3_is_incompatible(tmp_path):
    _initialise_git_repo(tmp_path)
    hook = tmp_path / ".githooks/pre-commit"
    hook.parent.mkdir(parents=True)
    hook.write_text(
        (contracts.REPO_ROOT / ".githooks/pre-commit").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    hook.chmod(0o755)

    project_python = tmp_path / ".venv/bin/python"
    project_python.parent.mkdir(parents=True)
    project_python.symlink_to(sys.executable)
    checker = tmp_path / "src/scripts/check_repository_contracts.py"
    checker.parent.mkdir(parents=True)
    checker.write_text(
        "from pathlib import Path\nPath('checker-ran').write_text('yes')\n",
        encoding="utf-8",
    )
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    incompatible_python = fake_bin / "python3"
    incompatible_python.write_text("#!/bin/sh\nexit 91\n", encoding="utf-8")
    incompatible_python.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        [str(hook)], cwd=tmp_path, env=env, capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "checker-ran").read_text(encoding="utf-8") == "yes"


def test_type_status_enum_must_match_taxonomy_lifecycle():
    view = _minimal_type_library()
    prefix = "src/brain-core/artefact-library/living/things"
    view.content[f"{prefix}/taxonomy.md"] = view.content[
        f"{prefix}/taxonomy.md"
    ].replace(
        "## Naming",
        "## Lifecycle\n\n"
        "| Status | Meaning |\n"
        "|---|---|\n"
        "| `open` | Open. |\n"
        "| `done` | Done. |\n\n"
        "## Naming",
    ).replace(
        "tags:\n  - thing\n",
        "tags:\n  - thing\nstatus: open\n",
    )
    view.content[f"{prefix}/template.md"] = view.content[
        f"{prefix}/template.md"
    ].replace(
        "tags:\n  - thing\n",
        "tags:\n  - thing\nstatus: open\n",
    )
    view.content[f"{prefix}/schema.yaml"] += (
        "optional:\n"
        "  status:\n"
        "    enum: [open, closed]\n"
        "    default: open\n"
    )

    assert contracts.validate_type_library(view) == [
        "living/things: schema status enum ['open', 'closed'] does not match "
        "taxonomy lifecycle ['open', 'done']"
    ]


def test_schema_status_enum_requires_an_exact_taxonomy_lifecycle():
    view = _minimal_type_library()
    prefix = "src/brain-core/artefact-library/living/things"
    view.content[f"{prefix}/taxonomy.md"] = view.content[
        f"{prefix}/taxonomy.md"
    ].replace(
        "## Naming",
        "Open and closed are documented words, but not a lifecycle table.\n\n## Naming",
    ).replace(
        "tags:\n  - thing\n",
        "tags:\n  - thing\nstatus: open\n",
    )
    view.content[f"{prefix}/template.md"] = view.content[
        f"{prefix}/template.md"
    ].replace(
        "tags:\n  - thing\n",
        "tags:\n  - thing\nstatus: open\n",
    )
    view.content[f"{prefix}/schema.yaml"] += (
        "optional:\n"
        "  status:\n"
        "    enum: [open, closed]\n"
        "    default: open\n"
    )

    assert any(
        "schema status enum ['open', 'closed'] does not match taxonomy lifecycle []"
        in error
        for error in contracts.validate_type_library(view)
    )


def test_every_docs_markdown_file_must_be_reachable_from_root_index():
    view = MemoryView(
        {
            "docs/README.md": "[User](user/README.md)\n",
            "docs/user/README.md": "[Guide](guide.md)\n",
            "docs/user/guide.md": "# Guide\n",
            "docs/user/orphan.md": "# Orphan\n",
        }
    )

    assert contracts.validate_docs_reachability(view) == [
        "docs/user/orphan.md: not reachable from docs/README.md through documentation indexes"
    ]


def test_leaf_link_does_not_substitute_for_layer_index_entry():
    view = MemoryView(
        {
            "docs/README.md": "[User](user/README.md)\n",
            "docs/user/README.md": "[Guide](guide.md)\n",
            "docs/user/guide.md": "[Hidden](hidden.md)\n",
            "docs/user/hidden.md": "# Hidden\n",
        }
    )

    assert contracts.validate_docs_reachability(view) == [
        "docs/user/hidden.md: not reachable from docs/README.md through documentation indexes"
    ]


def test_staged_brain_core_change_requires_staged_version_bump(tmp_path):
    _initialise_git_repo(tmp_path)
    version = tmp_path / contracts.VERSION_PATH
    core_file = tmp_path / "src/brain-core/core.md"
    version.parent.mkdir(parents=True)
    version.write_text("1.0.0\n", encoding="utf-8")
    core_file.write_text("old\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "initial")

    core_file.write_text("new\n", encoding="utf-8")
    _git(tmp_path, "add", "src/brain-core/core.md")
    errors = contracts.validate_staged_version_bump(
        tmp_path, contracts.GitIndexView(tmp_path)
    )
    assert errors == [
        "src/brain-core/VERSION: must increase when src/brain-core content changes "
        "(src/brain-core/core.md)"
    ]

    version.write_text("1.0.1\n", encoding="utf-8")
    _git(tmp_path, "add", contracts.VERSION_PATH)
    assert (
        contracts.validate_staged_version_bump(
            tmp_path, contracts.GitIndexView(tmp_path)
        )
        == []
    )


def test_staged_brain_core_deletion_requires_version_bump(tmp_path):
    _initialise_versioned_core_repo(tmp_path)
    core_file = tmp_path / "src/brain-core/core.md"
    core_file.unlink()
    _git(tmp_path, "add", "-u")

    assert contracts.validate_staged_version_bump(
        tmp_path, contracts.GitIndexView(tmp_path)
    ) == [
        "src/brain-core/VERSION: must increase when src/brain-core content changes "
        "(src/brain-core/core.md)"
    ]


def test_staged_brain_core_change_rejects_version_downgrade(tmp_path):
    _initialise_versioned_core_repo(tmp_path)
    (tmp_path / "src/brain-core/core.md").write_text("new\n", encoding="utf-8")
    (tmp_path / contracts.VERSION_PATH).write_text("0.9.9\n", encoding="utf-8")
    _git(tmp_path, "add", "src/brain-core")

    assert contracts.validate_staged_version_bump(
        tmp_path, contracts.GitIndexView(tmp_path)
    ) == [
        "src/brain-core/VERSION: must increase when src/brain-core content changes "
        "(src/brain-core/core.md)"
    ]


def test_staged_new_brain_core_file_requires_version_bump(tmp_path):
    _initialise_versioned_core_repo(tmp_path)
    added = tmp_path / "src/brain-core/added.md"
    added.write_text("new\n", encoding="utf-8")
    _git(tmp_path, "add", "src/brain-core/added.md")

    assert contracts.validate_staged_version_bump(
        tmp_path, contracts.GitIndexView(tmp_path)
    ) == [
        "src/brain-core/VERSION: must increase when src/brain-core content changes "
        "(src/brain-core/added.md)"
    ]


def test_staged_decision_deletion_is_rejected(tmp_path):
    _initialise_git_repo(tmp_path)
    decision = tmp_path / "docs/architecture/decisions/dd-001-one.md"
    decision.parent.mkdir(parents=True)
    decision.write_text("# DD-001: One\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "initial")

    decision.unlink()
    _git(tmp_path, "add", "-u")

    assert contracts.validate_staged_decision_history(tmp_path) == [
        "docs/architecture/decisions/dd-001-one.md: "
        "decision records and their numbers are permanent"
    ]


def test_staged_decision_rename_keeps_number_but_cannot_change_it(tmp_path):
    _initialise_decision_repo(tmp_path)
    old = "docs/architecture/decisions/dd-001-one.md"
    same_number = "docs/architecture/decisions/dd-001-renamed.md"
    _git(tmp_path, "mv", old, same_number)
    assert contracts.validate_staged_decision_history(tmp_path) == []

    _git(tmp_path, "mv", same_number, "docs/architecture/decisions/dd-002-renamed.md")
    errors = contracts.validate_staged_decision_history(tmp_path)
    assert any("rename changes permanent DD-001 number" in error for error in errors)


def test_staged_decision_number_cannot_be_reused_for_new_record(tmp_path):
    _initialise_decision_repo(tmp_path)
    old = tmp_path / "docs/architecture/decisions/dd-001-one.md"
    old.unlink()
    replacement = tmp_path / "docs/architecture/decisions/dd-001-replacement.md"
    replacement.write_text("# Entirely new record\n" + "different\n" * 50, encoding="utf-8")
    _git(tmp_path, "add", "-A")

    errors = contracts.validate_staged_decision_history(tmp_path)

    assert any("decision records and their numbers are permanent" in error for error in errors)
    assert any("DD-001 is already assigned" in error for error in errors)


def _minimal_type_library() -> MemoryView:
    root = "src/brain-core/artefact-library"
    content = {
        f"{root}/README.md": (
            "# Artefact Library\n\n"
            "### Living\n\n"
            "| Type | Key | Description |\n"
            "|---|---|---|\n"
            "| [Things](living/things/) | `things` | Things. **Template vault default.** |\n\n"
            "### Temporal\n\n"
            "| Type | Key | Description |\n"
            "|---|---|---|\n"
            "| [Events](temporal/events/) | `events` | Events. **Template vault default.** |\n\n"
            "## Choosing a Knowledge Type\n"
        ),
        "docs/contributor/specification.md": (
            "The starter vault ships 2 defaults (1 living + 1 temporal) "
            "out of 2 in the library.\n"
        ),
        "template-vault/_Config/Taxonomy/Living/things.md": "# Things\n",
        "template-vault/_Config/Taxonomy/Temporal/events.md": "# Events\n",
    }
    for classification, key, singular, folder in (
        ("living", "things", "thing", "Living"),
        ("temporal", "events", "event", "Temporal"),
    ):
        prefix = f"{root}/{classification}/{key}"
        title = key.title()
        key_schema = (
            "  key:\n"
            "    type: string\n"
            f'    pattern: "{contracts.CANONICAL_KEY_PATTERN}"\n'
            if classification == "living"
            else ""
        )
        key_frontmatter = "key: {key}\n" if classification == "living" else ""
        content.update(
            {
                f"{prefix}/README.md": f"# {title}\n",
                f"{prefix}/manifest.yaml": (
                    "files:\n"
                    "  taxonomy:\n"
                    "    source: taxonomy.md\n"
                    f"    target: _Config/Taxonomy/{folder}/{key}.md\n"
                    "  template:\n"
                    "    source: template.md\n"
                    f"    target: _Config/Templates/{folder}/{title}.md\n"
                    "folders:\n"
                    f"  - {title}/\n"
                ),
                f"{prefix}/schema.yaml": (
                    "required:\n"
                    "  type:\n"
                    f'    const: "{classification}/{singular}"\n'
                    f"{key_schema}"
                    "  tags:\n"
                    "    type: array\n"
                    f'    contains: "{singular}"\n'
                ),
                f"{prefix}/taxonomy.md": (
                    f"# {title}\n\n"
                    "## Naming\n\n"
                    f"`{{Title}}.md` in `{title}/`.\n\n"
                    "## Frontmatter\n\n"
                    "```yaml\n"
                    "---\n"
                    f"type: {classification}/{singular}\n"
                    f"{key_frontmatter}"
                    "tags:\n"
                    f"  - {singular}\n"
                    "---\n"
                    "```\n\n"
                    "## Template\n\n"
                    f"[[_Config/Templates/{folder}/{title}]]\n"
                ),
                f"{prefix}/template.md": (
                    "---\n"
                    f"type: {classification}/{singular}\n"
                    "tags:\n"
                    f"  - {singular}\n"
                    "---\n\n"
                    + (
                        "{{agent: Also set `key:`. Delete this line once applied.}}\n\n"
                        if classification == "living"
                        else ""
                    )
                    + f"# {title}\n"
                ),
            }
        )
    return MemoryView(content)


def _initialise_git_repo(root: Path) -> None:
    _git(root, "init")
    _git(root, "config", "user.name", "Repository Contract Tests")
    _git(root, "config", "user.email", "contracts@example.invalid")


def _initialise_versioned_core_repo(root: Path) -> None:
    _initialise_git_repo(root)
    version = root / contracts.VERSION_PATH
    version.parent.mkdir(parents=True)
    version.write_text("1.0.0\n", encoding="utf-8")
    (root / "src/brain-core/core.md").write_text("old\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "initial")


def _initialise_decision_repo(root: Path) -> None:
    _initialise_git_repo(root)
    decision = root / "docs/architecture/decisions/dd-001-one.md"
    decision.parent.mkdir(parents=True)
    decision.write_text("# DD-001: One\n" + "original\n" * 50, encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "initial")


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
