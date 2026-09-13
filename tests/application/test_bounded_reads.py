"""Portable text pages preserve exact normalized content and persisted revision."""

from dataclasses import replace
import json

import pytest

from _application.artefact.read import ArtefactReadRequest
from _application.vault.read_file import VaultReadFileRequest
from _application.resource.read import ReadableResource, ResourceReadRequest
from _application.results import ErrorCode
from _application.projection import canonical_result_envelope
from _application._response_budget import TextCursor
from _common import document_revision
from command_application import application_for


@pytest.mark.parametrize("kind", ["artefact", "file", "skill"])
def test_text_windows_round_trip_unicode_and_reject_intervening_edit(command_vault_clone, kind):
    root = command_vault_clone.vault_root
    if kind == "artefact":
        path = root / "Projects/Command Fixture.md"
        request = ArtefactReadRequest("Projects/Command Fixture.md")
    elif kind == "file":
        path = root / "_Config/User/preferences-always.md"
        request = VaultReadFileRequest("_Config/User/preferences-always.md")
    else:
        path = root / ".brain-core/skills/shaping/SKILL.md"
        request = ResourceReadRequest(ReadableResource.SKILL, "core:shaping")
    raw = ('---\r\nname: shaping\r\n---\r\n' + '文"\\🧠\r\n' * 7000).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    app = application_for(root)
    pages = []
    cursor = None
    first_cursor = None
    expected = raw.decode().replace("\r\n", "\n")
    while True:
        result = app.invoke(replace(request, cursor=cursor))
        assert result.status == "ok", result
        envelope = canonical_result_envelope(result)
        assert len(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode()) <= 16000
        assert result.result.revision == document_revision(raw)
        assert result.result.range.start == sum(map(len, pages))
        assert result.result.range.end - result.result.range.start == len(result.result.content)
        assert result.result.range.total_characters == len(expected)
        pages.append(result.result.content)
        cursor = result.result.range.next_cursor
        first_cursor = first_cursor or cursor
        if cursor is None:
            break
    assert len(pages) > 1
    assert "".join(pages) == expected
    invalid = app.invoke(replace(request, cursor=TextCursor(document_revision(raw), len(expected) + 1)))
    assert invalid.error.code is ErrorCode.INVALID_REQUEST
    path.write_bytes(raw + b"changed")
    conflict = app.invoke(replace(request, cursor=first_cursor))
    assert conflict.error.code is ErrorCode.CONFLICT
    assert conflict.effects == "none"


@pytest.mark.parametrize("value", [0, -1, 12001, True, "20"])
def test_read_window_rejects_invalid_character_limits(value):
    with pytest.raises(ValueError):
        ArtefactReadRequest("Example.md", max_characters=value)
