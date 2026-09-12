"""Ownership-safe client adapters for skills supplied by the active Brain."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

from _bootstrap.mcp_transport import SUPPORTED_CLIENTS
from _common import safe_write, safe_write_json

if TYPE_CHECKING:
    from _skill_library.models import PackageSnapshot


CLIENT_SKILLS_DIRS = {
    "claude": Path(".claude") / "skills",
    "codex": Path(".codex") / "skills",
    "grok": Path(".grok") / "skills",
}
ADAPTER_SKILL = "shaping"
BACKUP_DIR = ".brain-skill-backups"
MARKER_FILE = ".brain-agent-skill.json"
MARKER_OWNER = "obsidian-brain"
MARKER_KIND = "active-brain-skill-adapter"
MARKER_SCHEMA_VERSION = 1
INCIDENTAL_ENTRIES = {".DS_Store", "Thumbs.db"}
ADAPTER_TEMPLATE_REL = Path("client-adapters") / ADAPTER_SKILL / "SKILL.md"
ADAPTER_TEMPLATE_FILE = Path(__file__).resolve().parents[2] / ADAPTER_TEMPLATE_REL


class AgentSkillConfigError(RuntimeError):
    """A client skill cannot be changed without risking user-owned content."""


def load_effective_skill_adapter(
    vault_root: str | Path,
    skill_name: str,
    *,
    package_snapshot: PackageSnapshot | None = None,
) -> str:
    """Build a thin adapter that resolves the effective skill on every use."""
    try:
        from _skill_library.packages import inspect_package, validate_skill_name

        validate_skill_name(skill_name)
    except ValueError as exc:
        raise AgentSkillConfigError(str(exc)) from exc
    root = Path(vault_root)
    user = root / "_Config" / "Skills" / skill_name
    core = root / ".brain-core" / "skills" / skill_name
    if user.is_symlink():
        raise AgentSkillConfigError(
            f"effective user skill is a symlink and cannot be exposed: {user}"
        )
    from _portable.skill_resolution import effective_skill_path

    package = effective_skill_path(user, core)
    if not package.is_dir() or package.is_symlink():
        raise AgentSkillConfigError(f"Brain skill not found: {skill_name}")
    if package_snapshot is None:
        try:
            snapshot = inspect_package(package, expected_name=skill_name)
        except (OSError, ValueError) as exc:
            raise AgentSkillConfigError(f"invalid Brain skill {skill_name!r}: {exc}") from exc
    elif (
        package_snapshot.name != skill_name
        or package_snapshot.root != package
    ):
        raise AgentSkillConfigError(
            f"prepared package snapshot does not identify the effective skill {skill_name!r}"
        )
    else:
        snapshot = package_snapshot
    if skill_name == ADAPTER_SKILL:
        return load_shaping_adapter()
    if snapshot.executable_files:
        # Exposure loads instructions through the Brain; it does not install or
        # execute package assets. Keep the fact explicit for safety diagnostics.
        executable_note = (
            " The package contains executable files, but this adapter never "
            "executes or copies them."
        )
    else:
        executable_note = ""
    description = json.dumps(
        f"Loads the effective {skill_name} workflow from the currently active Brain."
    )
    return (
        "---\n"
        f"name: {skill_name}\n"
        f"description: {description}\n"
        "---\n\n"
        f"# Active Brain: {skill_name}\n\n"
        "This is a discovery adapter, not a local copy of the workflow.\n\n"
        "1. Call `session.start` to bootstrap the active Brain.\n"
        f"2. Call `resource.read(resource=\"skill\", reference=\"{skill_name}\")`.\n"
        "3. Follow the returned skill document as authoritative. Its `source` is "
        "`user` or `core`; unqualified resolution is user-first.\n"
        "4. Resolve any relative package file beneath "
        f"`_Config/Skills/{skill_name}/` when `source` is `user`, or beneath "
        f"`.brain-core/skills/{skill_name}/` when `source` is `core`, and load it "
        "with `vault.read-file`.\n\n"
        "Do not load workflow instructions from files beside this adapter. The "
        "active Brain owns resolution, content, and updates."
        f"{executable_note}\n"
    )


def load_shaping_adapter() -> str:
    """Read the shipped adapter only when the agent-skills surface needs it."""
    return ADAPTER_TEMPLATE_FILE.read_text(encoding="utf-8")


def _sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _marker(content_hash: str, skill_name: str = ADAPTER_SKILL) -> dict:
    return {
        "schema_version": MARKER_SCHEMA_VERSION,
        "owner": MARKER_OWNER,
        "kind": MARKER_KIND,
        "skill": skill_name,
        "content_sha256": content_hash,
    }


def _load_marker(path: Path, skill_name: str = ADAPTER_SKILL) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        raise AgentSkillConfigError(f"invalid Brain ownership marker at {path}: {exc}") from exc
    expected = {
        "schema_version": MARKER_SCHEMA_VERSION,
        "owner": MARKER_OWNER,
        "kind": MARKER_KIND,
        "skill": skill_name,
    }
    if not isinstance(data, dict) or any(data.get(key) != value for key, value in expected.items()):
        raise AgentSkillConfigError(f"unrecognised Brain ownership marker at {path}")
    digest = data.get("content_sha256")
    if not isinstance(digest, str) or len(digest) != 64:
        raise AgentSkillConfigError(f"invalid Brain ownership digest at {path}")
    return data


def _unexpected_entries(skill_dir: Path, *, marker_present: bool) -> list[str]:
    allowed = {*INCIDENTAL_ENTRIES, "SKILL.md"}
    if marker_present:
        allowed.add(MARKER_FILE)
    return sorted(entry.name for entry in skill_dir.iterdir() if entry.name not in allowed)


def _backup_path(skills_root: Path, skill_name: str = ADAPTER_SKILL) -> Path:
    backup_root = skills_root.parent / BACKUP_DIR
    base = backup_root / f"{skill_name}.pre-brain-adapter"
    candidate = base
    suffix = 2
    while candidate.exists() or candidate.is_symlink():
        candidate = backup_root / f"{base.name}-{suffix}"
        suffix += 1
    return candidate


def _write_adapter(
    skill_dir: Path,
    content: str,
    skill_name: str = ADAPTER_SKILL,
) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    digest = _sha256_text(content)
    safe_write(
        skill_dir / "SKILL.md",
        content,
        bounds=skill_dir,
        follow_symlinks=False,
    )
    safe_write_json(
        skill_dir / MARKER_FILE,
        _marker(digest, skill_name),
        bounds=skill_dir,
        follow_symlinks=False,
    )


def _archive_and_install(
    skills_root: Path,
    skill_dir: Path,
    content: str,
    *,
    existing_label: str,
    skill_name: str = ADAPTER_SKILL,
    dry_run: bool = False,
) -> tuple[str, str, Path | None]:
    backup = _backup_path(skills_root, skill_name)
    if dry_run:
        return (
            "planned",
            f"Would archive the existing {existing_label} at {backup} "
            f"and install the {skill_name} adapter.",
            backup,
        )
    backup.parent.mkdir(parents=True, exist_ok=True)
    os.replace(skill_dir, backup)
    _write_adapter(skill_dir, content, skill_name)
    return (
        "changed",
        f"Archived the existing {existing_label} at {backup} and installed the {skill_name} adapter.",
        backup,
    )


def _refuse_symlinked_destination(skills_root: Path, skill_dir: Path) -> None:
    client_root = skills_root.parent
    backup_root = client_root / BACKUP_DIR
    for path in (client_root, skills_root, skill_dir, backup_root):
        if path.is_symlink():
            raise AgentSkillConfigError(
                f"refusing to manage symlinked client skill destination: {path}"
            )


def install_prepared_skill_adapter(
    home_dir: Path,
    client: str,
    content: str,
    *,
    replace: bool,
    dry_run: bool = False,
    skill_name: str = ADAPTER_SKILL,
) -> tuple[str, str, Path | None]:
    """Install validated adapter content without resolving its Brain package."""
    if client not in SUPPORTED_CLIENTS:
        raise ValueError(f"unsupported agent-skill client '{client}'")
    skills_root = home_dir / CLIENT_SKILLS_DIRS[client]
    skill_dir = skills_root / skill_name
    skill_file = skill_dir / "SKILL.md"
    marker_path = skill_dir / MARKER_FILE
    desired_hash = _sha256_text(content)

    _refuse_symlinked_destination(skills_root, skill_dir)
    if skill_dir.exists() and not skill_dir.is_dir():
        if not replace:
            raise AgentSkillConfigError(
                f"{skill_dir} is not a Brain-managed skill directory; rerun with --replace to archive it"
            )
        if not dry_run:
            skills_root.mkdir(parents=True, exist_ok=True)
        return _archive_and_install(
            skills_root,
            skill_dir,
            content,
            existing_label="path",
            skill_name=skill_name,
            dry_run=dry_run,
        )

    if skill_dir.is_dir() and (skill_file.is_symlink() or marker_path.is_symlink()):
        raise AgentSkillConfigError(
            f"refusing to manage symlinked files inside skill directory: {skill_dir}"
        )

    marker = _load_marker(marker_path, skill_name) if skill_dir.is_dir() else None
    if marker is None:
        if not skill_dir.exists() or not any(skill_dir.iterdir()):
            if dry_run:
                return (
                    "planned",
                    f"Would install the active-Brain {skill_name} adapter at {skill_dir}.",
                    None,
                )
            _write_adapter(skill_dir, content, skill_name)
            return (
                "changed",
                f"Installed the active-Brain {skill_name} adapter at {skill_dir}.",
                None,
            )

        extras = _unexpected_entries(skill_dir, marker_present=False)
        current_hash = _sha256_file(skill_file) if skill_file.is_file() else None
        if not extras and current_hash is None:
            if dry_run:
                return (
                    "planned",
                    f"Would install the active-Brain {skill_name} adapter at {skill_dir}.",
                    None,
                )
            _write_adapter(skill_dir, content, skill_name)
            return (
                "changed",
                f"Installed the active-Brain {skill_name} adapter at {skill_dir}.",
                None,
            )
        if not extras and current_hash == desired_hash:
            if dry_run:
                return (
                    "planned",
                    f"Would adopt the existing {skill_name} adapter at {skill_dir}.",
                    None,
                )
            safe_write_json(
                marker_path,
                _marker(desired_hash, skill_name),
                bounds=skill_dir,
                follow_symlinks=False,
            )
            return (
                "changed",
                f"Adopted the existing {skill_name} adapter at {skill_dir}.",
                None,
            )
        if not replace:
            raise AgentSkillConfigError(
                f"unmanaged skill already exists at {skill_dir}; rerun with --replace to archive it"
            )
        return _archive_and_install(
            skills_root,
            skill_dir,
            content,
            existing_label="skill",
            skill_name=skill_name,
            dry_run=dry_run,
        )

    extras = _unexpected_entries(skill_dir, marker_present=True)
    if extras:
        raise AgentSkillConfigError(
            f"Brain-managed skill directory contains unowned entries at {skill_dir}: {', '.join(extras)}"
        )
    if not skill_file.is_file():
        if dry_run:
            return (
                "planned",
                f"Would restore the missing {skill_name} adapter at {skill_dir}.",
                None,
            )
        _write_adapter(skill_dir, content, skill_name)
        return (
            "changed",
            f"Restored the missing {skill_name} adapter at {skill_dir}.",
            None,
        )

    current_hash = _sha256_file(skill_file)
    recorded_hash = marker["content_sha256"]
    if current_hash == desired_hash:
        if recorded_hash != desired_hash:
            if dry_run:
                return (
                    "planned",
                    f"Would repair {skill_name} adapter ownership at {skill_dir}.",
                    None,
                )
            safe_write_json(
                marker_path,
                _marker(desired_hash, skill_name),
                bounds=skill_dir,
                follow_symlinks=False,
            )
            return (
                "changed",
                f"Repaired {skill_name} adapter ownership at {skill_dir}.",
                None,
            )
        return (
            "noop",
            f"The active-Brain {skill_name} adapter is current at {skill_dir}.",
            None,
        )
    if current_hash != recorded_hash:
        raise AgentSkillConfigError(
            f"Brain-managed {skill_name} adapter was modified at {skill_file}; preserve or remove those edits before retrying"
        )

    if dry_run:
        return (
            "planned",
            f"Would update the active-Brain {skill_name} adapter at {skill_dir}.",
            None,
        )
    _write_adapter(skill_dir, content, skill_name)
    return (
        "changed",
        f"Updated the active-Brain {skill_name} adapter at {skill_dir}.",
        None,
    )


def remove_prepared_skill_adapter(
    home_dir: Path,
    client: str,
    current_content: str | None = None,
    *,
    dry_run: bool = False,
    skill_name: str = ADAPTER_SKILL,
) -> tuple[str, str, Path | None]:
    """Remove unchanged managed adapter content already resolved by the caller."""
    if client not in SUPPORTED_CLIENTS:
        raise ValueError(f"unsupported agent-skill client '{client}'")
    skills_root = home_dir / CLIENT_SKILLS_DIRS[client]
    skill_dir = skills_root / skill_name
    skill_file = skill_dir / "SKILL.md"
    marker_path = skill_dir / MARKER_FILE
    _refuse_symlinked_destination(skills_root, skill_dir)
    if not skill_dir.exists():
        return (
            "noop",
            f"No Brain-managed {skill_name} adapter is installed for {client}.",
            None,
        )
    if not skill_dir.is_dir():
        raise AgentSkillConfigError(f"unmanaged skill path exists at {skill_dir}")
    if skill_file.is_symlink() or marker_path.is_symlink():
        raise AgentSkillConfigError(
            f"refusing to remove symlinked files inside skill directory: {skill_dir}"
        )

    marker = _load_marker(marker_path, skill_name)
    if marker is None:
        raise AgentSkillConfigError(f"unmanaged skill exists at {skill_dir}; it was not removed")
    extras = _unexpected_entries(skill_dir, marker_present=True)
    if extras:
        raise AgentSkillConfigError(
            f"Brain-managed skill directory contains unowned entries at {skill_dir}: {', '.join(extras)}"
        )
    if skill_file.is_file():
        current_hash = _sha256_file(skill_file)
        allowed_hashes = {marker["content_sha256"]}
        if current_content is not None:
            allowed_hashes.add(_sha256_text(current_content))
        if current_hash not in allowed_hashes:
            raise AgentSkillConfigError(
                f"Brain-managed {skill_name} adapter was modified at {skill_file}; it was not removed"
            )
    if dry_run:
        return (
            "planned",
            f"Would remove the Brain-managed {skill_name} adapter from {skill_dir}.",
            None,
        )
    for name in INCIDENTAL_ENTRIES:
        incidental = skill_dir / name
        if incidental.exists() or incidental.is_symlink():
            incidental.unlink()
    if skill_file.is_file():
        skill_file.unlink()
    marker_path.unlink()
    skill_dir.rmdir()
    return (
        "changed",
        f"Removed the Brain-managed {skill_name} adapter from {skill_dir}.",
        None,
    )


def configure_agent_skill_adapters(
    *,
    home_dir: str | Path,
    client: str,
    replace: bool = False,
    remove: bool = False,
    dry_run: bool = False,
) -> list[dict]:
    """Configure shaping adapters for one or all supported agent clients."""
    if client not in {*SUPPORTED_CLIENTS, "all"}:
        raise ValueError(f"unsupported agent-skill client '{client}'")
    if replace and remove:
        raise ValueError("--replace cannot be combined with --remove")

    clients = SUPPORTED_CLIENTS if client == "all" else (client,)
    adapter_content = None
    if remove:
        try:
            adapter_content = load_shaping_adapter()
        except OSError:
            pass
    else:
        try:
            adapter_content = load_shaping_adapter()
        except OSError as exc:
            return [
                {
                    "name": f"agent_skill_{selected}_{ADAPTER_SKILL}",
                    "status": "error",
                    "message": f"cannot read shaping adapter template: {exc}",
                }
                for selected in clients
            ]
    steps = []
    for selected in clients:
        try:
            if remove:
                status, message, _ = remove_prepared_skill_adapter(
                    Path(home_dir),
                    selected,
                    adapter_content,
                    dry_run=dry_run,
                )
            else:
                assert adapter_content is not None
                status, message, _ = install_prepared_skill_adapter(
                    Path(home_dir),
                    selected,
                    adapter_content,
                    replace=replace,
                    dry_run=dry_run,
                )
        except (AgentSkillConfigError, OSError, ValueError) as exc:
            status, message = "error", str(exc)
        steps.append(
            {
                "name": f"agent_skill_{selected}_{ADAPTER_SKILL}",
                "status": status,
                "message": message,
            }
        )
    return steps
