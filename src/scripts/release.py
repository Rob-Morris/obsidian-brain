#!/usr/bin/env python3
"""Inspect, prepare, or export an exact Brain release source tree."""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from dataclasses import asdict
from datetime import date
import difflib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
from typing import Callable
import uuid


REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_ROOT = REPO_ROOT / "cli"
if str(CLI_ROOT) not in sys.path:
    sys.path.insert(0, str(CLI_ROOT))

from _version_contract import (  # noqa: E402
    SEMVER_PATTERN as SEMVER,
    SEMVER_RE,
    VersionContract,
    parse_version_contract,
)
VERSION_PATH = "src/brain-core/VERSION"
README_PATH = "README.md"
UNIX_CLI_PATH = "cli/brain"
WINDOWS_CLI_PATH = "cli/brain.cmd"
PROXY_PATH = "src/brain-core/brain_mcp/proxy.py"
CHANGELOG_INDEX_PATH = "docs/CHANGELOG.md"
FUNCTIONAL_CLI_PATH = "docs/functional/cli.md"
USER_REFERENCE_PATH = "docs/user/user-reference.md"
COMMAND_CATALOGUE_PATH = "src/brain-core/command-catalogue.json"


class ReleaseError(RuntimeError):
    pass


class ReleaseTransactionError(ReleaseError):
    """A release write failed; rollback outcome and recovery files are explicit."""

    def __init__(
        self,
        initiating_error: BaseException,
        *,
        rollback_complete: bool,
        recovery_paths: tuple[Path, ...] = (),
    ) -> None:
        self.initiating_error = initiating_error
        self.rollback_complete = rollback_complete
        self.recovery_paths = recovery_paths
        outcome = "rollback complete"
        if not rollback_complete:
            rendered = ", ".join(str(path) for path in recovery_paths) or "unavailable"
            outcome = f"rollback incomplete; inspect recovery paths: {rendered}"
        super().__init__(f"release write failed: {initiating_error}; {outcome}")


def _git(root: Path, *args: str, text: bool = True):
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=text,
    ).stdout


def _read_view(root: Path, view: str, path: str) -> str | None:
    try:
        if view == "worktree":
            return (root / path).read_text(encoding="utf-8")
        spec = f":{path}" if view == "index" else f"HEAD:{path}"
        return _git(root, "show", spec)
    except (OSError, UnicodeError, subprocess.CalledProcessError):
        return None


def release_facts(root: Path, view: str) -> VersionContract:
    texts = {
        path: _read_view(root, view, path)
        for path in (
            VERSION_PATH,
            README_PATH,
            UNIX_CLI_PATH,
            WINDOWS_CLI_PATH,
            PROXY_PATH,
            CHANGELOG_INDEX_PATH,
            FUNCTIONAL_CLI_PATH,
            USER_REFERENCE_PATH,
        )
    }
    return parse_version_contract(
        core=texts[VERSION_PATH],
        readme=texts[README_PATH],
        unix_cli=texts[UNIX_CLI_PATH],
        windows_cli=texts[WINDOWS_CLI_PATH],
        proxy=texts[PROXY_PATH],
        changelog=texts[CHANGELOG_INDEX_PATH],
        functional_cli=texts[FUNCTIONAL_CLI_PATH],
        user_reference=texts[USER_REFERENCE_PATH],
    )


def _status(args: argparse.Namespace) -> int:
    views = ("head", "index", "worktree")
    facts = {view: release_facts(args.repo, view) for view in views}
    if args.json:
        print(json.dumps({name: asdict(value) for name, value in facts.items()}, indent=2))
    else:
        print("view       core     cli      proxy    changelog coherent")
        for view in views:
            value = facts[view]
            print(
                f"{view:<10} {value.core or '-':<8} {value.cli_unix or '-':<8} "
                f"{value.proxy or '-':<8} {value.changelog_head or '-':<9} "
                f"{'yes' if value.coherent else 'NO'}"
            )
    return 1 if args.check and not facts[args.check].coherent else 0


def _replace_one(
    text: str,
    pattern: str,
    replacement: str | Callable[[re.Match[str]], str],
    path: str,
) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise ReleaseError(f"{path}: expected exactly one release declaration")
    return updated


