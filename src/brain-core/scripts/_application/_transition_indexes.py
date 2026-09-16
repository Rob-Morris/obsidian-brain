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
    from _portable.transition_indexes import reconcile_artefact_indexes, IndexRefreshIncomplete
    try:
        reconcile_artefact_indexes(context.selected_brain.vault_root,
                                  lexical_before=lexical_before, changed_paths=changed_paths)
    except IndexRefreshIncomplete as exc:
        raise TransitionIndexesIncomplete(index_refresh_error(exc)) from exc
    finally:
        if context.derived_snapshots is not None:
            context.derived_snapshots.invalidate()


def index_refresh_error(exc):
    """Project portable completion failures into the command repair contract."""
    if exc.router_result is not None:
        from ._router_maintenance import router_partial_error
        error = router_partial_error(exc.router_result)
        if exc.operation_error is not None:
            error = replace(error, message=public_mutation_error_message(exc.operation_error) + " " + error.message)
        return error
    arguments = (CommandArgument("force", True),) if exc.command_id == "retrieval.refresh-lexical" else ()
    return CommandError(ErrorCode.CONFLICT, str(exc),
                        RequestErrorDetails(None, "derived-index-refresh-failed"),
                        CommandNextAction(exc.command_id, arguments))


def combine_transition_errors(original, maintenance):
    """Keep the repair action when both content application and completion fail."""
    error = transition_error(maintenance)
    return TransitionIndexesIncomplete(replace(
        error, message=public_mutation_error_message(original) + " " + error.message,
    ))
