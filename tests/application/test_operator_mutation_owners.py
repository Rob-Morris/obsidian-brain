"""Owner behaviour for remaining operator mutation workflows."""

from __future__ import annotations

import compile_router
import pytest

from _application.artefact.reparent_children import (
    ArtefactReparentChildrenRequest,
    ReparentChildrenMode,
)
from _application.artefact.migrate_naming import ArtefactMigrateNamingRequest
from _application.artefact.repair_frontmatter import ArtefactRepairFrontmatterRequest
from _application.artefact.repair_ownership import ArtefactRepairOwnershipRequest
from _application.links.fix import LinksFixRequest
from _application.registry import current_application_catalogue, current_request_resolver
from _application.results import ErrorCode
from _application.types import Authority, EffectClass, RetryClass
from command_application import application_for


PROJECT = "Projects/Command Fixture.md"
DESIGN = "Designs/project~command-fixture/Command Fixture Design.md"


def test_reparent_children_uses_explicit_source_parent_mode(command_vault_clone):
    result = application_for(command_vault_clone.vault_root).invoke(
        ArtefactReparentChildrenRequest(
            PROJECT,
            ReparentChildrenMode.SOURCE_PARENT,
        )
    )

    assert result.status == "ok"
    assert result.result.source == PROJECT
    assert result.result.to is None
    assert result.result.children[0].key == "design/command-fixture-design"
    assert result.result.moves[0].old_path == DESIGN
    assert result.result.moves[0].new_path == "Designs/Command Fixture Design.md"
    assert (command_vault_clone.vault_root / result.result.moves[0].new_path).is_file()


def test_reparent_children_no_children_is_an_explicit_noop(command_vault_clone):
    result = application_for(command_vault_clone.vault_root).invoke(
        ArtefactReparentChildrenRequest(
            "Ideas/Command Fixture Candidate.md",
            ReparentChildrenMode.TOP_LEVEL,
        )
    )

    assert result.status == "ok"
    assert result.result.children == ()
    assert result.result.moves == ()
    assert result.committed_effects == ()


def test_reparent_children_transport_requires_mode_specific_parent():
    resolver = current_request_resolver()
    parent = resolver.resolve(
        "artefact.reparent-children",
        {"source": PROJECT, "mode": "parent", "parent": "project/other"},
    )
    top = resolver.resolve(
        "artefact.reparent-children",
        {"source": PROJECT, "mode": "top-level"},
    )

    assert parent.parent == "project/other"
    assert top.parent is None
    with pytest.raises(ValueError, match="requires a parent"):
        resolver.resolve(
            "artefact.reparent-children",
            {"source": PROJECT, "mode": "parent"},
        )
    with pytest.raises(ValueError, match="does not accept parent"):
        resolver.resolve(
            "artefact.reparent-children",
            {"source": PROJECT, "mode": "source-parent", "parent": "project/other"},
        )


def test_links_fix_preview_and_apply_are_structural(command_vault_clone):
    root = command_vault_clone.vault_root
    target = root / "Wiki/Brain Inbox.md"
    referrer = root / "Wiki/linker.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("---\ntype: living/wiki\ntags: []\n---\n\n# Brain Inbox\n")
    referrer.write_text(
        "---\ntype: living/wiki\ntags: []\n---\n\nSee [[brain-inbox]].\n"
    )
    router = compile_router.compile(str(root))
    compile_router.persist_compiled_router(str(root), router)

    preview = application_for(root).invoke(LinksFixRequest(path="Wiki/linker.md"))

    assert preview.status == "ok"
    assert preview.result.mode == "preview"
    assert preview.result.summary.resolvable == 1
    assert preview.result.resolvable[0].resolved_to == "Brain Inbox"
    assert preview.result.substitutions == 0
    assert "[[brain-inbox]]" in referrer.read_text()

    applied = application_for(root).invoke(
        LinksFixRequest(apply=True, path="Wiki/linker.md")
    )

    assert applied.status == "ok"
    assert applied.result.mode == "apply"
    assert applied.result.substitutions == 1
    assert applied.committed_effects[0].subject == "Wiki/linker.md"
    assert "[[Brain Inbox]]" in referrer.read_text()


def test_links_fix_context_dry_run_overrides_apply(command_vault_clone):
    result = application_for(command_vault_clone.vault_root, dry_run=True).invoke(
        LinksFixRequest(apply=True)
    )

    assert result.status == "ok"
    assert result.result.mode == "preview"
    assert result.result.substitutions == 0
    assert result.committed_effects == ()


def test_links_filter_is_only_valid_for_scoped_apply():
    with pytest.raises(ValueError, match="requires apply=true and a path"):
        LinksFixRequest(links=("broken-link",))


