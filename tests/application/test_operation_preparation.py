"""Operation bindings retain exact requests and stop changed document effects."""

from dataclasses import dataclass, replace
from typing import ClassVar

import pytest

from _application._mutation_support import InlineContent
from _application.document._types import DocumentLocator, DocumentResource
from _application.document.write_body import (
    DocumentWriteBodyOperation, DocumentWriteBodyRequest,
    catalogue_entry, execute,
)
from _application.preparation import OperationBinding, ObservedResource, bind_operation
from _common import document_revision_at
from command_application import application_for


PATH = "Designs/project~command-fixture/Command Fixture Design.md"


@dataclass(frozen=True)
class SampleRequest:
    COMMAND_ID: ClassVar[str] = "sample.run"
    COMMAND_VERSION: ClassVar[int] = 1
    value: object


def test_binding_round_trip_preserves_exact_request_and_resource_identity():
    request = SampleRequest("large" * 1000)
    binding = bind_operation(request, observations=(
        ObservedResource("file", "Designs/A.md", "sha256:before"),
    ), frozen_inputs={"chosen_path": "Designs/A.md"})
    assert OperationBinding.from_wire(binding.to_wire()) == binding
    assert len(binding.request_json) < 300
    assert binding.digest != bind_operation(SampleRequest("other" * 1000)).digest
    assert binding.digest != replace(binding, observations=(
        ObservedResource("file", "Designs/B.md", "sha256:before"),
    )).digest


def test_bounded_argument_projection_does_not_erase_request_type():
    text = "body" * 1000
    text_binding = bind_operation(SampleRequest(text))
    from _application.preparation import content_digest
    object_binding = bind_operation(SampleRequest({
        "sha256": content_digest(text), "bytes": len(text.encode()),
    }))
    assert text_binding.digest != object_binding.digest


def test_binding_rejects_duplicate_resource_observations():
    item = ObservedResource("file", "same", None)
    with pytest.raises(ValueError, match="sorted unique"):
        bind_operation(SampleRequest("x"), observations=(item, item))


class RecordingAdmission:
    def __init__(self, binding=None):
        self.binding = binding
        self.requires_binding = binding is not None
        self.frozen_inputs = binding.frozen_inputs if binding else None
        self.calls = []
        self.pins = {}

    def read_pinned(self, source_key):
        return self.pins.get(source_key)

    def retain_content(self, source_key, content):
        from _application.preparation import content_digest

        self.pins[source_key] = content
        return {"pin": source_key, "sha256": content_digest(content), "bytes": len(content)}

    def admit(self, binding):
        if self.binding is not None and binding.digest != self.binding.digest:
            raise ValueError("prepared operation changed")
        self.calls.append(binding)


def _document_request(root, text="Prepared body"):
    return DocumentWriteBodyRequest(
        DocumentLocator(DocumentResource.ARTEFACT, PATH),
        document_revision_at(root / PATH),
        DocumentWriteBodyOperation.REPLACE, InlineContent(text),
    )


def test_prepare_document_has_no_content_effect_and_admits_exact_operation(command_vault_clone):
    root = command_vault_clone.vault_root
    context = application_for(root)._context
    request = _document_request(root)
    before = (root / PATH).read_bytes()
    binding = catalogue_entry().preparation.prepare(context, request)
    assert (root / PATH).read_bytes() == before
    admission = RecordingAdmission(binding)
    result = execute(replace(context, admission=admission), request)
    assert result.status == "ok"
    assert len(admission.calls) == 1
    assert "Prepared body" in (root / PATH).read_text()


def test_changed_body_is_rejected_before_document_effect(command_vault_clone):
    root = command_vault_clone.vault_root
    context = application_for(root)._context
    original = _document_request(root)
    binding = catalogue_entry().preparation.prepare(context, original)
    admission = RecordingAdmission(binding)
    before = (root / PATH).read_bytes()
    result = execute(replace(context, admission=admission),
                     replace(original, content=InlineContent("Different body")))
    assert result.status == "error"
    assert (root / PATH).read_bytes() == before
    assert admission.calls == []


def test_initial_authorisation_uses_same_admission_without_exact_binding(command_vault_clone):
    root = command_vault_clone.vault_root
    admission = RecordingAdmission()
    context = replace(application_for(root)._context, admission=admission)
    result = execute(context, _document_request(root))
    assert result.status == "ok"
    assert admission.calls == [None]


