"""Owner behaviour for plugin and trigger definition mutations."""

from __future__ import annotations

import define
import pytest

from _application._mutation_support import InlineContent, StagedContent
from _application.plugin.create import PluginCreateRequest
from _application.plugin.replace import PluginReplaceRequest
from _application.registry import current_application_catalogue, current_request_resolver
from _application.results import ErrorCode
from _application.trigger.create import TriggerCreateRequest
from _application.trigger.delete import TriggerDeleteRequest
from _application.trigger.replace import TriggerReplaceRequest
from _application.types import Authority, EffectClass, RetryClass
from _staging import read_staged_body, stage_body
from command_application import application_for


PLUGIN_NAME = "Command Plugin"
PLUGIN_PATH = "_Plugins/Command Plugin/SKILL.md"
TRIGGER_CONDITION = "When working on the command fixture"
TRIGGER_TARGET = "Projects/Command Fixture"
ROUTER_PATH = "_Config/router.md"


def test_plugin_create_returns_a_typed_operator_effect(command_vault_clone):
    result = application_for(command_vault_clone.vault_root).invoke(
        PluginCreateRequest(
            PLUGIN_NAME,
            InlineContent("# Command Plugin\n\nUse the typed command boundary.\n"),
        )
    )

    assert result.status == "ok"
    assert result.result.kind == "plugin"
    assert result.result.operation == "create"
    assert result.result.name == PLUGIN_NAME
    assert result.result.path == PLUGIN_PATH
    assert result.result.before_sha256 is None
    assert result.result.staged_handle_consumed is False
    assert result.committed_effects[0].kind == "plugin.create"
    assert result.committed_effects[0].subject == PLUGIN_PATH
    assert (command_vault_clone.vault_root / PLUGIN_PATH).is_file()


def test_plugin_replace_requires_the_current_hash_and_consumes_stage_after_commit(
    command_vault_clone,
):
    root = command_vault_clone.vault_root
    created = application_for(root).invoke(
        PluginCreateRequest(PLUGIN_NAME, InlineContent("# Command Plugin\n\nBefore.\n"))
    )
    stale_handle = stage_body(str(root), "# Command Plugin\n\nRejected.\n")["handle"]

    stale = application_for(root).invoke(
        PluginReplaceRequest(
            PLUGIN_NAME,
            StagedContent(stale_handle),
            "0" * 64,
        )
    )

    assert stale.error.code is ErrorCode.INVALID_REQUEST
    assert stale.effects == "none"
    assert read_staged_body(str(root), stale_handle).endswith("Rejected.\n")

    replacement_handle = stage_body(
        str(root), "# Command Plugin\n\nAfter.\n"
    )["handle"]
    replaced = application_for(root).invoke(
        PluginReplaceRequest(
            PLUGIN_NAME,
            StagedContent(replacement_handle),
            created.result.sha256,
        )
    )

    assert replaced.status == "ok"
    assert replaced.result.operation == "replace"
    assert replaced.result.before_sha256 == created.result.sha256
    assert replaced.result.sha256 != created.result.sha256
    assert replaced.result.staged_handle_consumed is True
    assert (root / PLUGIN_PATH).read_text().endswith("After.\n")
    with pytest.raises(ValueError, match="already-consumed"):
        read_staged_body(str(root), replacement_handle)


def test_trigger_create_replace_delete_preserve_exact_preconditions(
    command_vault_clone,
):
    root = command_vault_clone.vault_root
    created = application_for(root).invoke(
        TriggerCreateRequest(TRIGGER_CONDITION, TRIGGER_TARGET)
    )

    assert created.status == "ok"
    assert created.result.path == ROUTER_PATH
    assert created.result.condition == TRIGGER_CONDITION
    assert created.result.target == TRIGGER_TARGET
    assert created.committed_effects[0].kind == "trigger.create"
    router = (root / ROUTER_PATH).read_text()
    assert f"- {TRIGGER_CONDITION} → [[{TRIGGER_TARGET}]]" in router

    stale = application_for(root).invoke(
        TriggerReplaceRequest(
            TRIGGER_CONDITION,
            "Projects/Not The Current Target",
            new_condition="When reviewing the command fixture",
        )
    )
    assert stale.error.code is ErrorCode.INVALID_REQUEST
    assert stale.effects == "none"

    new_condition = "When reviewing the command fixture"
    replaced = application_for(root).invoke(
        TriggerReplaceRequest(
            TRIGGER_CONDITION,
            TRIGGER_TARGET,
            new_condition=new_condition,
        )
    )

    assert replaced.status == "ok"
    assert replaced.result.condition == new_condition
    assert replaced.result.target == TRIGGER_TARGET
    router = (root / ROUTER_PATH).read_text()
    assert TRIGGER_CONDITION not in router
    assert f"- {new_condition} → [[{TRIGGER_TARGET}]]" in router

    deleted = application_for(root).invoke(
        TriggerDeleteRequest(new_condition, TRIGGER_TARGET)
    )

    assert deleted.status == "ok"
    assert deleted.result.condition == new_condition
    assert deleted.result.target is None
    assert deleted.committed_effects[0].kind == "trigger.delete"
    assert new_condition not in (root / ROUTER_PATH).read_text()


