"""Shared document conversion warnings and named encoding refusals."""
from pathlib import Path

from .results import (CommandArgument, CommandError, CommandNextAction,
                      CommandWarning, Error, ErrorCode, RequestErrorDetails, WarningCode)


def conversion_warnings(path, code):
    if code is None:
        return ()
    return (CommandWarning(WarningCode.FOLLOW_UP_REQUIRED,
                           f"Converted '{path}' from {code} to UTF-8 without a byte-order mark."),)


def text_refusal(command_id, version, exc, vault_root):
    from _common._document_revision import UnreadableVaultTextFilesError
    def relative(path):
        path = Path(path)
        try:
            path = path.relative_to(Path(vault_root).resolve())
        except ValueError:
            pass
        return path.as_posix()
    if isinstance(exc, UnreadableVaultTextFilesError):
        paths = tuple(relative(path) for path, _, _ in exc.failures)
        message = "Cannot inspect vault text: " + "; ".join(
            f"{relative(path)}: {code}; {detail}" for path, code, detail in exc.failures)
    else:
        path = relative(exc.source_path)
        paths = (path,)
        message = f"Cannot read '{path}': {exc.code}; use {exc.remedy}."
    arguments = (CommandArgument("paths", paths),) if exc.remedy == "vault.repair-text" else ()
    return Error(command_id, version, CommandError(
        ErrorCode.CONFLICT, message, RequestErrorDetails("paths", message),
        CommandNextAction(exc.remedy, arguments)))


def read_conversion_warnings(context, content):
    if content.conversion_code is None:
        return ()
    relative = Path(content.source_path).relative_to(
        context.selected_brain.vault_root.resolve()).as_posix()
    return conversion_warnings(relative, content.conversion_code)


def is_nonstandard_text_error(exc):
    """Recognise named domain refusals without loading backend contracts at discovery."""
    from _common import NonStandardVaultTextError
    return isinstance(exc, NonStandardVaultTextError)


def raise_nonstandard_text_error(exc):
    """Keep a named text refusal from being flattened into an input error."""
    if is_nonstandard_text_error(exc):
        raise exc
