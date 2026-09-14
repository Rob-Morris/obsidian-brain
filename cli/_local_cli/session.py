"""Root-job supervisor for explicit instance-scoped CLI consent."""

from __future__ import annotations

import os
import signal
import subprocess
from threading import current_thread, main_thread

from _bootstrap.consent_owner import ConsentOwner, OwnerTransportUnavailable
from _bootstrap.owner_attachment import OwnerAttachment, without_owner_environment


def run_owned_job(selected, argv, *, initialise_owner) -> int:
    """Pin trusted identity before a root program runs and end consent when it ends.

    The composition callback initialises application-owned principal/Brain state;
    this transport helper neither authenticates nor makes consent decisions.
    """
    if not argv or not argv[0] or any(not isinstance(value, str) for value in argv):
        raise ValueError("an owned job requires a program and string arguments")
    if os.name != "posix":
        raise OwnerTransportUnavailable("CLI job consent requires supported POSIX descriptor inheritance")
    owner = ConsentOwner(selected.vault_root)
    attachment = None
    child = None
    previous = {}

    def interrupted(signum, _frame):
        # End admission before waiting for the root's response to interruption.
        owner.close()
        if child is not None and child.poll() is None:
            child.send_signal(signum)
        raise SystemExit(128 + signum)

    try:
        if current_thread() is main_thread():
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous[signum] = signal.signal(signum, interrupted)
        initialise_owner(owner)
        attachment = OwnerAttachment.for_job(owner)
        env = without_owner_environment()
        env.pop("BRAIN_OPERATOR_KEY", None)
        env["BRAIN_VAULT_ROOT"] = str(selected.vault_root.resolve())
        env.pop("BRAIN_WORKSPACE_DIR", None)
        if selected.workspace is not None:
            env["BRAIN_WORKSPACE_DIR"] = str(selected.workspace)
        child = subprocess.Popen(list(argv), **attachment.forwarded_process(env))
        code = child.wait()
        return code if code >= 0 else 128 - code
    finally:
        owner.close()
        if attachment is not None:
            attachment.close()
        for signum, handler in previous.items():
            signal.signal(signum, handler)
