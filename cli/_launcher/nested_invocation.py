"""Invoke one launcher command from inside another with its own identity."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from launcher_catalogue import LAUNCHER_CATALOGUE

from .context import LauncherContext
from .invocation import LauncherInvocation


def invoke_sibling(context: LauncherContext, request: object, *, invocation_id: str):
    """Run ``request`` as a sibling of the current invocation.

    The sibling keeps its own invocation identity, receipt and approval
    transition; everything else in the context is inherited, dry run included.
    """
    from .owners import LAUNCHER_OWNERS

    if not invocation_id.strip() or invocation_id == context.invocation_id:
        raise ValueError("a sibling launcher invocation requires its own invocation identity")
    sibling = replace(context, invocation_id=invocation_id)
    return LauncherInvocation(sibling, LAUNCHER_CATALOGUE, LAUNCHER_OWNERS).invoke(request)


def invoke_nested(context: LauncherContext, request: object, *, vault_root: Path, invocation_id: str):
    """Run ``request`` as a sibling invocation targeting ``vault_root``.

    ``workspace_dir`` and ``caller_dir`` are rebound to the target vault
    because workspace-breadth repairs target ``context.workspace_dir or
    context.caller_dir``; without that, repairing Brain Y would rewrite the
    caller directory's registration.
    """
    root = Path(vault_root)
    if not root.is_absolute():
        raise ValueError("nested launcher invocation requires an absolute vault root")
    nested = replace(context, current_vault=root, workspace_dir=root, caller_dir=root)
    return invoke_sibling(nested, request, invocation_id=invocation_id)
