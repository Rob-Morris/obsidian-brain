"""Source readers leave delivery paging to the generic result boundary."""

import pytest

from _application.artefact.read import ArtefactReadRequest
from _application.vault.read_file import VaultReadFileRequest
from _application.resource.read import ReadableResource, ResourceReadRequest
from _common import document_revision
from command_application import application_for


@pytest.mark.parametrize("kind", ["artefact", "file", "skill"])
def test_source_readers_return_complete_unicode_and_persisted_revision(command_vault_clone, kind):
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
    expected = raw.decode().replace("\r\n", "\n")
    result = app.invoke(request)
    assert result.status == "ok", result
    assert result.result.revision == document_revision(raw)
    assert result.result.content == expected
    from _application.projection import canonical_result_envelope
    from _application.result_navigation import build_view
    envelope = canonical_result_envelope(result)
    assert type(envelope["result"]["content"]) is str
    view = build_view(envelope)
    assert "".join(page["result"]["content"] for page in view.pages) == expected
    assert not hasattr(result.result, "range")


@pytest.mark.parametrize("kind,payload", [
    ("artefact.read", {"reference": "Example.md", "cursor": None}),
    ("vault.read-file", {"path": "Example.md", "max_characters": 20}),
    ("resource.read", {"resource": "skill", "reference": "core:shaping", "cursor": None}),
    ("session.start", {"cursor": None}),
    ("vault.read-config", {"max_characters": 20}),
])
def test_source_readers_reject_legacy_window_arguments(kind, payload):
    from _application.registry import current_request_resolver
    with pytest.raises(ValueError, match="unexpected fields"):
        current_request_resolver().resolve(kind, payload)