def test_frontmatter_repair_returns_bounded_steps_and_changes(command_vault_clone):
    path = command_vault_clone.vault_root / DESIGN
    path.write_text(
        "---\n"
        "type: living/design\n"
        "key: command-fixture-design\n"
        "tags:\n  - design\n"
        "status: ready\n"
        "parent: project/command-fixture\n"
        "---\n\n"
        "---\n"
        "tags:\n  - repaired\n"
        "---\n"
        "# Command Fixture Design\n"
    )

    result = application_for(command_vault_clone.vault_root).invoke(
        ArtefactRepairFrontmatterRequest()
    )

    assert result.status == "ok"
    assert result.result.scope == "frontmatter"
    assert result.result.status.value == "ok"
    assert result.result.steps[-1].status == "changed"
    assert result.committed_effects[0].kind == "artefact.repair-frontmatter"
    written = path.read_text()
    assert written.count("---\n") == 2
    assert "  - repaired" in written
    assert not hasattr(result.result, "vault_root")
    assert not hasattr(result.result, "managed_python")


def test_ownership_repair_projects_authoritative_parent(command_vault_clone):
    root = command_vault_clone.vault_root
    source = root / DESIGN
    drifted = root / "Designs/Command Fixture Design.md"
    source.rename(drifted)
    router = compile_router.compile(str(root))
    compile_router.persist_compiled_router(str(root), router)

    result = application_for(root).invoke(ArtefactRepairOwnershipRequest())

    assert result.status == "ok"
    assert result.result.scope == "ownership"
    assert result.result.status.value == "ok"
    assert not drifted.exists()
    assert source.is_file()


def test_naming_migration_previews_and_applies_canonical_filename(
    command_vault_clone,
):
    root = command_vault_clone.vault_root
    canonical = root / DESIGN
    legacy = canonical.with_name("command-fixture-design.md")
    canonical.rename(legacy)
    router = compile_router.compile(str(root))
    compile_router.persist_compiled_router(str(root), router)

    preview = application_for(root, dry_run=True).invoke(
        ArtefactMigrateNamingRequest()
    )

    assert preview.status == "ok"
    assert preview.result.dry_run is True
    assert any(
        item.path.old_path.endswith("command-fixture-design.md")
        for item in preview.result.details
    )
    assert legacy.is_file()

    applied = application_for(root).invoke(ArtefactMigrateNamingRequest())

    assert applied.status == "ok"
    assert applied.result.renamed >= 1
    assert canonical.is_file()
    assert not legacy.exists()
    assert applied.committed_effects[0].kind == "artefact.migrate-naming"


@pytest.mark.parametrize(
    ("command_id", "request_type"),
    (
        ("artefact.migrate-naming", ArtefactMigrateNamingRequest),
        ("artefact.repair-frontmatter", ArtefactRepairFrontmatterRequest),
        ("artefact.repair-ownership", ArtefactRepairOwnershipRequest),
    ),
)
def test_zero_input_operator_workflows_reject_hidden_options(command_id, request_type):
    resolver = current_request_resolver()

    assert type(resolver.resolve(command_id, {})) is request_type
    with pytest.raises(ValueError, match="unexpected fields"):
        resolver.resolve(command_id, {"scope": "all"})


@pytest.mark.parametrize(
    ("command_id", "payload", "request_type"),
    (
        (
            "artefact.reparent-children",
            {"source": PROJECT, "mode": "source-parent"},
            ArtefactReparentChildrenRequest,
        ),
        (
            "links.fix",
            {"apply": False, "path": DESIGN},
            LinksFixRequest,
        ),
        (
            "artefact.migrate-naming",
            {},
            ArtefactMigrateNamingRequest,
        ),
        (
            "artefact.repair-frontmatter",
            {},
            ArtefactRepairFrontmatterRequest,
        ),
        (
            "artefact.repair-ownership",
            {},
            ArtefactRepairOwnershipRequest,
        ),
    ),
)
def test_operator_workflow_catalogue_contract(command_id, payload, request_type):
    request = current_request_resolver().resolve(command_id, payload)
    entry = current_application_catalogue().resolve(request)

    assert type(request) is request_type
    assert entry.authority is Authority.OPERATOR
    assert entry.effect_class is EffectClass.SELECTED_BRAIN_MUTATION
    assert entry.retry_class is RetryClass.RECEIPT_REQUIRED


def test_reparent_children_dry_run_refuses_to_invent_moves(command_vault_clone):
    result = application_for(command_vault_clone.vault_root, dry_run=True).invoke(
        ArtefactReparentChildrenRequest(
            PROJECT,
            ReparentChildrenMode.SOURCE_PARENT,
        )
    )

    assert result.error.code is ErrorCode.INVALID_REQUEST
    assert (command_vault_clone.vault_root / DESIGN).is_file()
