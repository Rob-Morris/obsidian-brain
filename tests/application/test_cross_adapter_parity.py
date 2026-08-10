"""Deterministic semantic parity across eligible selected-Brain adapters."""

from __future__ import annotations

import asyncio
from datetime import datetime
from io import StringIO
import json
from pathlib import Path
import shutil
import subprocess
import sys

from mcp.server.fastmcp import FastMCP


REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_DIR = REPO_ROOT / "cli"
if str(CLI_DIR) not in sys.path:
    sys.path.insert(0, str(CLI_DIR))


from brain_mcp._command_adapter import register_application_tools
from _application.adapter import ApplicationAdapter
from _application.application import CommandApplication
from _application.projection import canonical_result_envelope
from _application.receipts import MemoryReceiptStore
from _application.registry import current_application_catalogue, current_request_resolver
from _application.types import DependencyTier, SnapshotFreshness
from _command_interface.context import compose_local_context
from _command_interface.script import run as run_direct_script
from _local_cli.discovery import ComposedCommandEntry
from _local_cli.execution import ApplicationProcessInvoker, SelectedBrainProcess


NOW = datetime.fromisoformat("2026-08-10T10:00:00+10:00")


class _Clock:
    def now(self):
        return NOW


def _vault(tmp_path):
    root = (tmp_path / "Brain").resolve()
    script = root / ".brain-core" / "scripts" / "command.py"
    script.parent.mkdir(parents=True)
    script.write_text("# selected Brain command owner\n", encoding="utf-8")
    (root / ".brain-core" / "VERSION").write_text("0.54.58\n", encoding="utf-8")
    return root


def _context(vault):
    clock = _Clock()
    return compose_local_context(
        vault_root=vault,
        brain_id="parity-brain",
        profile="reader",
        allowed_tools=frozenset(("brain_command_list",)),
        dependency_tier=DependencyTier.PORTABLE,
        provider_ids=(),
        capability_states=(),
        snapshot_token="snapshot-parity",
        snapshot_freshness=SnapshotFreshness.FRESH,
        snapshot_observed_at=NOW,
        correlation_id="corr-parity",
        invocation_id="inv-parity",
        receipt_store=MemoryReceiptStore(clock),
        clock=clock,
    )


def _context_factory(vault):
    def create(**_metadata):
        return _context(vault)

    return create


def _direct(vault, payload):
    stdout = StringIO()
    stderr = StringIO()
    code = run_direct_script(
        [
            "command",
            "list",
            "--request-json",
            json.dumps(payload, separators=(",", ":")),
            "--vault",
            str(vault),
            "--json",
        ],
        stdout=stdout,
        stderr=stderr,
        context_factory=_context_factory(vault),
    )
    return code, stdout.getvalue(), stderr.getvalue()


def _mcp(vault, payload):
    mcp = FastMCP("parity")
    register_application_tools(
        mcp,
        catalogue=current_application_catalogue(),
        resolver=current_request_resolver(),
        context_factory=_context_factory(vault),
    )
    return asyncio.run(mcp.call_tool("brain_command_list", payload))


def _local_cli(vault, payload):
    catalogue = current_application_catalogue()
    catalogue_entry = next(
        entry for entry in catalogue.entries if entry.command_id == "command.list"
    )
    entry = ComposedCommandEntry(
        "application",
        catalogue.schema,
        catalogue.fingerprint,
        catalogue_entry.command_id,
        catalogue_entry.command_version,
        catalogue_entry.summary,
        {
            "command_id": catalogue_entry.command_id,
            "command_version": catalogue_entry.command_version,
        },
    )

    def runner(argv, **_options):
        stdout = StringIO()
        stderr = StringIO()
        code = run_direct_script(
            argv[2:],
            stdout=stdout,
            stderr=stderr,
            context_factory=_context_factory(vault),
        )
        return subprocess.CompletedProcess(
            argv,
            code,
            stdout.getvalue(),
            stderr.getvalue(),
        )

    return ApplicationProcessInvoker(
        SelectedBrainProcess(vault, Path(sys.executable).resolve()),
        runner,
    ).invoke(entry, payload)


def test_equivalent_valid_intent_has_one_result_across_all_eligible_adapters(tmp_path):
    vault = _vault(tmp_path)
    payload = {"dependency_tier": "portable", "page_size": 1}
    catalogue = current_application_catalogue()
    resolver = current_request_resolver()
    request = resolver.resolve("command.list", payload)
    python_result = canonical_result_envelope(
        CommandApplication(_context(vault), catalogue).invoke(request)
    )
    dynamic = ApplicationAdapter(catalogue, resolver).invoke(
        _context(vault),
        "command.list",
        payload,
    )
    direct_code, direct_stdout, direct_stderr = _direct(vault, payload)
    mcp = _mcp(vault, payload)
    local = _local_cli(vault, payload)

    assert direct_code == dynamic.exit_code == local.exit_code == 0
    assert direct_stderr == ""
    assert [
        dynamic.structured_content,
        json.loads(direct_stdout),
        mcp.structuredContent,
        local.structured_content,
    ] == [python_result] * 4


def test_equivalent_known_invalid_intent_has_one_structural_error(tmp_path):
    vault = _vault(tmp_path)
    payload = {"unknown": True}
    catalogue = current_application_catalogue()
    resolver = current_request_resolver()
    dynamic = ApplicationAdapter(catalogue, resolver).invoke(
        _context(vault),
        "command.list",
        payload,
    )
    direct_code, direct_stdout, direct_stderr = _direct(vault, payload)
    mcp = _mcp(vault, payload)
    local = _local_cli(vault, payload)

    expected = dynamic.structured_content
    assert direct_code == dynamic.exit_code == local.exit_code == 2
    assert direct_stderr == ""
    assert [json.loads(direct_stdout), mcp.structuredContent, local.structured_content] == [
        expected,
        expected,
        expected,
    ]
    assert expected["error"]["code"] == "invalid_request"
    assert expected["error"]["effects"] == "none"


def test_local_cli_crosses_a_real_selected_brain_process_boundary(tmp_path):
    vault = (tmp_path / "Installed-Brain").resolve()
    shutil.copytree(REPO_ROOT / "src" / "brain-core", vault / ".brain-core")
    (vault / "README.md").write_text("selected Brain content\n", encoding="utf-8")
    (vault / ".brain").mkdir()
    (vault / ".brain" / "config.yaml").write_text(
        "vault:\n"
        "  profiles:\n"
        "    reader:\n"
        "      allow: [brain_vault_read_file]\n"
        "defaults:\n"
        "  default_profile: reader\n",
        encoding="utf-8",
    )
    catalogue = current_application_catalogue()
    catalogue_entry = next(
        entry for entry in catalogue.entries if entry.command_id == "vault.read-file"
    )
    entry = ComposedCommandEntry(
        "application",
        catalogue.schema,
        catalogue.fingerprint,
        catalogue_entry.command_id,
        catalogue_entry.command_version,
        catalogue_entry.summary,
        {
            "command_id": catalogue_entry.command_id,
            "command_version": catalogue_entry.command_version,
        },
    )

    result = ApplicationProcessInvoker(
        SelectedBrainProcess(vault, Path(sys.executable).resolve())
    ).invoke(entry, {"path": "README.md"})

    assert result.exit_code == 0
    assert result.structured_content["result"] == {
        "path": "README.md",
        "content": "selected Brain content\n",
    }