@pytest.mark.parametrize(
    ("command_id", "payload", "request_type"),
    (
        (
            "plugin.create",
            {
                "name": PLUGIN_NAME,
                "content": {"source": "inline", "content": "# Plugin"},
            },
            PluginCreateRequest,
        ),
        (
            "plugin.replace",
            {
                "name": PLUGIN_NAME,
                "content": {"source": "stage", "handle": "stage-123"},
                "expected_sha256": "1" * 64,
            },
            PluginReplaceRequest,
        ),
        (
            "trigger.create",
            {"condition": TRIGGER_CONDITION, "target": TRIGGER_TARGET},
            TriggerCreateRequest,
        ),
        (
            "trigger.replace",
            {
                "condition": TRIGGER_CONDITION,
                "target": TRIGGER_TARGET,
                "new_target": "Projects/Command Fixture",
            },
            TriggerReplaceRequest,
        ),
        (
            "trigger.delete",
            {"condition": TRIGGER_CONDITION, "target": TRIGGER_TARGET},
            TriggerDeleteRequest,
        ),
    ),
)
def test_definition_mutation_transports_are_granular_maintainer_commands(
    command_id,
    payload,
    request_type,
):
    request = current_request_resolver().resolve(command_id, payload)
    entry = current_application_catalogue().resolve(request)

    assert type(request) is request_type
    assert entry.authority is Authority.MAINTAINER
    assert entry.effect_class is EffectClass.SELECTED_BRAIN_MUTATION
    assert entry.retry_class is RetryClass.RECEIPT_REQUIRED


def test_definition_mutation_transports_reject_cross_verb_fields():
    resolver = current_request_resolver()
    with pytest.raises(ValueError, match="unexpected fields"):
        resolver.resolve(
            "plugin.create",
            {
                "name": PLUGIN_NAME,
                "content": {"source": "inline", "content": "body"},
                "expected_sha256": "1" * 64,
            },
        )
    with pytest.raises(ValueError, match="unexpected fields"):
        resolver.resolve(
            "trigger.delete",
            {"condition": TRIGGER_CONDITION, "new_target": TRIGGER_TARGET},
        )


def test_definition_mutations_reject_context_dry_run(command_vault_clone):
    result = application_for(command_vault_clone.vault_root, dry_run=True).invoke(
        PluginCreateRequest(PLUGIN_NAME, InlineContent("# Dry Plugin\n"))
    )

    assert result.error.code is ErrorCode.INVALID_REQUEST
    assert result.effects == "none"
    assert not (command_vault_clone.vault_root / PLUGIN_PATH).exists()


def test_definition_post_commit_failure_is_honestly_unknown(
    command_vault_clone,
    monkeypatch,
):
    real_write = define.write_definition

    def commit_then_fail(*args, **kwargs):
        real_write(*args, **kwargs)
        raise OSError("response failed after definition commit")

    monkeypatch.setattr(define, "write_definition", commit_then_fail)
    result = application_for(command_vault_clone.vault_root).invoke(
        PluginCreateRequest(PLUGIN_NAME, InlineContent("# Uncertain Plugin\n"))
    )

    assert result.error.code is ErrorCode.COMMAND_OUTCOME_UNKNOWN
    assert result.effects == "unknown"
    assert (command_vault_clone.vault_root / PLUGIN_PATH).is_file()