def test_creation_prep_freezes_date_and_identity_without_creating_folder(command_vault_clone):
    from datetime import datetime
    from _application.artefact.create import ArtefactCreateRequest, catalogue_entry, execute

    root = command_vault_clone.vault_root
    context = application_for(root)._context
    request = ArtefactCreateRequest("ideas", "Prepared Creation")
    before = {str(path.relative_to(root)) for path in root.rglob("*")}
    binding = catalogue_entry().preparation.prepare(context, request)
    after = {str(path.relative_to(root)) for path in root.rglob("*")}
    assert after - before <= {".brain/local/mutation.lock"}

    class LaterClock:
        def now(self):
            return datetime.fromisoformat("2030-12-31T23:59:59+11:00")

    admission = RecordingAdmission(binding)
    result = execute(replace(context, admission=admission, clock=LaterClock()), request)
    assert result.status == "ok"
    assert result.result.path == binding.review["path"]
    assert admission.calls[0].digest == binding.digest
    assert "2030-12-31" not in (root / result.result.path).read_text()


def test_prepared_staged_body_survives_original_handle_removal(command_vault_clone):
    from _application._mutation_support import StagedContent
    from _staging import stage_body, discard_staged_body

    root = command_vault_clone.vault_root
    admission = RecordingAdmission()
    context = replace(application_for(root)._context, admission=admission)
    handle = stage_body(root, "Pinned operation content")["handle"]
    request = replace(_document_request(root), content=StagedContent(handle))
    binding = catalogue_entry().preparation.prepare(context, request)
    discard_staged_body(root, handle)
    admission.binding = binding
    admission.requires_binding = True
    admission.frozen_inputs = binding.frozen_inputs
    result = execute(context, request)
    assert result.status == "ok"
    assert "Pinned operation content" in (root / PATH).read_text()
    assert admission.calls[0].digest == binding.digest


def test_outline_prep_binds_non_heading_source_changes(command_vault_clone):
    from _application.artefact.outline import ArtefactOutlineRequest, catalogue_entry, execute

    root = command_vault_clone.vault_root
    context = application_for(root)._context
    request = ArtefactOutlineRequest(PATH)
    binding = catalogue_entry().preparation.prepare(context, request)
    source = next(item for item in binding.observations if item.kind == "document")
    assert source.identity == PATH
    assert source.revision == document_revision_at(root / PATH)
    with (root / PATH).open("a") as handle:
        handle.write("\nA non-heading change.\n")
    admission = RecordingAdmission(binding)
    with pytest.raises(ValueError, match="prepared operation changed"):
        execute(replace(context, admission=admission), request)
    assert admission.calls == []


def test_definition_preparation_does_not_write_or_allow_changed_content(command_vault_clone):
    from _application.plugin.create import PluginCreateRequest, catalogue_entry, execute

    root = command_vault_clone.vault_root
    context = application_for(root)._context
    request = PluginCreateRequest("prepared-plugin", InlineContent("# Prepared\n"))
    binding = catalogue_entry().preparation.prepare(context, request)
    paths = binding.review["writes"]
    assert paths and all(not (root / path).exists() for path in paths)
    admission = RecordingAdmission(binding)
    result = execute(replace(context, admission=admission),
                     replace(request, content=InlineContent("# Changed\n")))
    assert result.status == "error"
    assert all(not (root / path).exists() for path in paths)
    assert admission.calls == []


def test_missing_prepared_pin_never_falls_back_to_original_stage(command_vault_clone):
    from _application._mutation_support import StagedContent
    from _staging import stage_body

    root = command_vault_clone.vault_root
    admission = RecordingAdmission()
    context = replace(application_for(root)._context, admission=admission)
    handle = stage_body(root, "Original still exists")["handle"]
    request = replace(_document_request(root), content=StagedContent(handle))
    binding = catalogue_entry().preparation.prepare(context, request)
    admission.binding = binding
    admission.requires_binding = True
    admission.frozen_inputs = binding.frozen_inputs
    admission.pins.clear()
    before = (root / PATH).read_bytes()
    result = execute(context, request)
    assert result.status == "error"
    assert "pin is unavailable" in result.error.message
    assert (root / PATH).read_bytes() == before
    assert admission.calls == []


def test_document_transform_is_planned_once_per_execution(command_vault_clone, monkeypatch):
    import edit

    root = command_vault_clone.vault_root
    admission = RecordingAdmission()
    context = replace(application_for(root)._context, admission=admission)
    original = edit._apply_body_operation
    calls = []

    def record(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(edit, "_apply_body_operation", record)
    assert execute(context, _document_request(root)).status == "ok"
    assert len(calls) == 1
