"""Complete derived indexes after durable artefact changes, under the caller's lock."""

from _common import PartialApplyError


class IndexRefreshIncomplete(PartialApplyError):
    """Known content effects need a specific derived-state repair."""

    def __init__(self, command_id, *, router_result=None, committed_paths=(), operation_error=None):
        self.command_id = command_id
        self.router_result = router_result
        self.committed_paths = committed_paths
        self.operation_error = operation_error
        message = "Artefact changes committed, but derived index refresh failed; run " + command_id + "."
        if operation_error is not None:
            message = str(operation_error) + " " + message
        super().__init__(message)


def reconcile_artefact_indexes(root, *, lexical_before=None, changed_paths=()):
    """Refresh the router and lexical listing once after a content transaction."""
    from _portable.router_maintenance import maintain_router
    from _portable.lexical_maintenance import maintain_lexical_index, update_lexical_documents

    command = "runtime.refresh-router"
    try:
        router = maintain_router(root, dry_run=False, force=False)
        if router.status == "partial":
            raise IndexRefreshIncomplete(command, router_result=router)
        command = "retrieval.refresh-lexical"
        if lexical_before is not None:
            update_lexical_documents(root, lexical_before, changed_paths)
        else:
            # Moves preserve mtime; a rebuild covers their full affected extent.
            maintain_lexical_index(root, dry_run=False, force=True)
    except IndexRefreshIncomplete:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise IndexRefreshIncomplete(command) from exc
