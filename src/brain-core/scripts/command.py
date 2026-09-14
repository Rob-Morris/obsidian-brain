#!/usr/bin/env python3
"""Direct noun/verb entry point for selected-Brain application commands."""

from __future__ import annotations

from pathlib import Path

if __name__ == "__main__":
    from _bootstrap.owner_attachment import OwnerAttachment
    from _bootstrap.consent_owner import OwnerConnectionError, OwnerTransportUnavailable
    import sys

    # Remove the inherited locator before contract imports or provider probes.
    try:
        attachment = OwnerAttachment.capture()
    except (OwnerConnectionError, OwnerTransportUnavailable) as exc:
        print(f"command.py: infrastructure — {exc}", file=sys.stderr)
        raise SystemExit(4) from exc
    try:
        from _command_interface.script import run
        raise SystemExit(run(script_path=Path(__file__), owner_attachment=attachment))
    finally:
        if attachment is not None:
            attachment.close()
