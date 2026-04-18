#!/usr/bin/env bash
# sync-template-vault.sh — Keep template-vault/ in sync with src/brain-core/.
#
# template-vault/_Config/ contains taxonomy and template files derived from
# src/brain-core/artefact-library/. Those derived files drift when brain-core
# taxonomy/templates change but template-vault isn't re-synced. This script
# detects and resolves that drift using sync_definitions.py, which is the
# canonical tool for comparing artefact-library sources to vault _Config.
#
# template-vault/.brain-core is a symlink to ../src/brain-core for dev
# convenience. sync_definitions.py reads from .brain-core/artefact-library/
# (via the symlink) and writes only to _Config/ and .brain/tracking.json —
# never into .brain-core/ itself, so the symlink is safe.
#
# Modes:
#   (default / --check)  Report drift. Exits 0 if in sync, 1 if drift, 2 on error.
#   --apply              Canonical maintenance flow:
#                        1. force-sync template-vault definitions/tracking
#                        2. recompile the template vault router
#                        Refuses to run if template-vault/ has uncommitted changes
#                        (workflow hygiene — keeps the post-apply diff reviewable).
#
# Why --force is safe here: template-vault/_Config/ is meant to always track the
# artefact library exactly. It has no legitimate "local changes" to preserve —
# any divergence is stale derivation, not intentional customisation. So --force
# overwrite is the correct semantic for this target.
#
# "In sync" = every installed type is classified as `in_sync`. Types in
# `uninstalled` are ignored — those are intentional starter-vault exclusions.

set -euo pipefail

MODE=check
for arg in "$@"; do
    case "$arg" in
        --check) MODE=check ;;
        --apply) MODE=apply ;;
        -h|--help)
            sed -n '2,21p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *)
            echo "error: unknown argument: $arg" >&2
            exit 2 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BRAIN_CORE="$REPO_ROOT/src/brain-core"
TEMPLATE_VAULT="$REPO_ROOT/template-vault"
SYNC_SCRIPT="$BRAIN_CORE/scripts/sync_definitions.py"
COMPILE_SCRIPT="$BRAIN_CORE/scripts/compile_router.py"
PYTHON_BIN="${PYTHON_BIN:-}"

if [ -z "$PYTHON_BIN" ]; then
    if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
        PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
    else
        PYTHON_BIN="python3"
    fi
fi

if [ ! -f "$BRAIN_CORE/VERSION" ]; then
    echo "error: $BRAIN_CORE/VERSION not found — is this the right repo?" >&2
    exit 2
fi
if [ ! -d "$TEMPLATE_VAULT" ]; then
    echo "error: $TEMPLATE_VAULT not found" >&2
    exit 2
fi
if [ ! -f "$SYNC_SCRIPT" ]; then
    echo "error: $SYNC_SCRIPT not found" >&2
    exit 2
fi
if [ ! -f "$COMPILE_SCRIPT" ]; then
    echo "error: $COMPILE_SCRIPT not found" >&2
    exit 2
fi
if [ ! -e "$TEMPLATE_VAULT/.brain-core" ]; then
    echo "error: $TEMPLATE_VAULT/.brain-core not found — run 'make dev-link' first." >&2
    exit 2
fi

VERSION="$(cat "$BRAIN_CORE/VERSION")"

if [ "$MODE" = apply ]; then
    if ! git -C "$REPO_ROOT" diff --quiet -- template-vault/ || \
       ! git -C "$REPO_ROOT" diff --cached --quiet -- template-vault/; then
        echo "error: template-vault/ has uncommitted changes. Commit or stash them before --apply." >&2
        exit 2
    fi
fi

if [ "$MODE" = check ]; then
    STATUS_JSON=$("$PYTHON_BIN" "$SYNC_SCRIPT" --vault "$TEMPLATE_VAULT" --status --json 2>&1) || {
        echo "error: sync_definitions.py --status failed:" >&2
        echo "$STATUS_JSON" >&2
        exit 2
    }

    SUMMARY=$(STATUS_JSON="$STATUS_JSON" python3 -c '
import json, os
data = json.loads(os.environ["STATUS_JSON"])
types = data.get("types", {})
not_installable = data.get("not_installable", [])
if not_installable:
    print("BROKEN")
    for entry in not_installable:
        print("  [not_installable] {}: {}".format(
            entry.get("type", "?"),
            entry.get("reason", "unknown"),
        ))
    raise SystemExit(0)
drift_categories = ("sync_ready", "locally_customised", "conflict")
drifted = []
for cat in drift_categories:
    for entry in types.get(cat, []):
        changed = ", ".join(
            f"{role}={state}"
            for role, state in sorted(entry.get("files", {}).items())
            if state != "in_sync"
        )
        drifted.append((cat, entry.get("type", "?"), changed))
if not drifted:
    print("OK")
else:
    print("DRIFT")
    for cat, t, changed in drifted:
        suffix = f" ({changed})" if changed else ""
        print(f"  [{cat}] {t}{suffix}")
')
    SUMMARY_STATE="$(echo "$SUMMARY" | head -1)"

    if [ "$SUMMARY_STATE" = "OK" ]; then
        echo "template-vault in sync with brain-core v$VERSION"
        exit 0
    fi
    if [ "$SUMMARY_STATE" = "BROKEN" ]; then
        echo "template-vault cannot be synced cleanly against brain-core v$VERSION:" >&2
        echo "$SUMMARY" | tail -n +2 >&2
        exit 2
    fi

    echo "template-vault drift detected against brain-core v$VERSION:"
    echo "$SUMMARY" | tail -n +2
    echo
    echo "Drift may be file content (_Config/ differs from upstream) or stale"
    echo "tracking metadata (.brain/tracking.json records older installed hashes"
    echo "even though content now matches). --apply resolves both and recompiles."
    echo
    echo "To resolve: run '$0 --apply', then review and stage template-vault/ changes."
    exit 1
fi

# Apply mode: canonical maintenance flow.
# Writes to template-vault/_Config/, template-vault/.brain/tracking.json,
# and template-vault/.brain/local/compiled-router.json.
"$PYTHON_BIN" "$SYNC_SCRIPT" --vault "$TEMPLATE_VAULT" --force
(
    cd "$TEMPLATE_VAULT"
    "$PYTHON_BIN" "$COMPILE_SCRIPT"
)

echo
echo "Changed files in template-vault/:"
CHANGED=$(git -C "$REPO_ROOT" status --short -- template-vault/)
if [ -z "$CHANGED" ]; then
    echo "  (none — template-vault was already in sync)"
else
    echo "$CHANGED" | sed 's/^/  /'
    echo
    echo "Review the diff and stage what's appropriate."
fi
exit 0
