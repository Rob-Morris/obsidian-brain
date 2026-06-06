#!/usr/bin/env python3
"""install.py — shared Python installer core for Brain vault setup."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from _bootstrap import mcp_transport
from _bootstrap.runtime import step as _step
from _bootstrap.workspace_scaffold import GitInspectionError, ensure_brain_ignore_rules
from _common import ensure_central_venv, vault_requirements_path
from _lifecycle_common import (
    emit_lifecycle_result,
    exit_code_for_result,
    make_result_envelope,
    render_human_result,
)
import vault_registry


TEMPLATE_COPY_IGNORE = shutil.ignore_patterns(
    ".brain-core",
    ".pytest_cache",
    ".venv",
    ".mcp.json",
)
# Nested machine-local artefacts are scrubbed after copy; this copy-time ignore
# only covers basename patterns that shutil can skip before copying large trees.
EXISTING_VAULT_SCAFFOLD_DIRS = (
    "_Config",
    "_Assets",
    "_Temporal",
    "_Plugins",
    "_Workspaces",
    ".backups",
)
SUPPORTED_MCP_SCOPES = ("project", "user", "skip")


def _default_source_root() -> Path:
    """Return the repo root when this script runs from a source checkout."""
    return Path(__file__).resolve().parents[3]


def _source_brain_core(source_root: Path) -> Path:
    return source_root / "src" / "brain-core"


def _source_template_vault(source_root: Path) -> Path:
    return source_root / "template-vault"


def _resolve_launcher_arg(launcher: str | Path | None) -> Path:
    if launcher is None:
        return Path(sys.executable).resolve()
    launcher_text = str(launcher)
    if os.path.dirname(launcher_text):
        return Path(launcher_text).expanduser().resolve()
    resolved = shutil.which(launcher_text)
    if resolved is not None:
        return Path(resolved).resolve()
    return Path(launcher_text)


def _resolve_vault_root(raw_vault_root: str | Path) -> Path:
    vault_root = Path(raw_vault_root).expanduser()
    if not vault_root.is_absolute():
        vault_root = Path.cwd() / vault_root
    return vault_root.resolve()


def _copy_tree_contents(source: Path, destination: Path, *, ignore=None) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, dirs_exist_ok=True, ignore=ignore)


def _remove_existing(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _install_brain_core(vault_root: Path, brain_core_source: Path) -> None:
    target = vault_root / ".brain-core"
    _remove_existing(target)
    shutil.copytree(brain_core_source, target)


def _scrub_machine_local_template_state(vault_root: Path) -> None:
    for rel in (".pytest_cache", ".venv", ".brain/local"):
        path = vault_root / rel
        if path.exists() or path.is_symlink():
            _remove_existing(path)
    for rel in (".mcp.json", ".codex/config.toml"):
        path = vault_root / rel
        if path.exists() or path.is_symlink():
            path.unlink()
    try:
        (vault_root / ".codex").rmdir()
    except OSError:
        pass
    local = vault_root / ".brain" / "local"
    local.mkdir(parents=True, exist_ok=True)
    (local / ".gitkeep").touch()


def _scaffold_fresh_vault(vault_root: Path, source_root: Path) -> None:
    template = _source_template_vault(source_root)
    _copy_tree_contents(template, vault_root, ignore=TEMPLATE_COPY_IGNORE)
    _scrub_machine_local_template_state(vault_root)
    _install_brain_core(vault_root, _source_brain_core(source_root))


def _scaffold_existing_vault(vault_root: Path, source_root: Path) -> None:
    template = _source_template_vault(source_root)
    _install_brain_core(vault_root, _source_brain_core(source_root))

    for rel in EXISTING_VAULT_SCAFFOLD_DIRS:
        source = template / rel
        destination = vault_root / rel
        if source.is_dir() and not destination.exists():
            shutil.copytree(source, destination)

    if not (vault_root / "AGENTS.md").exists() and not (vault_root / "Agents.md").exists():
        shutil.copy2(template / "AGENTS.md", vault_root / "AGENTS.md")
    if not (vault_root / "CLAUDE.md").exists():
        shutil.copy2(template / "CLAUDE.md", vault_root / "CLAUDE.md")

    snippet = template / ".obsidian" / "snippets" / "brain-folder-colours.css"
    if snippet.exists():
        snippet_target = vault_root / ".obsidian" / "snippets" / snippet.name
        snippet_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(snippet, snippet_target)
    (vault_root / ".brain" / "local").mkdir(parents=True, exist_ok=True)
    (vault_root / ".brain" / "local" / ".gitkeep").touch()


def _is_existing_non_brain_vault(vault_root: Path) -> bool:
    return vault_root.is_dir() and any(vault_root.iterdir()) and not (vault_root / ".brain-core").exists()


def _destination_error(vault_root: Path, source_root: Path) -> str | None:
    if vault_root == Path(vault_root.anchor):
        return f"refusing to install into filesystem root: {vault_root}"
    try:
        if vault_root == Path.home().resolve():
            return "refusing to install into the user home directory; choose a subdirectory"
    except RuntimeError:
        pass
    if vault_root == source_root:
        return "refusing to install into the obsidian-brain source checkout"
    if not vault_root.parent.is_dir():
        return f"parent directory does not exist: {vault_root.parent}"
    return None


def _register_vault(vault_root: Path, brain_id: str | None) -> tuple[dict, str | None]:
    try:
        resolved_id = vault_registry.register(str(vault_root), brain_id=brain_id)
        message = f"Registered local Brain '{resolved_id}'."
        return _step("vault_registry", "changed", message, brain_id=resolved_id), resolved_id
    except (OSError, RuntimeError, ValueError) as exc:
        return _step("vault_registry", "error", f"Could not register vault: {exc}"), None


def _set_default_brain(brain_id: str | None) -> dict:
    if brain_id is None:
        return _step("machine_default", "error", "Could not set machine default because vault registration failed.")
    try:
        vault_registry.set_default(brain_id)
        return _step("machine_default", "changed", f"Set '{brain_id}' as the machine default Brain.")
    except (OSError, RuntimeError, ValueError) as exc:
        return _step("machine_default", "error", f"Could not set machine default Brain: {exc}")


def _ensure_git_ignore_rules(vault_root: Path, *, client: str, mcp_scope: str) -> dict:
    clients = ["claude", "codex"] if client == "all" else [client]
    try:
        message = ensure_brain_ignore_rules(
            vault_root,
            "project" if mcp_scope != "user" else "user",
            clients,
            skip_mcp=mcp_scope == "skip",
        )
        status = "changed" if message and message.startswith("Updated ") else "noop"
        return _step("git_ignore", status, message or "No git ignore changes needed.")
    except GitInspectionError as exc:
        return _step("git_ignore", "error", f"Could not update git ignore rules: {exc}")


def _ensure_managed_runtime(vault_root: Path, launcher: Path) -> dict:
    try:
        result = ensure_central_venv(vault_requirements_path(vault_root), launcher=launcher)
        status = "changed" if result.get("created") else "noop"
        message = "Created managed runtime." if status == "changed" else "Managed runtime already available."
        return _step(
            "managed_runtime",
            status,
            message,
            python=result.get("python"),
            venv_dir=result.get("venv_dir"),
        )
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        return _step("managed_runtime", "error", f"Could not provision managed runtime: {exc}")


def _configure_mcp(vault_root: Path, *, scope: str, client: str) -> tuple[dict, list[str]]:
    try:
        result = mcp_transport.apply_mcp_transport_action(
            vault_root,
            client_arg=client,
            scope=scope,
            target_dir=vault_root if scope == "project" else None,
            remove=False,
            vault_self=scope == "project",
        )
        notes = list(result.get("verification_notes", []))
        notes.extend(result.get("warnings", []))
        return (
            _step(
                "mcp_transport",
                result.get("status", "changed"),
                f"Configured Brain MCP transport for {client} ({scope}).",
            ),
            notes,
        )
    except mcp_transport.InitTransportError as exc:
        return _step("mcp_transport", "error", f"Could not configure MCP transport: {exc}"), []


def install_vault_action(
    vault_root: str | Path,
    *,
    source_root: str | Path | None = None,
    launcher: str | Path | None = None,
    mcp_scope: str = "project",
    client: str = "all",
    brain_id: str | None = None,
) -> dict:
    """Install Brain into a vault path using Python-owned install policy."""
    vault_root = _resolve_vault_root(vault_root)
    source_root = Path(source_root).resolve() if source_root is not None else _default_source_root()
    launcher_path = _resolve_launcher_arg(launcher)

    steps: list[dict[str, Any]] = []
    notes: list[str] = []

    def result() -> dict:
        return make_result_envelope(
            action="install",
            vault_root=vault_root,
            managed_python=sys.executable,
            steps=steps,
            notes=notes,
        )

    if not _source_template_vault(source_root).is_dir() or not _source_brain_core(source_root).is_dir():
        steps.append(_step("install_args", "error", f"source root is not an obsidian-brain checkout: {source_root}"))
        return result()

    if mcp_scope not in SUPPORTED_MCP_SCOPES:
        steps.append(_step("install_args", "error", f"invalid mcp_scope '{mcp_scope}'"))
        return result()
    if client not in {"claude", "codex", "all"}:
        steps.append(_step("install_args", "error", f"invalid client '{client}'"))
        return result()

    destination_error = _destination_error(vault_root, source_root)
    if destination_error is not None:
        steps.append(_step("install_args", "error", destination_error))
        return result()

    if (vault_root / ".brain-core").exists():
        steps.append(
            _step(
                "vault_scaffold",
                "error",
                "Brain is already installed at this vault; use upgrade.py for existing vault upgrades.",
            )
        )
        return result()

    try:
        if _is_existing_non_brain_vault(vault_root):
            _scaffold_existing_vault(vault_root, source_root)
            steps.append(_step("vault_scaffold", "changed", "Installed Brain into existing vault."))
        else:
            _scaffold_fresh_vault(vault_root, source_root)
            steps.append(_step("vault_scaffold", "changed", "Created Brain vault scaffold."))
    except OSError as exc:
        steps.append(_step("vault_scaffold", "error", f"Could not scaffold vault: {exc}"))
        return result()

    registry_step, resolved_id = _register_vault(vault_root, brain_id)
    steps.append(registry_step)
    if registry_step["status"] == "error":
        notes.append("Vault scaffold is present but NOT registered; run vault_registry.py --register for this vault.")
    steps.append(_ensure_git_ignore_rules(vault_root, client=client, mcp_scope=mcp_scope))

    if mcp_scope == "skip":
        steps.append(_step("mcp_transport", "noop", "MCP registration skipped."))
        notes.append("Register MCP later with configure.py mcp or init.py.")
    else:
        runtime_step = _ensure_managed_runtime(vault_root, launcher_path)
        steps.append(runtime_step)
        if runtime_step["status"] == "error":
            notes.append("Vault scaffold is present; rerun runtime repair before registering MCP.")
        else:
            mcp_step, mcp_notes = _configure_mcp(vault_root, scope=mcp_scope, client=client)
            steps.append(mcp_step)
            notes.extend(mcp_notes)
            if mcp_scope == "user" and mcp_step["status"] != "error":
                steps.append(_set_default_brain(resolved_id))

    return result()


def _render_human(result: dict) -> str:
    return render_human_result(result, subject_label="Install action", subject_key="action")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install Brain into a vault using the shared Python installer core.")
    parser.add_argument("vault", help="Destination vault directory.")
    parser.add_argument("--source-root", help="Source checkout root containing template-vault/ and src/brain-core/.")
    parser.add_argument("--launcher", help="Python launcher used to create the managed runtime.")
    parser.add_argument("--mcp-scope", choices=SUPPORTED_MCP_SCOPES, default="project")
    parser.add_argument("--client", choices=("claude", "codex", "all"), default="all")
    parser.add_argument("--id", dest="brain_id", help="Explicit local Brain ID for the machine registry.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    # Canonical launcher contract: 0 success, 1 partial/follow-up, 2 hard error.
    # install.sh and install.ps1 mirror this mapping for native launcher UX.
    try:
        result = install_vault_action(
            args.vault,
            source_root=args.source_root,
            launcher=args.launcher,
            mcp_scope=args.mcp_scope,
            client=args.client,
            brain_id=args.brain_id,
        )
    except Exception as exc:
        vault_root = _resolve_vault_root(args.vault)
        result = make_result_envelope(
            action="install",
            vault_root=vault_root,
            managed_python=sys.executable,
            steps=[_step("install", "error", f"Unhandled install error: {exc}")],
        )
    emit_lifecycle_result(result, as_json=args.json, render_human=_render_human)
    return exit_code_for_result(result)


if __name__ == "__main__":
    sys.exit(main())
