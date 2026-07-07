import os

import pytest

from _common._artefacts import (
    BrokenParentChainError,
    CyclicParentChainError,
    StaleArtefactIndexError,
    apply_terminal_status_folder,
    descendant_entries,
    direct_child_entries,
    parent_chain_error_message,
    parent_chain_entries,
    replace_artefact_key_references,
    resolve_folder,
)


def _art(type_key, path, *, terminal_statuses=None):
    classification, prefix = type_key.split("/", 1)
    return {
        "type": type_key,
        "frontmatter_type": type_key,
        "key": f"{prefix}s",
        "path": path,
        "classification": classification,
        "frontmatter": {"terminal_statuses": terminal_statuses or []},
    }


def _entry(type_key, key, path, *, parent=None, classification=None):
    _classification, prefix = type_key.split("/", 1)
    return {
        "path": path,
        "type": type_key,
        "classification": classification,
        "type_key": f"{prefix}s",
        "type_prefix": prefix,
        "key": key,
        "parent": parent,
        "children_count": 0,
    }


def _router(index):
    return {"artefact_index": index}


class TestReplaceArtefactKeyReferences:
    def test_removes_parent_and_tag_when_new_key_is_none(self):
        fields = {
            "parent": "project/brain",
            "tags": ["brain-core", "project/brain", "wiki/reference"],
        }

        changed = replace_artefact_key_references(
            fields, "project/brain", None
        )

        assert changed is True
        assert "parent" not in fields
        assert fields["tags"] == ["brain-core", "wiki/reference"]

    def test_rewrites_parent_and_tag_when_new_key_is_present(self):
        fields = {
            "parent": "project/brain",
            "tags": ["project/brain", "wiki/reference"],
        }

        changed = replace_artefact_key_references(
            fields, "project/brain", "project/brain-two"
        )

        assert changed is True
        assert fields["parent"] == "project/brain-two"
        assert fields["tags"] == ["project/brain-two", "wiki/reference"]


