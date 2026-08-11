"""Owner behaviour for exact vault-file and archived-artefact reads."""

from __future__ import annotations

import pytest

from _application.artefact.list import (
    ArtefactListLocation,
    ArtefactListRequest,
)
from _application.artefact.read import (
    ArtefactLocation,
    ArtefactReadRequest,
)
from _application.registry import current_request_resolver
from _application.results import ErrorCode
from _application.vault.read_file import VaultReadFileRequest
from command_application import application_for


def test_vault_read_file_requires_an_exact_bounded_path(command_vault_baseline):
    application = application_for(command_vault_baseline.vault_root)

    exact = application.invoke(
        VaultReadFileRequest("_Assets/Attachments/command-fixture.txt")
    )
    basename = application.invoke(VaultReadFileRequest("command-fixture.txt"))
    inferred_extension = application.invoke(
        VaultReadFileRequest("Ideas/Command Fixture Candidate")
    )
    escaped = application.invoke(VaultReadFileRequest("../outside.md"))

    assert exact.status == "ok"
    assert exact.result.path == "_Assets/Attachments/command-fixture.txt"
    assert exact.result.content.strip()
    assert basename.error.code is ErrorCode.NOT_FOUND
    assert inferred_extension.error.code is ErrorCode.NOT_FOUND
    assert escaped.error.code is ErrorCode.INVALID_REQUEST
    assert escaped.effects == "none"


def test_archived_artefact_owners_read_and_list_top_level_and_legacy_paths(
    command_vault_clone,
):
    archives = {
        "_Archive/Ideas/20260101-old-idea.md": (
            "living/ideas",
            "adopted",
            "2026-01-01",
            "Old idea.",
        ),
        "Ideas/_Archive/20260202-legacy-idea.md": (
            "living/ideas",
            "adopted",
            "2026-02-02",
            "Legacy idea.",
        ),
    }
    for relative, (artefact_type, status, archived_date, body) in archives.items():
        path = command_vault_clone.vault_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\n"
            f"type: {artefact_type}\n"
            f"status: {status}\n"
            f"archiveddate: {archived_date}\n"
            "---\n\n"
            f"{body}\n",
            encoding="utf-8",
        )
    application = application_for(command_vault_clone.vault_root)

    listing = application.invoke(
        ArtefactListRequest(location=ArtefactListLocation.ARCHIVED)
    )
    reading = application.invoke(
        ArtefactReadRequest(
            "Ideas/_Archive/20260202-legacy-idea.md",
            ArtefactLocation.ARCHIVED,
        )
    )
    inferred_extension = application.invoke(
        ArtefactReadRequest(
            "Ideas/_Archive/20260202-legacy-idea",
            ArtefactLocation.ARCHIVED,
        )
    )

    assert [item.path for item in listing.result.items] == [
        "Ideas/_Archive/20260202-legacy-idea.md",
        "_Archive/Ideas/20260101-old-idea.md",
    ]
    assert listing.result.items[0].archived_date == "2026-02-02"
    assert "Legacy idea." in reading.result.content
    assert inferred_extension.error.code is ErrorCode.NOT_FOUND


def test_archive_boundary_is_explicit(command_vault_baseline):
    application = application_for(command_vault_baseline.vault_root)

    active_as_archived = application.invoke(
        ArtefactReadRequest(
            "Ideas/Command Fixture Candidate.md",
            ArtefactLocation.ARCHIVED,
        )
    )
    archived_as_file = application.invoke(
        VaultReadFileRequest("_Archive/Ideas/old.md")
    )

    assert active_as_archived.error.code is ErrorCode.INVALID_REQUEST
    assert archived_as_file.error.code is ErrorCode.INVALID_REQUEST


def test_vault_file_transport_contracts_are_strict():
    resolver = current_request_resolver()

    assert type(
        resolver.resolve(
            "vault.read-file",
            {"path": "_Assets/Attachments/command-fixture.txt"},
        )
    ) is VaultReadFileRequest
    assert type(
        resolver.resolve(
            "artefact.read",
            {"reference": "_Archive/Ideas/old.md", "location": "archived"},
        )
    ) is ArtefactReadRequest
    assert type(
        resolver.resolve("artefact.list", {"location": "archived"})
    ) is ArtefactListRequest

    with pytest.raises(ValueError, match="unexpected fields"):
        resolver.resolve("artefact.list", {"query": "old"})
