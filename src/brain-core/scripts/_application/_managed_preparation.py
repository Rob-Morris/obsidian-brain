"""Exact source and output scope for managed retrieval and cache maintenance."""

from pathlib import Path

from .preparation import ObservedResource, OperationPreparation, bind_operation, canonical_json, content_digest


SIDECARS = (".brain/local/type-embeddings.npy", ".brain/local/doc-embeddings.npy",
            ".brain/local/embeddings-meta.json")
CACHE_OUTPUTS = (".brain/local/compiled-router.json", ".brain/local/retrieval-index.json", *SIDECARS)


def maintenance_binding(context, request, *, frozen_inputs=None):
    from _portable.maintenance_inputs import file_identity, source_manifest

    root = context.selected_brain.vault_root
    manifest = source_manifest(root)
    command = request.COMMAND_ID
    outputs = CACHE_OUTPUTS
    if command == "retrieval.refresh-lexical":
        outputs = (".brain/local/retrieval-index.json", *SIDECARS)
    elif command == "runtime.refresh-router":
        outputs = (".brain/local/compiled-router.json", *SIDECARS)
    elif command == "runtime.warmup":
        outputs = (*CACHE_OUTPUTS, ".brain/local/runtime-status.json")
    observations = [ObservedResource("maintenance-sources", "selected-brain", manifest["sha256"])]
    observations.extend(ObservedResource("maintenance-output", path, file_identity(root / path))
                        for path in outputs)
    # Router refresh also rewrites the canonical rendered session documents.
    if command in {"runtime.refresh-router", "runtime.warmup", "retrieval.enable",
                   "retrieval.repair-semantic", "retrieval.rebuild-semantic"}:
        from session import SESSION_MARKDOWN_REL
        observations.append(ObservedResource("session-output", SESSION_MARKDOWN_REL,
                                              file_identity(root / SESSION_MARKDOWN_REL)))
    frozen = dict(frozen_inputs or {})
    frozen["maintenance_sources"] = manifest
    return bind_operation(request, observations=observations, frozen_inputs=frozen,
                          review={"operation": command, "scope": "Selected Brain corpus and derived retrieval/runtime state",
                                  "source_count": manifest["file_count"], "sources": manifest["sha256"],
                                  "outputs": list(outputs), "force": getattr(request, "force", False),
                                  "provision_runtime_and_model": command in {"retrieval.enable", "retrieval.repair-semantic"}})


def prepare_maintenance(context, request, *, frozen_inputs=None):
    from _common import vault_mutation_lock
    with vault_mutation_lock(context.selected_brain.vault_root):
        return maintenance_binding(context, request, frozen_inputs=frozen_inputs)


MAINTENANCE = OperationPreparation(prepare_maintenance)