class TestRecursiveOwnerFolders:
    def test_flat_same_type_direct_parent_matches_legacy_path(self):
        design = _art("living/design", "Designs")
        router = _router({
            "design/brain": _entry("living/design", "brain", "Designs/Brain.md"),
        })

        folder = resolve_folder(
            design,
            parent="design/brain",
            fields={"key": "child"},
            router=router,
        )

        assert folder == os.path.join("Designs", "brain")

    def test_living_index_checks_use_explicit_classification(self):
        design = _art("living/design", "Designs")
        router = _router({
            "design/brain": _entry(
                "custom/design",
                "brain",
                "Designs/Brain.md",
                classification="living",
            ),
        })

        folder = resolve_folder(
            design,
            parent="design/brain",
            fields={"key": "child"},
            router=router,
        )

        assert folder == os.path.join("Designs", "brain")
        assert direct_child_entries(router, "design/brain") == []

    def test_flat_cross_type_direct_parent_matches_legacy_path(self):
        idea = _art("living/idea", "Ideas")
        router = _router({
            "design/brain": _entry("living/design", "brain", "Designs/Brain.md"),
        })

        folder = resolve_folder(
            idea,
            parent="design/brain",
            fields={"key": "child"},
            router=router,
        )

        assert folder == os.path.join("Ideas", "design~brain")

    def test_same_type_parent_chain_uses_plain_key_segments(self):
        design = _art("living/design", "Designs")
        router = _router({
            "design/brain": _entry("living/design", "brain", "Designs/Brain.md"),
            "design/brain-app": _entry(
                "living/design",
                "brain-app",
                "Designs/brain/Brain App.md",
                parent="design/brain",
            ),
        })

        folder = resolve_folder(
            design,
            parent="design/brain-app",
            fields={"key": "desktop-client"},
            router=router,
        )

        assert folder == os.path.join("Designs", "brain", "brain-app")

    def test_cross_type_parent_chain_uses_type_prefixed_segments(self):
        idea = _art("living/idea", "Ideas")
        router = _router({
            "design/brain": _entry("living/design", "brain", "Designs/Brain.md"),
            "design/brain-app": _entry(
                "living/design",
                "brain-app",
                "Designs/brain/Brain App.md",
                parent="design/brain",
            ),
        })

        folder = resolve_folder(
            idea,
            parent="design/brain-app",
            fields={"key": "capture"},
            router=router,
        )

        assert folder == os.path.join("Ideas", "design~brain", "design~brain-app")

    def test_temporal_parent_chain_scopes_before_month_folder(self):
        report = _art("temporal/report", os.path.join("_Temporal", "Reports"))
        router = _router({
            "project/brain": _entry("living/project", "brain", "Projects/Brain.md"),
            "design/search": _entry(
                "living/design",
                "search",
                "Designs/project~brain/Search.md",
                parent="project/brain",
            ),
        })

        folder = resolve_folder(
            report,
            parent="design/search",
            fields={"created": "2026-07-07T09:30:00+02:00"},
            router=router,
        )

        assert folder == os.path.join(
            "_Temporal", "Reports", "project~brain", "design~search", "2026-07"
        )

    def test_temporal_parent_requires_router_for_owner_scope(self):
        report = _art("temporal/report", os.path.join("_Temporal", "Reports"))

        with pytest.raises(BrokenParentChainError, match="compiled router"):
            resolve_folder(
                report,
                parent="project/brain",
                fields={"created": "2026-07-07T09:30:00+02:00"},
            )

    def test_mixed_chain_segments_are_relative_to_target_type(self):
        design = _art("living/design", "Designs")
        router = _router({
            "design/brain": _entry("living/design", "brain", "Designs/Brain.md"),
            "project/brain-app": _entry(
                "living/project",
                "brain-app",
                "Projects/design~brain/Brain App.md",
                parent="design/brain",
            ),
        })

        folder = resolve_folder(
            design,
            parent="project/brain-app",
            fields={"key": "desktop-client"},
            router=router,
        )

        assert folder == os.path.join("Designs", "brain", "project~brain-app")

    def test_target_terminal_status_is_appended_after_owner_projection(self):
        design = _art("living/design", "Designs", terminal_statuses=["deprecated"])
        router = _router({
            "project/brain": _entry("living/project", "brain", "Projects/Brain.md"),
        })

        owner_folder = resolve_folder(
            design,
            parent="project/brain",
            fields={"key": "old-design", "status": "deprecated"},
            router=router,
        )

        assert apply_terminal_status_folder(
            owner_folder, design, {"status": "deprecated"}
        ) == os.path.join("Designs", "project~brain", "+Deprecated")

    def test_ancestor_terminal_status_does_not_propagate_to_descendant_path(self):
        design = _art("living/design", "Designs", terminal_statuses=["deprecated"])
        router = _router({
            "design/brain": _entry("living/design", "brain", "Designs/+Deprecated/Brain.md"),
            "design/brain-app": _entry(
                "living/design",
                "brain-app",
                "Designs/brain/Brain App.md",
                parent="design/brain",
            ),
        })

        folder = resolve_folder(
            design,
            parent="design/brain-app",
            fields={"key": "active-child", "status": "active"},
            router=router,
        )

        assert folder == os.path.join("Designs", "brain", "brain-app")
        assert "+Deprecated" not in folder

    def test_broken_parent_chain_is_explicit(self):
        design = _art("living/design", "Designs")
        router = _router({
            "design/brain-app": _entry(
                "living/design",
                "brain-app",
                "Designs/brain/Brain App.md",
                parent="design/missing",
            ),
        })

        with pytest.raises(BrokenParentChainError, match="design/missing"):
            resolve_folder(design, parent="design/brain-app", router=router)

    def test_temporal_parent_entry_is_invalid_for_living_projection(self):
        design = _art("living/design", "Designs")
        router = _router({
            "log/standup": _entry(
                "temporal/log",
                "standup",
                "_Temporal/Logs/2026-03/20260315-log.md",
            ),
        })

        with pytest.raises(BrokenParentChainError, match="not a living"):
            resolve_folder(design, parent="log/standup", router=router)

    def test_cyclic_parent_chain_is_explicit(self):
        design = _art("living/design", "Designs")
        router = _router({
            "design/a": _entry("living/design", "a", "Designs/A.md", parent="design/b"),
            "design/b": _entry("living/design", "b", "Designs/B.md", parent="design/a"),
        })

        with pytest.raises(CyclicParentChainError, match="Cyclic parent chain"):
            resolve_folder(design, parent="design/a", router=router)

    def test_parent_chain_entries_return_root_to_parent(self):
        router = _router({
            "design/brain": _entry("living/design", "brain", "Designs/Brain.md"),
            "project/brain-app": _entry(
                "living/project",
                "brain-app",
                "Projects/Brain App.md",
                parent="design/brain",
            ),
            "design/desktop": _entry(
                "living/design",
                "desktop",
                "Designs/brain/project~brain-app/Desktop.md",
                parent="project/brain-app",
            ),
        })

        chain = parent_chain_entries(router, "design/desktop")

        assert [entry["artefact_key"] for entry in chain] == [
            "design/brain",
            "project/brain-app",
            "design/desktop",
        ]