def _summary(entry: str, version: str) -> str | None:
    match = re.search(
        rf"^# v{re.escape(version)}\s*$.*?^\*\*Summary:\*\*\s*(.+?)\s*$",
        entry,
        re.MULTILINE | re.DOTALL,
    )
    return match.group(1) if match else None


def _index_with_release(index: str, version: str, release_date: str, summary: str) -> str:
    row_pattern = re.compile(
        rf"^\| \[v{re.escape(version)}\]\(changelog/v{re.escape(version)}\.md\).*$",
        re.MULTILINE,
    )
    rows = row_pattern.findall(index)
    expected = (
        f"| [v{version}](changelog/v{version}.md) | {release_date} | {summary} |"
    )
    if rows:
        if rows != [expected]:
            raise ReleaseError(f"{CHANGELOG_INDEX_PATH}: existing v{version} row differs")
        return index
    marker = "|---|---|---|\n"
    if index.count(marker) != 1:
        raise ReleaseError(f"{CHANGELOG_INDEX_PATH}: release table marker is ambiguous")
    return index.replace(marker, marker + expected + "\n", 1)


def _render_command_catalogue_route(root: Path) -> str:
    """Render the derived catalogue route from the selected source tree."""
    scripts_root = root / "src" / "brain-core" / "scripts"
    source = """
import json
from _application.registry import current_application_catalogue

catalogue = current_application_catalogue()
print(json.dumps({
    "schema": catalogue.schema,
    "interface_epoch": catalogue.interface_epoch,
    "static_fingerprint": catalogue.fingerprint,
    "installed_application_command_count": len(catalogue.entries),
}, indent=2))
"""
    environment = os.environ.copy()
    existing_path = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = os.pathsep.join(
        value for value in (str(scripts_root), existing_path) if value
    )
    completed = subprocess.run(
        [sys.executable, "-c", source],
        cwd=root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        diagnostic = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
        raise ReleaseError(f"cannot render {COMMAND_CATALOGUE_PATH}: {diagnostic}")
    try:
        route = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ReleaseError(f"cannot render {COMMAND_CATALOGUE_PATH}: invalid JSON") from exc
    if not isinstance(route, dict):
        raise ReleaseError(f"cannot render {COMMAND_CATALOGUE_PATH}: expected an object")
    return json.dumps(route, indent=2) + "\n"


def _prepare_changes(args: argparse.Namespace) -> dict[str, str]:
    for name, value in (
        ("core version", args.core_version),
        ("CLI version", args.cli_version),
        ("proxy version", args.proxy_version),
    ):
        if value is not None and not SEMVER_RE.fullmatch(value):
            raise ReleaseError(f"invalid {name}: {value!r}")
    if args.summary.endswith(".") or re.search(rf"\s+\(?v{SEMVER}\)?$", args.summary):
        raise ReleaseError("release Summary must omit periods and version suffixes")

    root = args.repo
    current = release_facts(root, "worktree")
    cli_version = args.cli_version or current.cli_unix
    proxy_version = args.proxy_version or current.proxy
    if cli_version is None or proxy_version is None:
        raise ReleaseError("current CLI/proxy versions are not uniquely readable")
    paths = (
        VERSION_PATH,
        README_PATH,
        UNIX_CLI_PATH,
        WINDOWS_CLI_PATH,
        PROXY_PATH,
        CHANGELOG_INDEX_PATH,
        FUNCTIONAL_CLI_PATH,
        USER_REFERENCE_PATH,
        COMMAND_CATALOGUE_PATH,
    )
    original = {path: (root / path).read_text(encoding="utf-8") for path in paths}
    changed = dict(original)
    changed[COMMAND_CATALOGUE_PATH] = _render_command_catalogue_route(root)
    changed[VERSION_PATH] = args.core_version + "\n"
    changed[README_PATH] = _replace_one(
        original[README_PATH],
        rf"version-{SEMVER}-blue",
        f"version-{args.core_version}-blue",
        README_PATH,
    )
    for path, cli_pattern, ref_pattern, cli_replacement, ref_replacement in (
        (
            UNIX_CLI_PATH,
            rf'^BRAIN_CLI_VERSION="{SEMVER}"$',
            rf'^BRAIN_INSTALL_REF="v{SEMVER}"$',
            f'BRAIN_CLI_VERSION="{cli_version}"',
            f'BRAIN_INSTALL_REF="v{args.core_version}"',
        ),
        (
            WINDOWS_CLI_PATH,
            rf'^set "BRAIN_CLI_VERSION={SEMVER}"$',
            rf'^set "BRAIN_INSTALL_REF=v{SEMVER}"$',
            f'set "BRAIN_CLI_VERSION={cli_version}"',
            f'set "BRAIN_INSTALL_REF=v{args.core_version}"',
        ),
    ):
        changed[path] = _replace_one(original[path], cli_pattern, cli_replacement, path)
        changed[path] = _replace_one(changed[path], ref_pattern, ref_replacement, path)
    changed[PROXY_PATH] = _replace_one(
        original[PROXY_PATH],
        rf'^PROXY_VERSION = "{SEMVER}"$',
        f'PROXY_VERSION = "{proxy_version}"',
        PROXY_PATH,
    )
    functional = _replace_one(
        original[FUNCTIONAL_CLI_PATH],
        rf"brain-cli/{SEMVER}/",
        f"brain-cli/{cli_version}/",
        FUNCTIONAL_CLI_PATH,
    )
    functional = _replace_one(
        functional,
        rf"brain-cli\\{SEMVER}\\",
        lambda _match: f"brain-cli\\{cli_version}\\",
        FUNCTIONAL_CLI_PATH,
    )
    functional = _replace_one(
        functional,
        rf"`BRAIN_CLI_VERSION` is `{SEMVER}`; `BRAIN_INSTALL_REF` is `v{SEMVER}`",
        f"`BRAIN_CLI_VERSION` is `{cli_version}`; `BRAIN_INSTALL_REF` is `v{args.core_version}`",
        FUNCTIONAL_CLI_PATH,
    )
    changed[FUNCTIONAL_CLI_PATH] = functional
    changed[USER_REFERENCE_PATH] = _replace_one(
        original[USER_REFERENCE_PATH],
        rf"Brain Core {SEMVER} and CLI {SEMVER}",
        f"Brain Core {args.core_version} and CLI {cli_version}",
        USER_REFERENCE_PATH,
    )

    entry_path = f"docs/changelog/v{args.core_version}.md"
    entry_file = root / entry_path
    if entry_file.exists():
        if not args.amend:
            raise ReleaseError(f"{entry_path}: already exists; pass --amend deliberately")
        entry = entry_file.read_text(encoding="utf-8")
        if _summary(entry, args.core_version) != args.summary:
            raise ReleaseError(f"{entry_path}: existing Summary differs")
    else:
        if args.amend:
            raise ReleaseError(f"{entry_path}: cannot amend a missing release entry")
        if not args.release_type or not args.change:
            raise ReleaseError("new releases require --release-type and at least one --change")
        bullets = "\n".join(f"- {item}" for item in args.change)
        entry = (
            f"# v{args.core_version}\n\n"
            f"**Summary:** {args.summary}\n\n"
            f"**Type:** {args.release_type}\n\n"
            f"## Changed\n\n{bullets}\n"
        )
    changed[entry_path] = entry
    changed[CHANGELOG_INDEX_PATH] = _index_with_release(
        original[CHANGELOG_INDEX_PATH], args.core_version, args.date, args.summary
    )
    return {
        path: content
        for path, content in changed.items()
        if not (root / path).exists()
        or (root / path).read_text(encoding="utf-8") != content
    }


def _diff(root: Path, changes: dict[str, str]) -> str:
    output = []
    for path, after in sorted(changes.items()):
        target = root / path
        before = target.read_text(encoding="utf-8") if target.exists() else ""
        output.extend(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
            )
        )
    return "".join(output)


