#!/usr/bin/env python3
"""Internal selected-Brain helper for CLI-only external access approval."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys

from _application.registry import current_application_catalogue
from _command_interface.access import (
    approve_external_request,
    resolve_external_approval,
)
from _command_interface.context import SystemClock
from _common import is_brain_vault
import config as brain_config


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--vault", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _profile_commands(config: dict, profile: str) -> frozenset[str]:
    definition = config.get("vault", {}).get("profiles", {}).get(profile)
    allowed = definition.get("allow") if isinstance(definition, dict) else None
    if not isinstance(allowed, list) or any(not isinstance(item, str) for item in allowed):
        raise ValueError(f"approver profile '{profile}' has an invalid allow-list")
    return frozenset(allowed)


def run(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        root = Path(args.vault).expanduser()
        if root.is_symlink():
            raise ValueError("selected Brain vault cannot be a symlink")
        root = root.resolve()
        if not is_brain_vault(root):
            raise ValueError("selected path is not an installed Brain")
        key = os.environ.get("BRAIN_ACCESS_APPROVER_KEY")
        if not key:
            raise PermissionError("external approval requires an operator key")
        catalogue = current_application_catalogue()
        config = brain_config.load_config(
            str(root),
            additional_valid_tools=frozenset(
                entry.command_id for entry in catalogue.entries
            ),
        )
        profile, operator_id = brain_config.authenticate_operator(key, config)
        if operator_id is None:
            raise PermissionError("external approval requires a registered operator")
        commands = _profile_commands(config, profile)
        approver_identity = f"operator:{operator_id}"
        clock = SystemClock()
        target = resolve_external_approval(
            vault_root=root,
            config=config,
            request_id=args.request_id,
            approver_identity=approver_identity,
            approver_commands=commands,
            clock=clock,
        )
        if args.dry_run:
            payload = {
                "status": "planned",
                "principal": target.principal,
                "request_id": target.request_id,
                "commands": list(target.commands),
                "lease": None,
            }
        else:
            lease = approve_external_request(
                vault_root=root,
                config=config,
                target=target,
                approver_profile=profile,
                approver_identity=approver_identity,
                approver_commands=commands,
                clock=clock,
            )
            lease_value = asdict(lease)
            lease_value["policy"] = lease.policy.value
            payload = {
                "status": "approved",
                "principal": target.principal,
                "request_id": target.request_id,
                "commands": list(target.commands),
                "lease": lease_value,
            }
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        return 0
    except (OSError, PermissionError, ValueError) as exc:
        print(f"access approval denied: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(run())
