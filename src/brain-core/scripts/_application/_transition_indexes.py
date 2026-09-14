"""Derived-state completion for artefact creation and transitions under the mutation lock."""

from dataclasses import replace
from _common import PartialApplyError, public_mutation_error_message
from .results import CommandError, CommandNextAction, CommandArgument, ErrorCode, RequestErrorDetails


class TransitionIndexesIncomplete(PartialApplyError):
    """Content committed, but a named derived-state repair remains necessary."""

    def __init__(self, error):
        self.error = error
        super().__init__(error.message)


def transition_error(exc):
    """Preserve a precise maintenance action alongside known content effects."""
    if isinstance(exc, TransitionIndexesIncomplete):
        return exc.error
    return CommandError(ErrorCode.CONFLICT, public_mutation_error_message(exc),
                        RequestErrorDetails(None, public_mutation_error_message(exc)))


def reconcile_transition_indexes(context, *, lexical_before=None, changed_paths=()):
    """Publish current router/listing state after content effects, without encoding."""
    from _portable.router_maintenance import maintain_router
    from _portable.lexical_maintenance import maintain_lexical_index, update_lexical_documents

    root = context.selected_brain.vault_root
    action = CommandNextAction("runtime.refresh-router")
    try:
        router = maintain_router(root, dry_run=False, force=False)
        if router.status == "partial":
            from ._router_maintenance import router_partial_error
            raise TransitionIndexesIncomplete(router_partial_error(router))
        action = CommandNextAction("retrieval.refresh-lexical", (CommandArgument("force", True),))
        if lexical_before is not None:
            update_lexical_documents(root, lexical_before, changed_paths)
        else:
            # Moves preserve mtime, so rebuild when the affected extent is not
            # confined to the in-place documents captured before admission.
            maintain_lexical_index(root, dry_run=False, force=True)
    except TransitionIndexesIncomplete:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise TransitionIndexesIncomplete(CommandError(
            ErrorCode.CONFLICT,
            "Artefact changes committed, but derived index refresh failed; run " + action.command_id + ".",
            RequestErrorDetails(None, "derived-index-refresh-failed"), action,
        )) from exc
    finally:
        if context.derived_snapshots is not None:
            context.derived_snapshots.invalidate()


def combine_transition_errors(original, maintenance):
    """Keep the repair action when both content application and completion fail."""
    error = transition_error(maintenance)
    return TransitionIndexesIncomplete(replace(
        error, message=public_mutation_error_message(original) + " " + error.message,
    ))
