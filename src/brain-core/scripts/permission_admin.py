#!/usr/bin/env python3
"""CLI-only transport to the selected Brain's permission administration service."""

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys


def run(argv=None):
    """Consume an explicit private key and one JSON request; emit a bounded result."""
    key = os.environ.pop('BRAIN_PERMISSION_ADMIN_KEY', None)
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--vault', required=True)
    parser.add_argument('--invocation-id', required=True)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    from _application.registry import current_application_catalogue
    from _command_interface.context import SystemClock
    from _command_interface.permission_admin import PermissionOutcomeUnknown, set_operator_profile
    try:
        request = json.load(sys.stdin)
        if not isinstance(request, dict) or set(request) != {'operator_id', 'profile', 'expected_revision'}:
            raise ValueError('permission request has an invalid shape')
        result = set_operator_profile(vault_root=Path(args.vault), catalogue=current_application_catalogue(),
            operator_key=key, invocation_id=args.invocation_id, dry_run=args.dry_run, clock=SystemClock(), **request)
        print(json.dumps({'status': 'ok', 'result': asdict(result)}, separators=(',', ':')))
        return 0
    except PermissionOutcomeUnknown as exc:
        print(json.dumps({'status': 'unknown', 'invocation_id': exc.invocation_id, 'message': str(exc)}))
        return 3
    except (PermissionError, ValueError) as exc:
        print(json.dumps({'status': 'error', 'code': 'authority_denied' if isinstance(exc, PermissionError) else 'invalid_request',
                          'message': str(exc)}))
        return 2


if __name__ == '__main__':
    raise SystemExit(run())
