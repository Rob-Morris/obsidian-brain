"""Ownership-safe client adapters for skills supplied by the active Brain."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from _bootstrap.mcp_transport import SUPPORTED_CLIENTS
from _common import safe_write, safe_write_json


CLIENT_SKILLS_DIRS = {
    "claude": Path(".claude") / "skills",
    "codex": Path(".codex") / "skills",
}
ADAPTER_SKILL = "shaping"
MARKER_FILE = ".brain-agent-skill.json"
MARKER_OWNER = "obsidian-brain"
MARKER_KIND = "active-brain-skill-adapter"
MARKER_SCHEMA_VERSION = 1
INCIDENTAL_ENTRIES = {".DS_Store", "Thumbs.db"}
ADAPTER_TEMPLATE_REL = Path("client-adapters") / ADAPTER_SKILL / "SKILL.md"
ADAPTER_TEMPLATE_FILE = Path(__file__).resolve().parents[2] / ADAPTER_TEMPLATE_REL


class AgentSkillConfigError(RuntimeError):
    """A client skill cannot be changed without risking user-owned content."""


def load_shaping_adapter() -> str:
    """Read the shipped adapter only when the agent-skills surface needs it."""
    return ADAPTER_TEMPLATE_FILE.read_text(encoding="utf-8")


def _sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _marker(content_hash: str) -> dict:
    return {
        "schema_version": MARKER_SCHEMA_VERSION,
        "owner": MARKER_OWNER,
        "kind": MARKER_KIND,
        "skill": ADAPTER_SKILL,
        "content_sha256": content_hash,
    }


def _load_marker(path: Path) -> dict | None:
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
        "skill": ADAPTER_SKILL,
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


def _backup_path(skills_root: Path) -> Path:
    base = skills_root / f"{ADAPTER_SKILL}.pre-brain-adapter"
    candidate = base
    suffix = 2
    while candidate.exists() or candidate.is_symlink():
        candidate = skills_root / f"{base.name}-{suffix}"
        suffix += 1
    return candidate


def _write_adapter(skill_dir: Path, content: str) -> None:
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
        _marker(digest),
        bounds=skill_dir,
        follow_symlinks=False,
    )


def _archive_and_install(
    skills_root: Path,
    skill_dir: Path,
    content: str,
    *,
    existing_label: str,
) -> tuple[str, str]:
    backup = _backup_path(skills_root)
    os.replace(skill_dir, backup)
    _write_adapter(skill_dir, content)
    return (
        "changed",
        f"Archived the existing {existing_label} at {backup} and installed the shaping adapter.",
    )


def _refuse_symlinked_destination(skills_root: Path, skill_dir: Path) -> None:
    client_root = skills_root.parent
    for path in (client_root, skills_root, skill_dir):
        if path.is_symlink():
            raise AgentSkillConfigError(
                f"refusing to manage symlinked client skill destination: {path}"
            )


def _install_client_adapter(
    home_dir: Path,
    client: str,
    content: str,
    *,
    replace: bool,
) -> tuple[str, str]:
    skills_root = home_dir / CLIENT_SKILLS_DIRS[client]
    skill_dir = skills_root / ADAPTER_SKILL
    skill_file = skill_dir / "SKILL.md"
    marker_path = skill_dir / MARKER_FILE
    desired_hash = _sha256_text(content)

    _refuse_symlinked_destination(skills_root, skill_dir)
    if skill_dir.exists() and not skill_dir.is_dir():
        if not replace:
            raise AgentSkillConfigError(
                f"{skill_dir} is not a Brain-managed skill directory; rerun with --replace to archive it"
            )
        skills_root.mkdir(parents=True, exist_ok=True)
        return _archive_and_install(
            skills_root, skill_dir, content, existing_label="path"
        )

    if skill_dir.is_dir() and (skill_file.is_symlink() or marker_path.is_symlink()):
        raise AgentSkillConfigError(
            f"refusing to manage symlinked files inside skill directory: {skill_dir}"
        )

    marker = _load_marker(marker_path) if skill_dir.is_dir() else None
    if marker is None:
        if not skill_dir.exists() or not any(skill_dir.iterdir()):
            _write_adapter(skill_dir, content)
            return "changed", f"Installed the active-Brain shaping adapter at {skill_dir}."

        extras = _unexpected_entries(skill_dir, marker_present=False)
        current_hash = _sha256_file(skill_file) if skill_file.is_file() else None
        if not extras and current_hash is None:
            _write_adapter(skill_dir, content)
            return "changed", f"Installed the active-Brain shaping adapter at {skill_dir}."
        if not extras and current_hash == desired_hash:
            safe_write_json(
                marker_path,
                _marker(desired_hash),
                bounds=skill_dir,
                follow_symlinks=False,
            )
            return "changed", f"Adopted the existing shaping adapter at {skill_dir}."
        if not replace:
            raise AgentSkillConfigError(
                f"unmanaged skill already exists at {skill_dir}; rerun with --replace to archive it"
            )
        return _archive_and_install(
            skills_root, skill_dir, content, existing_label="skill"
        )

    extras = _unexpected_entries(skill_dir, marker_present=True)
    if extras:
        raise AgentSkillConfigError(
            f"Brain-managed skill directory contains unowned entries at {skill_dir}: {', '.join(extras)}"
        )
    if not skill_file.is_file():
        _write_adapter(skill_dir, content)
        return "changed", f"Restored the missing shaping adapter at {skill_dir}."

    current_hash = _sha256_file(skill_file)
    recorded_hash = marker["content_sha256"]
    if current_hash == desired_hash:
        if recorded_hash != desired_hash:
            safe_write_json(
                marker_path,
                _marker(desired_hash),
                bounds=skill_dir,
                follow_symlinks=False,
            )
            return "changed", f"Repaired shaping adapter ownership at {skill_dir}."
        return "noop", f"The active-Brain shaping adapter is current at {skill_dir}."
    if current_hash != recorded_hash:
        raise AgentSkillConfigError(
            f"Brain-managed shaping adapter was modified at {skill_file}; preserve or remove those edits before retrying"
        )

    _write_adapter(skill_dir, content)
    return "changed", f"Updated the active-Brain shaping adapter at {skill_dir}."


def _remove_client_adapter(
    home_dir: Path,
    client: str,
    current_content: str | None = None,
) -> tuple[str, str]:
    skills_root = home_dir / CLIENT_SKILLS_DIRS[client]
    skill_dir = skills_root / ADAPTER_SKILL
    skill_file = skill_dir / "SKILL.md"
    marker_path = skill_dir / MARKER_FILE
    _refuse_symlinked_destination(skills_root, skill_dir)
    if not skill_dir.exists():
        return "noop", f"No Brain-managed shaping adapter is installed for {client}."
    if not skill_dir.is_dir():
        raise AgentSkillConfigError(f"unmanaged skill path exists at {skill_dir}")
    if skill_file.is_symlink() or marker_path.is_symlink():
        raise AgentSkillConfigError(
            f"refusing to remove symlinked files inside skill directory: {skill_dir}"
        )

    marker = _load_marker(marker_path)
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
                f"Brain-managed shaping adapter was modified at {skill_file}; it was not removed"
            )
    for name in INCIDENTAL_ENTRIES:
        incidental = skill_dir / name
        if incidental.exists() or incidental.is_symlink():
            incidental.unlink()
    if skill_file.is_file():
        skill_file.unlink()
    marker_path.unlink()
    skill_dir.rmdir()
    return "changed", f"Removed the Brain-managed shaping adapter from {skill_dir}."


def configure_agent_skill_adapters(
    *,
    home_dir: str | Path,
    client: str,
    replace: bool = False,
    remove: bool = False,
) -> list[dict]:
    """Configure shaping adapters for one or both supported agent clients."""
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
                status, message = _remove_client_adapter(
                    Path(home_dir), selected, adapter_content
                )
            else:
                assert adapter_content is not None
                status, message = _install_client_adapter(
                    Path(home_dir), selected, adapter_content, replace=replace
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