class TestDescendantTraversal:
    def test_direct_child_entries_are_living_direct_children_only(self):
        router = _router({
            "design/brain": _entry("living/design", "brain", "Designs/Brain.md"),
            "project/app": _entry(
                "living/project", "app", "Projects/App.md", parent="design/brain"
            ),
            "log/standup": _entry(
                "temporal/log",
                "standup",
                "_Temporal/Logs/2026-03/20260315-log.md",
                parent="design/brain",
            ),
            "design/desktop": _entry(
                "living/design",
                "desktop",
                "Designs/brain/project~app/Desktop.md",
                parent="project/app",
            ),
        })

        children = direct_child_entries(router, "design/brain")

        assert [entry["artefact_key"] for entry in children] == ["project/app"]

    def test_descendant_entries_return_parent_before_child(self):
        router = _router({
            "design/brain": _entry("living/design", "brain", "Designs/Brain.md"),
            "project/app": _entry(
                "living/project", "app", "Projects/App.md", parent="design/brain"
            ),
            "design/desktop": _entry(
                "living/design",
                "desktop",
                "Designs/brain/project~app/Desktop.md",
                parent="project/app",
            ),
            "wiki/readme": _entry(
                "living/wiki",
                "readme",
                "Wiki/design~brain/project~app/design~desktop/Readme.md",
                parent="design/desktop",
            ),
        })

        descendants = descendant_entries(router, "design/brain")

        assert [entry["artefact_key"] for entry in descendants] == [
            "project/app",
            "design/desktop",
            "wiki/readme",
        ]

    def test_descendant_entries_reject_unknown_source(self):
        with pytest.raises(StaleArtefactIndexError, match="source artefact key"):
            descendant_entries(_router({}), "project/missing")

    def test_descendant_entries_cycle_message_uses_ordered_path(self):
        router = _router({
            "design/brain": _entry("living/design", "brain", "Designs/Brain.md"),
            "project/app": _entry(
                "living/project", "app", "Projects/App.md", parent="design/brain"
            ),
            "wiki/readme": _entry(
                "living/wiki", "readme", "Wiki/Readme.md", parent="project/app"
            ),
        })
        router["artefact_index"]["design/brain"]["parent"] = "wiki/readme"

        with pytest.raises(CyclicParentChainError) as exc_info:
            descendant_entries(router, "design/brain")

        assert (
            str(exc_info.value)
            == "Cyclic descendant chain: design/brain -> project/app -> wiki/readme -> design/brain"
        )

    def test_stale_index_error_message_points_to_reconciliation(self):
        exc = StaleArtefactIndexError(
            "source artefact key is not in the compiled living index: project/missing"
        )

        message = parent_chain_error_message(exc)

        assert "Stale compiled artefact index" in message
        assert "project/missing" in message
        assert "Recompile or repair the router/index" in message

    def test_descendant_entries_reject_temporal_source(self):
        router = _router({
            "log/standup": _entry(
                "temporal/log",
                "standup",
                "_Temporal/Logs/2026-03/20260315-log.md",
            ),
        })

        with pytest.raises(BrokenParentChainError, match="not a living"):
            descendant_entries(router, "log/standup")
