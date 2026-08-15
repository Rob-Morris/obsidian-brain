"""Cross-resource behaviour for cohesive document mutation commands."""

from __future__ import annotations

import os

import compile_router
import pytest

from _application._mutation_support import FrontmatterField, InlineContent
from _application.document._types import DocumentLocator, DocumentResource
from _application.document.edit import (
    DocumentEditRequest,
    HeadingPart,
    HeadingSelection,
    ReplaceStructure,
)
from _application.document.patch import DocumentPatchRequest, UniqueMatch
from _application.document.update_frontmatter import DocumentUpdateFrontmatterRequest
from _application.document.write import DocumentWriteOperation, DocumentWriteRequest
from _application.resource.read import ReadableResource, ResourceReadRequest
from _common import document_revision_at, load_compiled_router, parse_frontmatter
from command_application import application_for


RESOURCE_CASES = {
    DocumentResource.MEMORY: ("brain-core-reference", "# Brain Core Reference", "Brain-core is"),
    DocumentResource.SKILL: ("vault-maintenance", "# Vault Maintenance", "Read the vault's router"),
    DocumentResource.STYLE: ("writing", "# Writing Style", "Use Australian English"),
    DocumentResource.TEMPLATE: ("designs", "## Open Decisions", "What needs to be decided"),
}


def _read(application, resource, reference):
    result = application.invoke(ResourceReadRequest(ReadableResource(resource.value), reference))
    assert result.status == "ok"
    return result.result


@pytest.mark.parametrize("resource", tuple(RESOURCE_CASES))
@pytest.mark.parametrize("operation", ("write", "patch", "edit"))
def test_all_named_documents_support_write_patch_and_structural_edit(
    command_vault_clone,
    resource,
    operation,
):
    name, heading, old_text = RESOURCE_CASES[resource]
    application = application_for(command_vault_clone.vault_root)
    document = DocumentLocator(resource, name)

    initial = _read(application, resource, name)
    if operation == "patch":
        result = application.invoke(
            DocumentPatchRequest(
                document,
                initial.revision,
                old_text,
                "Patched safely",
                UniqueMatch(),
            )
        )
    elif operation == "edit":
        heading_level = len(heading) - len(heading.lstrip("#"))
        heading_text = heading.lstrip("# ")
        result = application.invoke(
            DocumentEditRequest(
                document,
                initial.revision,
                ReplaceStructure(
                    HeadingSelection(heading_text, HeadingPart.BODY, level=heading_level),
                    InlineContent("Edited structurally.\n"),
                ),
            )
        )
    else:
        result = application.invoke(
            DocumentWriteRequest(
                document,
                initial.revision,
                DocumentWriteOperation.APPEND,
                InlineContent("\nWritten at body end.\n"),
            )
        )
    assert result.status == "ok"
    assert result.result.revision != initial.revision


def test_named_frontmatter_update_overwrites_lists_instead_of_inheriting_body_mode(
    command_vault_clone,
):
    application = application_for(command_vault_clone.vault_root)
    document = DocumentLocator(DocumentResource.MEMORY, "brain-core-reference")
    initial = _read(application, DocumentResource.MEMORY, "brain-core-reference")

    result = application.invoke(
        DocumentUpdateFrontmatterRequest(
            document,
            initial.revision,
            (FrontmatterField("triggers", ("typed-update",)),),
        )
    )

    assert result.status == "ok"
    fields, _body = parse_frontmatter(
        (command_vault_clone.vault_root / result.result.path).read_text()
    )
    assert fields["triggers"] == ["typed-update"]
    assert result.result.revision == document_revision_at(
        command_vault_clone.vault_root / result.result.path
    )


def test_crlf_named_resource_revision_can_be_used_for_an_immediate_mutation(
    command_vault_clone,
):
    vault_root = command_vault_clone.vault_root
    router = load_compiled_router(vault_root)
    metadata = next(
        item for item in router["memories"]
        if item["name"] == "brain-core-reference"
    )
    path = vault_root / metadata["memory_doc"]
    source_stat = path.stat()
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    os.utime(path, ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns))
    refreshed_router = compile_router.compile(str(vault_root))
    compile_router.persist_compiled_router(str(vault_root), refreshed_router)
    application = application_for(vault_root)

    initial = _read(
        application,
        DocumentResource.MEMORY,
        "brain-core-reference",
    )

    assert "\r" not in initial.content
    assert initial.revision == document_revision_at(path)
    result = application.invoke(
        DocumentPatchRequest(
            DocumentLocator(DocumentResource.MEMORY, "brain-core-reference"),
            initial.revision,
            "Brain-core is",
            "Brain Core is",
            UniqueMatch(),
        )
    )
    assert result.status == "ok", result.error.message
    assert result.result.revision == document_revision_at(path)
