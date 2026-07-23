#!/usr/bin/env python3
"""Compatibility adapter for pre-v0.53 ``start_shaping`` consumers."""

import argparse
import json
import sys

from _common import (
    find_vault_root,
    MutationLockError,
    PartialApplyError,
    public_mutation_error_message,
    vault_mutation_lock,
)
from _lifecycle.derived_cache_state import load_fresh_compiled_router
from start_shaping_session import (
    _prepare_shaping_target,
    SHAPING_MODES,
    start_shaping_session,
)


def _legacy_target_and_default_mode(vault_root, router, target):
    prepared = _prepare_shaping_target(vault_root, router, target)
    shaping = prepared.artefact["shaping"]
    mode = "discover" if shaping["flavour"] == "discovery" else "refine"
    return prepared, mode


def start_shaping(vault_root, router, params):
    """Compatibility wrapper for pre-v0.53 script imports."""
    if not params or "target" not in params:
        return {"error": "start_shaping requires params: {target}"}
    target = params["target"]
    requested_mode = params.get("mode")
    legacy_skill_type = params.get("skill_type")
    mode = requested_mode.lower() if isinstance(requested_mode, str) else None
    prepared = None
    try:
        if mode is None:
            legacy_mode = (
                legacy_skill_type.lower()
                if isinstance(legacy_skill_type, str)
                else None
            )
            if legacy_mode in SHAPING_MODES:
                mode = legacy_mode
            else:
                prepared, mode = _legacy_target_and_default_mode(
                    vault_root, router, target
                )
                target = prepared.resolved_path
        result = start_shaping_session(
            vault_root,
            router,
            target,
            mode=mode,
            _prepared_target=prepared,
        )
    except (ValueError, FileNotFoundError) as exc:
        return {"error": str(exc)}
    return {
        **result,
        "set_status": result["status_changed"],
        "appended": result["transcript_operation"] == "appended",
    }


def _build_parser():
    parser = argparse.ArgumentParser(
        description="Open a shaping session for an existing artefact."
    )
    parser.add_argument("--target", required=True)
    parser.add_argument("--mode", choices=SHAPING_MODES)
    parser.add_argument("--skill-type")
    parser.add_argument(
        "--title",
        help="Legacy option retained for compatibility; transcript identity is source-based.",
    )
    parser.add_argument("--vault")
    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    vault_root = str(find_vault_root(args.vault))
    router = load_fresh_compiled_router(vault_root)
    if "error" in router:
        raise SystemExit(f"Error: {router['error']}")
    params = {"target": args.target}
    if args.mode is not None:
        params["mode"] = args.mode
    if args.skill_type is not None:
        params["skill_type"] = args.skill_type
    if args.title is not None:
        params["title"] = args.title
    try:
        with vault_mutation_lock(vault_root):
            result = start_shaping(vault_root, router, params)
        if "error" in result:
            raise ValueError(result["error"])
    except (
        MutationLockError,
        PartialApplyError,
        ValueError,
        FileNotFoundError,
        OSError,
    ) as exc:
        print(f"Error: {public_mutation_error_message(exc)}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
