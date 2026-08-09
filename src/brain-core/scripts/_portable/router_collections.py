"""Portable exact views over memory and trigger router collections."""

from __future__ import annotations

from _common import load_compiled_router, read_file_content


def list_memories(router, query=None):
    memories = [dict(item) for item in router.get("memories", ())]
    if query is None:
        return memories
    folded = query.casefold()
    return [item for item in memories if folded in item.get("name", "").casefold()]


def read_memory_exact(router, vault_root, reference):
    match = next(
        (item for item in router.get("memories", ()) if item.get("name") == reference),
        None,
    )
    if match is None:
        return {"error": f"No memory matching '{reference}'"}
    return dict(match), read_file_content(vault_root, match["memory_doc"])


def list_triggers(router, query=None):
    triggers = [dict(item) for item in router.get("triggers", ())]
    if query is None:
        return triggers
    folded = query.casefold()
    return [
        item
        for item in triggers
        if any(
            folded in str(item.get(field) or "").casefold()
            for field in ("category", "condition", "detail", "target")
        )
    ]


def read_trigger_exact(router, condition):
    matches = [
        item
        for item in router.get("triggers", ())
        if item.get("condition") == condition
    ]
    if not matches:
        return {"error": f"No trigger with condition '{condition}'"}
    if len(matches) != 1:
        raise ValueError(
            f"Trigger condition must match exactly once; found {len(matches)}: {condition}"
        )
    return dict(matches[0])


def _load(vault_root):
    router = load_compiled_router(vault_root)
    if "error" in router:
        raise FileNotFoundError(router["error"])
    return router


def list_memories_from_vault(vault_root, query=None):
    return list_memories(_load(vault_root), query)


def read_memory_exact_from_vault(vault_root, reference):
    return read_memory_exact(_load(vault_root), vault_root, reference)


def list_triggers_from_vault(vault_root, query=None):
    return list_triggers(_load(vault_root), query)


def read_trigger_exact_from_vault(vault_root, condition):
    return read_trigger_exact(_load(vault_root), condition)
