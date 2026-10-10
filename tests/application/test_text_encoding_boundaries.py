"""Named decode refusals through real router and maintenance command owners."""

from dataclasses import replace
from datetime import datetime, timezone

import pytest

import compile_router
from _application.application import CommandApplication
from _application.maintenance.run import MaintenanceRunRequest
from _application.registry import current_request_resolver
from _application.results import ErrorCode, WarningCode
from _application.runtime.refresh_router import RuntimeRefreshRouterRequest
from _bootstrap.maintenance_summary import GroupOutcome
from _lifecycle.router_errors import UnreadableRouterSourceError
from command_application import application_for, context_for


ROUTER = ".brain/local/compiled-router.json"


@pytest.mark.parametrize("relative", [
    "_Config/Taxonomy/Living/notes.md",
    "_Config/router.md",
    "_Config/Memories/damaged.md",
])
@pytest.mark.parametrize("raw,code,remedy", [
    (b"broken\xff", "not_utf8", "vault.check"),
    ("# text\n".encode("utf-16"), "utf16_bom", "vault.repair-text"),
    ("# text\n".encode("utf-32"), "utf32_bom", "vault.repair-text"),
    (b"caf\xc3\xa9\xc3", "truncated_utf8", "vault.repair-text"),
    (b"\x00\xff", "not_text", "vault.check"),
])
def test_refresh_router_names_a_definite_no_effect_source_failure(
    command_vault_clone, relative, raw, code, remedy,
):
    root = command_vault_clone.vault_root
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    before = (root / ROUTER).read_bytes()

    result = application_for(root).invoke(RuntimeRefreshRouterRequest(force=True))

    assert result.status == "error"
    assert result.error.code is ErrorCode.CONFLICT
    assert result.error.details.reason == code
    assert relative in result.error.message
    assert result.error.next_action.command_id == remedy
    assert result.effects == "none"
    assert (root / ROUTER).read_bytes() == before
    assert path.read_bytes() == raw


def test_memory_body_decode_failure_is_not_hidden_by_reading_only_frontmatter(command_vault_clone):
    root = command_vault_clone.vault_root
    path = root / "_Config/Memories/damaged.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"---\ntriggers: [brain]\n---\nbody\xff")

    with pytest.raises(UnreadableRouterSourceError) as caught:
        compile_router.compile(root)

    assert str(path) in str(caught.value)
    assert caught.value.diagnosis.code == "not_utf8"
    assert isinstance(caught.value.__cause__, UnicodeDecodeError)


@pytest.mark.parametrize("mark", ["", "\ufeff"])
@pytest.mark.parametrize("opening,closing", [
    ("---", "---"), (" \t--- \t", " \t---\t"),
])
def test_memory_trigger_delimiters_keep_the_file_readers_whitespace_semantics(
    tmp_path, mark, opening, closing,
):
    from _common import read_frontmatter

    path = tmp_path / "memory.md"
    path.write_text(f"{mark}{opening}\ntriggers: [brain]\n{closing}\nbody\n", encoding="utf-8")

    assert read_frontmatter(path) == {"triggers": ["brain"]}
    assert compile_router._parse_memory_triggers(path) == ["brain"]


class _Clock:
    def now(self):
        return datetime.now(timezone.utc)


class _Sibling:
    def __init__(self, root):
        self.root = root

    def repair(self, family, *, invocation_id):
        request = current_request_resolver().resolve(family.command_id, dict(family.request))
        return application_for(
            self.root, invocation_id=invocation_id, context_kind="standalone",
        ).invoke(request)


def test_maintenance_pass_reports_the_named_router_failure(command_vault_clone):
    root = command_vault_clone.vault_root
    relative = "_Config/Taxonomy/Living/notes.md"
    (root / relative).write_bytes(b"taxonomy\xff")
    context = context_for(root, context_kind="standalone")
    context = replace(context, maintenance=_Sibling(root), clock=_Clock())
    context = replace(context, access=context.authorisation.bind(context))

    result = CommandApplication(context, context.authorisation.catalogue).invoke(MaintenanceRunRequest())

    assert result.status == "partial"
    assert result.error.code is ErrorCode.CONFLICT
    assert any(
        warning.code is WarningCode.FOLLOW_UP_REQUIRED
        and relative in warning.message and "not_utf8" in warning.message
        for warning in result.warnings
    )
    assert result.error.code is not ErrorCode.COMMAND_OUTCOME_UNKNOWN
    from _application.maintenance.list import MaintenanceListRequest

    listed = application_for(root, context_kind="standalone").invoke(MaintenanceListRequest(all=True))
    assert listed.status == "ok"
    assert next(item for item in listed.result.items if item.scope == "router").last_outcome is GroupOutcome.FAILED
