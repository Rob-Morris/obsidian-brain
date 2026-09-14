"""Resolve skill inputs once and bind package, tracking and replacement scope."""

from __future__ import annotations

from pathlib import Path
import stat

from ..preparation import ObservedResource, bind_operation, canonical_json, content_digest, admit_owner


def _tree_revision(path: Path, *, order_matters=False):
    if path.is_symlink():
        raise ValueError(f"skill preparation refuses a symbolic-link target: {path}")
    if not path.exists():
        return None
    if not path.is_dir():
        raise ValueError(f"skill tree target is not a directory: {path}")
    entries = []
    for item in sorted(path.rglob("*")):
        if item.is_symlink():
            raise ValueError(f"skill preparation refuses a symbolic-link entry: {item}")
        mode = item.stat().st_mode
        if stat.S_ISREG(mode):
            entries.append([item.relative_to(path).as_posix(), content_digest(item.read_bytes()), bool(mode & 0o111)])
        elif not stat.S_ISDIR(mode):
            raise ValueError(f"skill preparation found an unsupported file: {item}")
    return content_digest(canonical_json({"files": entries, "order": path.stat().st_mtime_ns if order_matters else None}))


def _source_value(source):
    return {"repository": source.repository, "skill_path": source.skill_path,
            "resolved_commit": source.resolved_commit, "name": source.package.name,
            "package_sha256": source.package.package_sha256,
            "file_count": len(source.package.files),
            "executable_file_count": len(source.package.executable_files)}


def prepare_skill(context, request, *, frozen_inputs=None, source=None):
    """Pin imports to an immutable commit and include indirect conflict/backup effects."""
    from _skill_library import service
    from _skill_library.packages import inspect_package, validate_skill_name
    from _skill_library.tracking import load_core_manifest, load_tracking

    root = context.selected_brain.vault_root
    tracking = load_tracking(root)
    core = load_core_manifest(root)
    command = request.COMMAND_ID
    frozen = dict(frozen_inputs or {})
    if command in {"skill.add-git", "skill.update"}:
        if source is not None:
            frozen["source"] = _source_value(source)
        elif "source" not in frozen:
            if command == "skill.add-git":
                repository, path, ref, expected = request.repository, request.skill_path, request.configured_ref, None
            else:
                expected = validate_skill_name(request.name)
                record = tracking["managed"].get(expected) or core.get(expected)
                if not record:
                    raise ValueError(f"skill {expected!r} has no configured update source")
                repository, path = record["repository"], record["skill_path"]
                ref = request.to_commit or record["configured_ref"]
            with service.checkout_source(repository, skill_path=path, configured_ref=ref, expected_name=expected) as acquired:
                frozen["source"] = _source_value(acquired)
        names = {validate_skill_name(frozen["source"]["name"])}
    elif request.name is not None:
        names = {validate_skill_name(request.name)}
    else:
        names = service._skill_names(root, tracking, core)
    observations = []
    for name in sorted(names):
        for substrate, relative in (("user", service.USER_SKILLS_REL), ("core", service.CORE_SKILLS_REL)):
            path = root / relative / name
            revision = None
            if path.exists() or path.is_symlink():
                revision = inspect_package(path, expected_name=name).package_sha256
            observations.append(ObservedResource("skill-package", f"{substrate}:{name}", revision))
        record = {"managed": tracking["managed"].get(name), "core_override": tracking["core_overrides"].get(name),
                  "core_source": core.get(name), "core_check": tracking["core_checks"].get(name)}
        observations.append(ObservedResource("skill-tracking", name, content_digest(canonical_json(record))))
        if command == "skill.update":
            conflict = root / service.CONFLICTS_REL / name
            observations.append(ObservedResource("skill-conflict", str(conflict), _tree_revision(conflict)))
            backup_root = root / service.BACKUPS_REL
            if backup_root.is_symlink():
                raise ValueError("skill backup root is a symbolic link")
            backups = sorted(backup_root.glob(f"{name}.*")) if backup_root.exists() else []
            observations.append(ObservedResource("skill-backup-set", name, content_digest(canonical_json([item.name for item in backups]))))
            for backup in backups:
                observations.append(ObservedResource("skill-backup", str(backup), _tree_revision(backup, order_matters=True)))
    return bind_operation(
        request, observations=tuple(observations), frozen_inputs=frozen,
        review={"action": command, "skills": sorted(names), "source": frozen.get("source"),
                "replace_conflict": getattr(request, "replace_conflict", False),
                "scope": "Skill packages, their source tracking, and applicable conflict stages and retained backups."},
    )


def skill_execution_options(context, request):
    """Forward pinned acquisition and admission into the existing locked service."""
    def before_write(source):
        admit_owner(context, request, prepare_skill, source=source)
    options = {"before_write": before_write}
    frozen = context.admission.frozen_inputs or {}
    if request.COMMAND_ID in {"skill.add-git", "skill.update"} and "source" in frozen:
        options["resolved_commit"] = frozen["source"]["resolved_commit"]
    return options
