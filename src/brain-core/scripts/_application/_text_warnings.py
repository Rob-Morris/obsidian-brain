"""Shared document conversion warnings and named encoding refusals."""
from pathlib import Path

from .results import (CommandArgument, CommandError, CommandNextAction,
                      CommandWarning, Error, ErrorCode, InstructionNextAction,
                      RequestErrorDetails, WarningCode)


def conversion_warnings(path, code):
    if code is None:
        return ()
    return (CommandWarning(WarningCode.FOLLOW_UP_REQUIRED,
                           f"Converted '{path}' from {code} to UTF-8 without a byte-order mark."),)


def text_refusal(command_id, version, exc, vault_root):
    from _common._document_revision import UnreadableVaultTextFilesError
    from _lifecycle.text_files import iter_vault_text_files
    from _skill_library.tracking import TrackingError
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
            f"{relative(path)}: {code}" +
            (f"; {detail}" if code in {"os_error", "inspection_error"} else "")
            for path, code, detail in exc.failures)
    else:
        path = relative(exc.source_path)
        paths = (path,)
        message = f"Cannot read '{path}': {exc.code}."
    try:
        inventory = set(iter_vault_text_files(vault_root))
    except (OSError, TrackingError, UnicodeDecodeError) as inventory_error:
        message += f" Cannot determine text-check coverage: {inventory_error}."
        next_action = InstructionNextAction(
            "Restore access to the vault text inventory, then retry the original command.")
    else:
        outside = sorted(set(paths) - inventory)
        if outside:
            message += " Outside the vault text scanned set: " + ", ".join(outside) + "."
            next_action = InstructionNextAction(
                "Restore the named source through its owning tool, or convert user-owned "
                "text to UTF-8 without a byte-order mark in an editor.")
        else:
            arguments = (CommandArgument("paths", paths),) if exc.remedy == "vault.repair-text" else ()
            next_action = CommandNextAction(exc.remedy, arguments)
    return Error(command_id, version, CommandError(
        ErrorCode.CONFLICT, message, RequestErrorDetails("paths", message),
        next_action))


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
