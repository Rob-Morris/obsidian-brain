"""Small command identity contract shared without importing command owners."""

from __future__ import annotations


def command_identity(request: object) -> tuple[str, int, type]:
    """Return identity owned by the concrete request type, never caller input."""

    request_type = type(request)
    try:
        command_id = request_type.COMMAND_ID
        command_version = request_type.COMMAND_VERSION
        result_type = request_type.RESULT_TYPE
    except AttributeError as exc:
        raise TypeError(
            f"command request type does not own identity: {request_type.__name__}"
        ) from exc
    if not isinstance(command_id, str) or not command_id:
        raise TypeError("request command identity must be a non-empty string")
    if not isinstance(command_version, int) or isinstance(command_version, bool):
        raise TypeError("request command version must be an integer")
    return command_id, command_version, result_type
