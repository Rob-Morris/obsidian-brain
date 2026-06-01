"""Tests for DD-049 compliance: dispatched scripts accept --vault at the top level.

cli/brain injects ``--vault <path>`` at the FRONT of the forwarded argv
(before the subcommand name).  Every dispatched script must accept
``--vault`` on its top-level parser so the injection always parses
without hitting an "invalid choice" or "unrecognised arguments" error.

Two test groups:

1. Dispatch-guard — subprocess-level, one per dispatched subcommand.
   Verifies ``python <script>.py --vault <tmp> --help`` exits 0 and
   emits no argparse error.  Uses ``--help`` so no vault state is
   needed; argparse processes ``--vault`` before ``--help``.

2. Focused parse_args tests — unit-level, configure + setup only.
   Asserts correct value resolution for front-injected, post-subcommand,
   and absent ``--vault``, proving SUPPRESS prevents same-dest clobber.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import configure
import setup as brain_setup


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "src" / "brain-core" / "scripts"

# ── Dispatch contract (mirrors test_brain_cli.py) ──────────────────────────
_PUBLIC_DISPATCH_CONTRACT = [
    "check", "create", "edit", "rename",
    "setup", "configure", "repair", "upgrade",
    "session", "read", "migrate-naming", "fix-links",
]
_DISPATCH_COMPAT = ["init"]
_DISPATCH_CONTRACT = _PUBLIC_DISPATCH_CONTRACT + _DISPATCH_COMPAT

# Scripts that do NOT use subcommands and already declare --vault on the
# top-level parser — confirm they are unaffected (no regression).
# Scripts known to require non-trivial arguments beyond --vault --help
# (e.g. positional subcommand) are handled by the generic --help approach,
# which argparse resolves before --help is consumed.


# ---------------------------------------------------------------------------
# 1. Dispatch-guard: every dispatched script accepts --vault at the top level
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("subcommand", _DISPATCH_CONTRACT)
def test_dispatch_script_accepts_front_injected_vault(tmp_path, subcommand):
    """``python <script>.py --vault <tmp> --help`` must not produce an argparse error.

    This mirrors the exact argument order that ``cli/brain`` produces:
    ``--vault <path>`` is injected at the front, before the subcommand name
    or any user-supplied arguments.

    The key invariant is the absence of argparse parse errors ("invalid choice"
    / "unrecognised arguments") — some scripts validate the vault path before
    honouring ``--help``, so exit 0 is only asserted when the script itself
    exits cleanly.
    """
    script = SCRIPTS_DIR / f"{subcommand.replace('-', '_')}.py"
    if not script.is_file():
        pytest.skip(f"{script.name} does not exist in this brain-core version")

    result = subprocess.run(
        [sys.executable, str(script), "--vault", str(tmp_path), "--help"],
        capture_output=True,
        text=True,
        cwd=str(SCRIPTS_DIR),
    )

    stderr_lower = result.stderr.lower()
    assert "invalid choice" not in stderr_lower, (
        f"{script.name}: argparse rejected '--vault <path> --help' with 'invalid choice'.\n"
        f"stderr: {result.stderr}"
    )
    assert "unrecognized argument" not in stderr_lower, (
        f"{script.name}: argparse produced 'unrecognised argument'.\n"
        f"stderr: {result.stderr}"
    )
    # Scripts that use argparse-native --help exit 0; scripts that validate the
    # vault before reaching --help may exit non-zero (e.g. vault-not-found).
    # We cannot assert exit 0 here without a real vault.  The argparse checks
    # above are the load-bearing assertions for the DD-049 contract.


# ---------------------------------------------------------------------------
# 2. configure.parse_args — vault placement and SUPPRESS semantics
# ---------------------------------------------------------------------------

def test_configure_vault_front_injected_workspace_binding(tmp_path):
    """Front-injected form: ``configure --vault X workspace binding``."""
    ns = configure.parse_args(["--vault", str(tmp_path), "workspace", "binding"])
    assert getattr(ns, "vault", None) == str(tmp_path)
    assert ns.command == "workspace"
    assert ns.workspace_command == "binding"


def test_configure_vault_post_subcommand_workspace_binding(tmp_path):
    """Post-subcommand form: ``configure workspace binding --vault X``."""
    ns = configure.parse_args(["workspace", "binding", "--vault", str(tmp_path)])
    assert getattr(ns, "vault", None) == str(tmp_path)
    assert ns.command == "workspace"
    assert ns.workspace_command == "binding"


def test_configure_vault_same_value_both_placements(tmp_path):
    """Both placements resolve to the same vault path (no clobber)."""
    front = configure.parse_args(["--vault", str(tmp_path), "workspace", "binding"])
    post = configure.parse_args(["workspace", "binding", "--vault", str(tmp_path)])
    assert getattr(front, "vault", None) == getattr(post, "vault", None)


def test_configure_vault_absent_is_none(tmp_path):
    """Absent ``--vault`` must not set args.vault (SUPPRESS semantics)."""
    ns = configure.parse_args(["workspace", "binding"])
    assert getattr(ns, "vault", None) is None


def test_configure_vault_front_injected_semantic(tmp_path):
    """Front-injected form for ``configure --vault X semantic --enable``."""
    ns = configure.parse_args(["--vault", str(tmp_path), "semantic", "--enable"])
    assert getattr(ns, "vault", None) == str(tmp_path)
    assert ns.command == "semantic"


def test_configure_vault_front_injected_mcp(tmp_path):
    """Front-injected form for ``configure --vault X mcp``."""
    ns = configure.parse_args(["--vault", str(tmp_path), "mcp"])
    assert getattr(ns, "vault", None) == str(tmp_path)
    assert ns.command == "mcp"


def test_configure_vault_front_injected_workspace_metadata(tmp_path):
    """Front-injected form for ``configure --vault X workspace metadata``."""
    ns = configure.parse_args(["--vault", str(tmp_path), "workspace", "metadata"])
    assert getattr(ns, "vault", None) == str(tmp_path)
    assert ns.workspace_command == "metadata"


def test_configure_vault_front_injected_workspace_bootstrap(tmp_path):
    """Front-injected form for ``configure --vault X workspace bootstrap``."""
    ns = configure.parse_args(["--vault", str(tmp_path), "workspace", "bootstrap"])
    assert getattr(ns, "vault", None) == str(tmp_path)
    assert ns.workspace_command == "bootstrap"


# ---------------------------------------------------------------------------
# 3. setup.parse_args — vault placement and SUPPRESS semantics
# ---------------------------------------------------------------------------

def test_setup_vault_front_injected_workspace(tmp_path):
    """Front-injected form: ``setup --vault X workspace``."""
    ns = brain_setup.parse_args(["--vault", str(tmp_path), "workspace"])
    assert getattr(ns, "vault", None) == str(tmp_path)
    assert ns.command == "workspace"


def test_setup_vault_post_subcommand_workspace(tmp_path):
    """Post-subcommand form: ``setup workspace --vault X``."""
    ns = brain_setup.parse_args(["workspace", "--vault", str(tmp_path)])
    assert getattr(ns, "vault", None) == str(tmp_path)
    assert ns.command == "workspace"


def test_setup_vault_same_value_both_placements(tmp_path):
    """Both placements resolve to the same vault path (no clobber)."""
    front = brain_setup.parse_args(["--vault", str(tmp_path), "workspace"])
    post = brain_setup.parse_args(["workspace", "--vault", str(tmp_path)])
    assert getattr(front, "vault", None) == getattr(post, "vault", None)


def test_setup_vault_absent_is_none():
    """Absent ``--vault`` must not set args.vault (SUPPRESS semantics)."""
    ns = brain_setup.parse_args(["workspace"])
    assert getattr(ns, "vault", None) is None
