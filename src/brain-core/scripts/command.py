#!/usr/bin/env python3
"""Direct noun/verb entry point for selected-Brain application commands."""

from __future__ import annotations

from pathlib import Path

if __name__ == "__main__":
    from _bootstrap.owner_attachment import OwnerAttachment, capture_process_identity
    from _bootstrap.consent_owner import OwnerConnectionError, OwnerTransportUnavailable
    import sys

    # Remove the inherited locator before contract imports or provider probes.
    try:
        attachment = OwnerAttachment.capture()
    except (OwnerConnectionError, OwnerTransportUnavailable) as exc:
        print(f"command.py: infrastructure — {exc}", file=sys.stderr)
        raise SystemExit(4) from exc
    try:
        identity, may_initialise = capture_process_identity()
        from _command_interface.script import run, initialise_job
        if sys.argv[1:2] == ["--initialise-job-owner"]:
            raise SystemExit(initialise_job(sys.argv[1:], owner_attachment=attachment,
                                            transport_identity=identity,
                                            owner_initialisation_allowed=may_initialise))
        raise SystemExit(run(script_path=Path(__file__), owner_attachment=attachment,
                             transport_identity=identity, owner_initialisation_allowed=may_initialise))
    finally:
        if attachment is not None:
            attachment.close()