def _write_transaction(root: Path, changes: dict[str, str]) -> None:
    staged: dict[str, Path] = {}
    originals: dict[str, tuple[bytes, int] | None] = {}
    replaced: list[str] = []
    try:
        for path, content in changes.items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            originals[path] = (
                (target.read_bytes(), target.stat().st_mode) if target.exists() else None
            )
            stage = target.with_name(f".{target.name}.{uuid.uuid4().hex}.release")
            staged[path] = stage
            stage.write_text(content, encoding="utf-8")
            if target.exists():
                os.chmod(stage, target.stat().st_mode)
        for path, stage in staged.items():
            os.replace(stage, root / path)
            replaced.append(path)
    except BaseException as initiating_error:
        recovery_paths: list[Path] = []
        for path in reversed(replaced):
            original = originals[path]
            target = root / path
            restore: Path | None = None
            try:
                if original is None:
                    target.unlink(missing_ok=True)
                    continue
                data, mode = original
                restore = target.with_name(
                    f".{target.name}.{uuid.uuid4().hex}.restore"
                )
                restore.write_bytes(data)
                os.chmod(restore, mode)
                os.replace(restore, target)
            except BaseException:
                recovery_paths.append(target)
                if restore is not None and restore.exists():
                    recovery_paths.append(restore)
        recovery_paths.extend(_remove_staged_files(staged.values()))
        error = ReleaseTransactionError(
            initiating_error,
            rollback_complete=not recovery_paths,
            recovery_paths=tuple(recovery_paths),
        )
        if isinstance(initiating_error, (KeyboardInterrupt, SystemExit)):
            initiating_error.add_note(str(error))
            raise
        raise error from initiating_error
    cleanup_failures = _remove_staged_files(staged.values())
    if cleanup_failures:
        rendered = ", ".join(str(path) for path in cleanup_failures)
        raise ReleaseError(f"release committed but stale staging files remain: {rendered}")


