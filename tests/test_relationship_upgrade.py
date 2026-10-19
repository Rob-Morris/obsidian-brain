"""Isolated native upgrade preserves semantic data and reports contract conflicts."""

import upgrade
from test_upgrade import source_and_vault, _isolate_global_cli_targets
from _relationships.definitions import BUILTINS
from _relationships.model import validate_definition
from _relationships.storage import Store


def test_upgrade_preserves_local_definitions_and_history_and_exposes_collision(source_and_vault):
    source, root = source_and_vault
    store = Store(root)
    local = {"kind": "predicate", "id": "delivers", "name": "Delivers", "meaning": "Local delivery", "cardinality": "many"}
    local["contract_revision"] = validate_definition(local)
    conflicting = {**BUILTINS["native-transition"], "meaning": "Previous local meaning"}
    conflicting["contract_revision"] = validate_definition(conflicting)
    tombstone = {"kind": "event", "id": "retained", "retired": True, "revision": "previous"}
    store.write([local, conflicting, tombstone])
    paths = [store.path(item["kind"], item["id"]) for item in (local, conflicting, tombstone)]
    before = {path: path.read_bytes() for path in paths}
    result = upgrade.upgrade(str(root), str(source), sync=True)
    assert result["status"] == "ok", result
    assert {path: path.read_bytes() for path in paths} == before
    warnings = result["sync_result"]["warnings"]
    assert any(item["type"] == "relationships" and "conflict" in item["reason"] for item in warnings)
