"""Owner behaviour for explicit artefact lifecycle mutations."""

from __future__ import annotations

import edit
import pytest

from _application.artefact.reparent import ArtefactReparentRequest
from _application.artefact.set_key import ArtefactSetKeyRequest
from _application.artefact.set_naming_field import ArtefactSetNamingFieldRequest
from _application.artefact.set_status import ArtefactSetStatusRequest
from _application.registry import current_application_catalogue, current_request_resolver
from _application.results import ErrorCode
from _application.types import Authority, EffectClass, RetryClass
from command_application import application_for


CANDIDATE = "Ideas/Command Fixture Candidate.md"
OWNED_DESIGN = "Designs/project~command-fixture/Command Fixture Design.md"


def test_artefact_reparent_places_under_owner_and_reports_typed_change(
    command_vault_clone,
):
    result = application_for(command_vault_clone.vault_root).invoke(
        ArtefactReparentRequest(CANDIDATE, "project/command-fixture")
    )

    assert result.status == "ok", result.error.message
    assert result.result.field == "parent"
    assert result.result.old_value is None
    assert result.result.new_value == "project/command-fixture"
    assert result.result.path != CANDIDATE
    assert (command_vault_clone.vault_root / result.result.path).is_file()
    assert result.committed_effects[0].kind == "artefact.reparent"
    assert result.committed_effects[0].subject == result.result.path


def test_artefact_reparent_requires_explicit_nullable_parent(command_vault_clone):
    resolver = current_request_resolver()

    with pytest.raises(ValueError, match="parent is required"):
        resolver.resolve("artefact.reparent", {"path": OWNED_DESIGN})

    request = resolver.resolve(
        "artefact.reparent", {"path": OWNED_DESIGN, "parent": None}
    )
    result = application_for(command_vault_clone.vault_root).invoke(request)

    assert type(request) is ArtefactReparentRequest
    assert result.status == "ok"
    assert result.result.old_value == "project/command-fixture"
    assert result.result.new_value is None
    assert result.result.path == "Designs/Command Fixture Design.md"


def test_artefact_set_status_uses_lifecycle_move_semantics(command_vault_clone):
    result = application_for(command_vault_clone.vault_root).invoke(
        ArtefactSetStatusRequest(CANDIDATE, "adopted")
    )

    assert result.status == "ok"
    assert result.result.field == "status"
    assert result.result.old_value == "ready"
    assert result.result.new_value == "adopted"
    assert result.result.path.startswith("Ideas/+Adopted/")
    assert (command_vault_clone.vault_root / result.result.path).is_file()


def test_artefact_set_key_preserves_backend_key_invariants(command_vault_clone):
    result = application_for(command_vault_clone.vault_root).invoke(
        ArtefactSetKeyRequest(CANDIDATE, "command-fixture-renamed")
    )

    assert result.status == "ok"
    assert result.result.field == "key"
    assert result.result.old_value == "command-fixture-candidate"
    assert result.result.new_value == "command-fixture-renamed"
    written = (command_vault_clone.vault_root / result.result.path).read_text()
    assert "key: command-fixture-renamed" in written


def test_artefact_set_naming_field_renames_through_naming_engine(
    command_vault_clone,
    monkeypatch,
):
    from _lifecycle import derived_cache_state
    import compile_router

    vault_root = command_vault_clone.vault_root
    source = vault_root / "Wiki/old-Brain Overview.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        "---\ntype: living/wiki\ntags: []\ncode: old\n---\n\n# Brain Overview\n"
    )
    router = compile_router.compile(str(vault_root))
    wiki = next(item for item in router["artefacts"] if item["folder"] == "Wiki")
    wiki["configured"] = True
    wiki["frontmatter_type"] = "living/wiki"
    wiki["naming"] = {
        "pattern": "{Code}-{Title}.md",
        "folder": "Wiki/",
        "rules": [
            {
                "match_field": None,
                "match_values": None,
                "pattern": "{Code}-{Title}.md",
                "date_source": None,
            }
        ],
        "placeholders": [
            {
                "name": "Code",
                "field": "code",
                "required_when_field": None,
                "required_values": None,
                "regex": None,
            }
        ],
    }
    monkeypatch.setattr(
        derived_cache_state, "load_fresh_compiled_router", lambda _root: router
    )

    result = application_for(vault_root).invoke(
        ArtefactSetNamingFieldRequest(
            source.relative_to(vault_root).as_posix(), "code", "new"
        )
    )

    assert result.status == "ok", result.error.message
    assert result.result.field == "code"
    assert result.result.path == "Wiki/new-Brain Overview.md"
    assert (vault_root / result.result.path).is_file()


@pytest.mark.parametrize(
    ("command_id", "payload", "request_type"),
    (
        (
            "artefact.reparent",
            {"path": CANDIDATE, "parent": "project/command-fixture"},
            ArtefactReparentRequest,
        ),
        (
            "artefact.set-status",
            {"path": CANDIDATE, "status": "shaping"},
            ArtefactSetStatusRequest,
        ),
        (
            "artefact.set-key",
            {"path": CANDIDATE, "key": "changed"},
            ArtefactSetKeyRequest,
        ),
        (
            "artefact.set-naming-field",
            {"path": CANDIDATE, "field": "code", "value": "new"},
            ArtefactSetNamingFieldRequest,
        ),
    ),
)
def test_lifecycle_transport_resolves_one_concrete_request(
    command_id,
    payload,
    request_type,
):
    request = current_request_resolver().resolve(command_id, payload)
    entry = current_application_catalogue().resolve(request)

    assert type(request) is request_type
    assert entry.command_id == command_id
    assert entry.authority is Authority.CONTRIBUTOR
    assert entry.effect_class is EffectClass.SELECTED_BRAIN_MUTATION
    assert entry.retry_class is RetryClass.RECEIPT_REQUIRED


def test_lifecycle_mutation_dry_run_refuses_to_mutate(command_vault_clone):
    before = (command_vault_clone.vault_root / CANDIDATE).read_bytes()

    result = application_for(command_vault_clone.vault_root, dry_run=True).invoke(
        ArtefactSetStatusRequest(CANDIDATE, "adopted")
    )

    assert result.error.code is ErrorCode.INVALID_REQUEST
    assert (command_vault_clone.vault_root / CANDIDATE).read_bytes() == before


def test_lifecycle_post_commit_failure_is_honestly_unknown(
    command_vault_clone,
    monkeypatch,
):
    real_update = edit.apply_artefact_transition

    def commit_then_fail(*args, **kwargs):
        real_update(*args, **kwargs)
        raise OSError("response failed after lifecycle commit")

    monkeypatch.setattr(edit, "apply_artefact_transition", commit_then_fail)
    result = application_for(command_vault_clone.vault_root).invoke(
        ArtefactSetKeyRequest(CANDIDATE, "uncertain-key")
    )

    assert result.error.code is ErrorCode.COMMAND_OUTCOME_UNKNOWN
    assert result.effects == "unknown"
    assert "key: uncertain-key" in (
        command_vault_clone.vault_root / CANDIDATE
    ).read_text()