def _remove_staged_files(paths: Iterable[Path]) -> tuple[Path, ...]:
    """Remove all transaction stages, retaining every path that could not be removed."""
    failures = []
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            failures.append(path)
    return tuple(failures)


def _prepare(args: argparse.Namespace) -> int:
    changes = _prepare_changes(args)
    print(_diff(args.repo, changes), end="")
    if not changes:
        print("release preparation: no changes")
        return 0
    if not args.apply:
        print("release preparation: dry run; pass --apply to write")
        return 0
    _write_transaction(args.repo, changes)
    print(f"release preparation: updated {len(changes)} files")
    return 0


def _safe_archive_member(member: tarfile.TarInfo) -> bool:
    path = Path(member.name)
    return not path.is_absolute() and ".." not in path.parts


def _export(args: argparse.Namespace) -> int:
    destination = args.destination.expanduser().resolve()
    if destination.exists():
        raise ReleaseError(f"export destination already exists: {destination}")
    commit = _git(args.repo, "rev-parse", "--verify", f"{args.ref}^{{commit}}").strip()
    archive = _git(args.repo, "archive", "--format=tar", commit, text=False)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{destination.name}.release-", dir=destination.parent
    ) as temp_dir:
        stage = Path(temp_dir) / "source"
        stage.mkdir()
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
            members = bundle.getmembers()
            if any(not _safe_archive_member(member) for member in members):
                raise ReleaseError("Git archive contains an unsafe path")
            bundle.extractall(stage, members=members, filter="tar")
        os.replace(stage, destination)
    print(json.dumps({"commit": commit, "destination": str(destination)}))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=REPO_ROOT)
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status", help="compare release facts across Git views")
    status.add_argument("--json", action="store_true")
    status.add_argument("--check", choices=("head", "index", "worktree"))
    status.set_defaults(handler=_status)
    prepare = sub.add_parser("prepare", help="preview or apply explicit release mechanics")
    prepare.add_argument("--core-version", required=True)
    prepare.add_argument("--cli-version")
    prepare.add_argument("--proxy-version")
    prepare.add_argument("--summary", required=True)
    prepare.add_argument("--date", default=date.today().isoformat())
    prepare.add_argument("--release-type")
    prepare.add_argument("--change", action="append", default=[])
    prepare.add_argument("--amend", action="store_true")
    prepare.add_argument("--apply", action="store_true")
    prepare.set_defaults(handler=_prepare)
    export = sub.add_parser("export", help="materialise one committed source tree")
    export.add_argument("--ref", default="HEAD")
    export.add_argument("--destination", type=Path, required=True)
    export.set_defaults(handler=_export)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    args.repo = args.repo.resolve()
    try:
        return args.handler(args)
    except (OSError, ReleaseError, subprocess.CalledProcessError, tarfile.TarError) as exc:
        print(f"release: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
