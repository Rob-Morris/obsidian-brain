"""Single lifecycle service for Brain skill sources and user packages."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timezone
from enum import Enum
import os
from pathlib import Path
import shutil
import uuid

from _common import vault_mutation_lock
from _common import safe_write_json

from .git_source import GitSourceError, checkout_repository, checkout_source
from .models import (
    CoreReconciliationAction,
    CoreReconciliation,
    PackageSnapshot,
    SkillMutation,
    SkillMutationAction,
    SkillState,
    SkillStatus,
    SkillSubstrate,
)
from .packages import copy_package, inspect_package, manifest_value, validate_skill_name
from .tracking import load_core_manifest, load_tracking, write_tracking
from _portable.skill_resolution import SKILL_SUBSTRATE_ORDER


USER_SKILLS_REL = Path("_Config") / "Skills"
CORE_SKILLS_REL = Path(".brain-core") / "skills"
BACKUPS_REL = Path(".brain") / "skill-backups"
CONFLICTS_REL = Path(".brain") / "skill-conflicts"
MAX_BACKUPS_PER_SKILL = 3
_MAX_SOURCE_REFRESH_WORKERS = 4


class SkillLibraryError(RuntimeError):
    """A requested skill lifecycle operation cannot proceed safely."""


class SkillMutationOutcomeUncertain(RuntimeError):
    """A failed recovery could not prove whether a skill mutation committed."""


class _SkillTransition(str, Enum):
    MATCHES_SOURCE = "matches_source"
    CONFLICT = "conflict"
    LOCAL_ONLY = "local_only"
    SOURCE_ONLY = "source_only"
    INCONSISTENT_BASELINES = "inconsistent_baselines"


def _classify_transition(
    *,
    current: str,
    installed_baseline: str,
    source_baseline: str,
    available_source: str,
) -> _SkillTransition:
    """Classify one installed/baseline/available package relationship."""

    if current == available_source:
        return _SkillTransition.MATCHES_SOURCE
    local_changed = current != installed_baseline
    source_changed = available_source != source_baseline
    if local_changed and source_changed:
        return _SkillTransition.CONFLICT
    if local_changed:
        return _SkillTransition.LOCAL_ONLY
    if source_changed:
        return _SkillTransition.SOURCE_ONLY
    return _SkillTransition.INCONSISTENT_BASELINES


def list_skill_status(
    vault_root: str | Path,
    *,
    name: str | None = None,
    refresh: bool = False,
) -> tuple[SkillStatus, ...]:
    """Classify core and user entries, optionally refreshing remote state."""
    root = Path(vault_root)
    tracking = load_tracking(root)
    core_sources = load_core_manifest(root)
    names = _skill_names(root, tracking, core_sources)
    if name is not None:
        reference = validate_skill_name(name)
        if reference not in names:
            raise SkillLibraryError(f"skill not found: {reference}")
        names = {reference}
    if refresh:
        original_tracking = deepcopy(tracking)
        refreshed_tracking = _refresh_sources(
            root, names, tracking, core_sources
        )
        with vault_mutation_lock(root):
            if load_tracking(root) != original_tracking:
                raise SkillLibraryError(
                    "skill source state changed during refresh; retry status"
                )
            write_tracking(root, refreshed_tracking)
        tracking = refreshed_tracking
    rows: list[SkillStatus] = []
    managed = tracking["managed"]
    core_overrides = tracking["core_overrides"]
    core_checks = tracking["core_checks"]
    for skill_name in sorted(names):
        snapshots = {
            "user": _optional_snapshot(
                root / USER_SKILLS_REL / skill_name, skill_name
            ),
            "core": _optional_snapshot(
                root / CORE_SKILLS_REL / skill_name, skill_name
            ),
        }
        for substrate in SKILL_SUBSTRATE_ORDER:
            snapshot = snapshots[substrate]
            if snapshot is None:
                continue
            if substrate == "user":
                rows.append(
                    _user_status(
                        skill_name,
                        snapshot,
                        managed.get(skill_name),
                        snapshots["core"] is not None,
                        core_override=core_overrides.get(skill_name),
                    )
                )
            else:
                rows.append(
                    _core_status(
                        skill_name,
                        snapshot,
                        core_sources.get(skill_name),
                        core_checks.get(skill_name),
                        shadowed=snapshots["user"] is not None,
                    )
                )
    return tuple(rows)


def add_git_skill(
    vault_root: str | Path,
    *,
    repository: str,
    skill_path: str,
    configured_ref: str = "HEAD",
) -> SkillMutation:
    """Install a new Git-managed package into the user substrate."""
    root = Path(vault_root)
    try:
        with checkout_source(
            repository,
            skill_path=skill_path,
            configured_ref=configured_ref,
        ) as source:
            return _install_new_managed(root, source, core_lineage=None)
    except (GitSourceError, OSError, ValueError) as exc:
        raise SkillLibraryError(str(exc)) from exc


def update_skill(
    vault_root: str | Path,
    *,
    name: str,
    to_commit: str | None = None,
    replace_conflict: bool = False,
) -> SkillMutation:
    """Update the user entry, materialising a core-only source when needed."""
    root = Path(vault_root)
    skill_name = validate_skill_name(name)
    tracking = load_tracking(root)
    user_path = root / USER_SKILLS_REL / skill_name
    if user_path.exists() or user_path.is_symlink():
        record = tracking["managed"].get(skill_name)
        if not isinstance(record, dict):
            raise SkillLibraryError(
                f"user skill {skill_name!r} has no configured update source"
            )
        return _update_existing(root, skill_name, record, to_commit, replace_conflict)

    core_path = root / CORE_SKILLS_REL / skill_name
    if not core_path.is_dir() or core_path.is_symlink():
        raise SkillLibraryError(f"skill not found: {skill_name}")
    descriptor = load_core_manifest(root).get(skill_name)
    if descriptor is None:
        raise SkillLibraryError(
            f"core skill {skill_name!r} has no configured external update source"
        )
    ref = to_commit or str(descriptor["configured_ref"])
    try:
        with checkout_source(
            str(descriptor["repository"]),
            skill_path=str(descriptor["skill_path"]),
            configured_ref=ref,
            expected_name=skill_name,
        ) as source:
            return _install_new_managed(root, source, core_lineage=skill_name)
    except (GitSourceError, OSError, ValueError) as exc:
        raise SkillLibraryError(str(exc)) from exc


def detach_skill(vault_root: str | Path, *, name: str) -> SkillMutation:
    """Retain a user package while removing its external update ownership."""
    root = Path(vault_root)
    skill_name = validate_skill_name(name)
    with vault_mutation_lock(root):
        tracking = load_tracking(root)
        record = tracking["managed"].get(skill_name)
        if not isinstance(record, dict):
            raise SkillLibraryError(f"managed user skill not found: {skill_name}")
        package = _require_user_snapshot(root, skill_name)
        updated = deepcopy(tracking)
        del updated["managed"][skill_name]
        write_tracking(root, updated)
    return SkillMutation(
        skill_name,
        SkillMutationAction.DETACHED,
        SkillState.USER_OWNED,
        package.package_sha256,
        None,
        (str(Path(".brain") / "skill-sources.json"),),
    )


def materialise_core_skill_for_edit(
    vault_root: str | Path, *, name: str, lock_held: bool = False
) -> SkillMutation | None:
    """Copy a core-only package into user space before a supported edit."""

    root = Path(vault_root)
    skill_name = validate_skill_name(name)
    user_path = root / USER_SKILLS_REL / skill_name
    if user_path.exists() or user_path.is_symlink():
        return None
    core = _optional_snapshot(root / CORE_SKILLS_REL / skill_name, skill_name)
    if core is None:
        return None
    descriptor = load_core_manifest(root).get(skill_name)
    lock = nullcontext() if lock_held else vault_mutation_lock(root)
    with lock:
        if user_path.exists() or user_path.is_symlink():
            return None
        _install_snapshot(root, core, replace=False, archive_existing=False)
        changed = [str(USER_SKILLS_REL / skill_name)]
        if descriptor is not None:
            tracking = load_tracking(root)
            record = _record(
                source_repository=str(descriptor["repository"]),
                source_skill_path=str(descriptor["skill_path"]),
                configured_ref=str(descriptor["configured_ref"]),
                resolved_commit=str(descriptor.get("resolved_commit") or "unknown"),
                source=core,
                core_lineage=skill_name,
            )
            tracking["managed"][skill_name] = record
            try:
                write_tracking(root, tracking)
            except BaseException:
                _rollback_install(root, user_path, None, False)
                raise
            changed.append(str(Path(".brain") / "skill-sources.json"))
        else:
            tracking = load_tracking(root)
            tracking["core_overrides"][skill_name] = {
                "core_lineage": skill_name,
                "installed_baseline_sha256": core.package_sha256,
                "installed_manifest": manifest_value(core),
                "materialised_at": _now(),
            }
            try:
                write_tracking(root, tracking)
            except BaseException:
                _rollback_install(root, user_path, None, False)
                raise
            changed.append(str(Path(".brain") / "skill-sources.json"))
    return SkillMutation(
        skill_name,
        SkillMutationAction.MATERIALISED_FOR_EDIT,
        SkillState.LOCALLY_CUSTOMISED,
        core.package_sha256,
        str(descriptor.get("resolved_commit")) if descriptor else None,
        tuple(changed),
    )


def reconcile_core_overrides(
    vault_root: str | Path,
) -> tuple[CoreReconciliation, ...]:
    """Archive clean redundant user overrides that now equal immutable core."""
    root = Path(vault_root)
    reconciled: list[CoreReconciliation] = []
    with vault_mutation_lock(root):
        tracking = load_tracking(root)
        updated = deepcopy(tracking)
        for name, _record, collection, _user, _core in _eligible_core_overrides(
            root,
            root / CORE_SKILLS_REL,
            tracking,
        ):
            archive = _archive_destination(root, name, "converged")
            archive.parent.mkdir(parents=True, exist_ok=True)
            os.replace(root / USER_SKILLS_REL / name, archive)
            del updated[collection][name]
            try:
                write_tracking(root, updated)
            except BaseException:
                try:
                    os.replace(archive, root / USER_SKILLS_REL / name)
                except BaseException as rollback_exc:
                    raise SkillMutationOutcomeUncertain(
                        f"could not restore collapsed override for {name!r}"
                    ) from rollback_exc
                raise
            cleanup_warning = _trim_backups(root, name)
            reconciled.append(
                CoreReconciliation(
                    name,
                    CoreReconciliationAction.COLLAPSED_TO_CORE,
                    str(archive.relative_to(root)),
                    cleanup_warning,
                )
            )
    return tuple(reconciled)


def preview_core_override_reconciliation(
    vault_root: str | Path,
    *,
    core_root: str | Path | None = None,
) -> tuple[str, ...]:
    """Return clean tracked overrides that would collapse to a core tree."""
    root = Path(vault_root)
    core_base = (
        Path(core_root) / "skills"
        if core_root is not None
        else root / CORE_SKILLS_REL
    )
    tracking = load_tracking(root)
    names = [
        name
        for name, _record, _collection, _user, _core in _eligible_core_overrides(
            root,
            core_base,
            tracking,
        )
    ]
    return tuple(sorted(names))


def _eligible_core_overrides(root, core_base, tracking):
    candidates = [
        (name, record, "managed")
        for name, record in tracking["managed"].items()
    ] + [
        (name, record, "core_overrides")
        for name, record in tracking["core_overrides"].items()
    ]
    eligible = []
    for name, record, collection in candidates:
        if not isinstance(record, dict) or record.get("core_lineage") != name:
            continue
        user = _optional_snapshot(root / USER_SKILLS_REL / name, name)
        core = _optional_snapshot(core_base / name, name)
        if user is None or core is None:
            continue
        if user.package_sha256 != record.get("installed_baseline_sha256"):
            continue
        if user.package_sha256 == core.package_sha256:
            eligible.append((name, record, collection, user, core))
    return tuple(eligible)


def _refresh_sources(root, names, tracking, core_sources):
    updated = deepcopy(tracking)
    checked_at = _now()
    source_groups = {}
    for name in sorted(names):
        record = updated["managed"].get(name)
        descriptor = record if isinstance(record, dict) else core_sources.get(name)
        if descriptor is None:
            continue
        source_key = (
            str(descriptor["repository"]),
            str(descriptor["configured_ref"]),
        )
        source_groups.setdefault(source_key, []).append((name, record, descriptor))

    ordered_groups = tuple(sorted(source_groups.items()))
    if not ordered_groups:
        return updated
    if len(ordered_groups) == 1:
        key, entries = ordered_groups[0]
        group_results = ((key, _refresh_source_group(key, entries, checked_at)),)
    else:
        worker_count = min(_MAX_SOURCE_REFRESH_WORKERS, len(ordered_groups))
        with ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="brain-skill-refresh",
        ) as executor:
            futures = {
                key: executor.submit(_refresh_source_group, key, entries, checked_at)
                for key, entries in ordered_groups
            }
            group_results = tuple(
                (key, futures[key].result()) for key, _entries in ordered_groups
            )

    for _key, results in group_results:
        for name, check in results:
            _record_source_check(
                updated,
                name,
                updated["managed"].get(name),
                check,
            )
    return updated


def _refresh_source_group(source_key, entries, checked_at):
    """Inspect one repository/ref checkout without sharing mutable result state."""

    repository, configured_ref = source_key
    results = []
    try:
        with checkout_repository(
            repository,
            configured_ref=configured_ref,
        ) as repository_checkout:
            for name, _record, descriptor in entries:
                try:
                    with repository_checkout.checkout_source(
                        skill_path=str(descriptor["skill_path"]),
                        expected_name=name,
                    ) as source:
                        check = _source_check(
                            checked_at=checked_at,
                            resolved_commit=source.resolved_commit,
                            package_sha256=source.package.package_sha256,
                        )
                except (GitSourceError, OSError, ValueError) as exc:
                    check = _source_check(checked_at=checked_at, error=str(exc))
                results.append((name, check))
    except (GitSourceError, OSError, ValueError) as exc:
        for name, _record, _descriptor in entries:
            results.append(
                (name, _source_check(checked_at=checked_at, error=str(exc)))
            )
    return tuple(results)


def _source_check(
    *,
    checked_at,
    resolved_commit=None,
    package_sha256=None,
    error=None,
):
    return {
        "resolved_commit": resolved_commit,
        "package_sha256": package_sha256,
        "checked_at": checked_at,
        "error": error,
    }


def _record_source_check(updated, name, record, check):
    if isinstance(record, dict):
        record["available_commit"] = check["resolved_commit"]
        record["available_package_sha256"] = check["package_sha256"]
        record["last_checked_at"] = check["checked_at"]
        record["source_error"] = check["error"]
    else:
        updated["core_checks"][name] = check


def _install_new_managed(root, source, *, core_lineage):
    name = source.package.name
    with vault_mutation_lock(root):
        destination = root / USER_SKILLS_REL / name
        if destination.exists() or destination.is_symlink():
            raise SkillLibraryError(f"user skill already exists: {name}")
        tracking = load_tracking(root)
        if name in tracking["managed"]:
            raise SkillLibraryError(f"managed skill tracking already exists: {name}")
        record = _record(
            source_repository=source.repository,
            source_skill_path=source.skill_path,
            configured_ref=source.configured_ref,
            resolved_commit=source.resolved_commit,
            source=source.package,
            core_lineage=core_lineage,
        )
        changed, backup, cleanup_warning = _commit_package_and_tracking(
            root,
            source.package,
            tracking,
            record,
            replace=False,
            archive_existing=False,
        )
    return SkillMutation(
        name,
        SkillMutationAction.INSTALLED,
        SkillState.IN_SYNC,
        source.package.package_sha256,
        source.resolved_commit,
        changed,
        backup,
        cleanup_warning,
    )


def _update_existing(root, name, record, to_commit, replace_conflict):
    ref = to_commit or str(record["configured_ref"])
    try:
        with checkout_source(
            str(record["repository"]),
            skill_path=str(record["skill_path"]),
            configured_ref=ref,
            expected_name=name,
        ) as source:
            with vault_mutation_lock(root):
                tracking = load_tracking(root)
                current_record = tracking["managed"].get(name)
                if not isinstance(current_record, dict):
                    raise SkillLibraryError(f"managed skill tracking disappeared: {name}")
                current = _require_user_snapshot(root, name)
                baseline = str(current_record["installed_baseline_sha256"])
                source_baseline = str(current_record["source_package_sha256"])
                transition = _classify_transition(
                    current=current.package_sha256,
                    installed_baseline=baseline,
                    source_baseline=source_baseline,
                    available_source=source.package.package_sha256,
                )
                if transition is _SkillTransition.MATCHES_SOURCE:
                    updated_record = _record_from_existing(current_record, source)
                    tracking["managed"][name] = updated_record
                    write_tracking(root, tracking)
                    return SkillMutation(
                        name,
                        SkillMutationAction.REBASELINED,
                        SkillState.IN_SYNC,
                        current.package_sha256,
                        source.resolved_commit,
                        (str(Path(".brain") / "skill-sources.json"),),
                    )
                if transition is _SkillTransition.CONFLICT and not replace_conflict:
                    current_record["available_commit"] = source.resolved_commit
                    current_record["available_package_sha256"] = (
                        source.package.package_sha256
                    )
                    current_record["last_checked_at"] = _now()
                    current_record["source_error"] = None
                    tracking["managed"][name] = current_record
                    conflict_path, previous_conflict = _stage_conflict(
                        root,
                        current,
                        current_record,
                        source.package,
                        source.resolved_commit,
                    )
                    try:
                        write_tracking(root, tracking)
                    except BaseException as exc:
                        try:
                            _rollback_conflict_stage(
                                conflict_path, previous_conflict
                            )
                        except BaseException as rollback_exc:
                            raise SkillMutationOutcomeUncertain(
                                f"could not recover failed conflict staging for {name!r}"
                            ) from rollback_exc
                        raise
                    cleanup_warning = _cleanup_displaced_conflict(previous_conflict)
                    detail = (
                        "Local and upstream packages both changed. The upstream "
                        "candidate and comparison metadata were staged without "
                        "changing the installed user skill."
                    )
                    if cleanup_warning is not None:
                        detail = f"{detail} {cleanup_warning}"
                    return SkillMutation(
                        name,
                        SkillMutationAction.CONFLICT_STAGED,
                        SkillState.CONFLICT,
                        current.package_sha256,
                        source.resolved_commit,
                        (
                            str(conflict_path.relative_to(root)),
                            str(Path(".brain") / "skill-sources.json"),
                        ),
                        None,
                        detail,
                    )
                if transition is _SkillTransition.LOCAL_ONLY:
                    raise SkillLibraryError(
                        f"skill {name!r} is locally customised and upstream is unchanged"
                    )
                if transition is _SkillTransition.INCONSISTENT_BASELINES:
                    raise SkillLibraryError(
                        f"skill {name!r} tracking baselines disagree with the installed package"
                    )
                updated_record = _record_from_existing(current_record, source)
                archive_existing = transition is _SkillTransition.CONFLICT
                changed, backup, cleanup_warning = _commit_package_and_tracking(
                    root,
                    source.package,
                    tracking,
                    updated_record,
                    replace=True,
                    archive_existing=archive_existing,
                )
            return SkillMutation(
                name,
                (
                    SkillMutationAction.REPLACED
                    if archive_existing
                    else SkillMutationAction.UPDATED
                ),
                SkillState.IN_SYNC,
                source.package.package_sha256,
                source.resolved_commit,
                changed,
                backup,
                cleanup_warning,
            )
    except (GitSourceError, OSError, ValueError) as exc:
        raise SkillLibraryError(str(exc)) from exc


def _stage_conflict(root, current, record, upstream, resolved_commit):
    conflict_root = root / CONFLICTS_REL
    if conflict_root.is_symlink():
        raise SkillLibraryError(f"skill conflict root is a symlink: {conflict_root}")
    conflict_root.mkdir(parents=True, exist_ok=True)
    destination = conflict_root / current.name
    if destination.is_symlink():
        raise SkillLibraryError(f"skill conflict destination is a symlink: {destination}")
    temporary = conflict_root / f".{current.name}.brain-stage-{uuid.uuid4().hex}"
    temporary.mkdir()
    previous = None
    staged = False
    try:
        copy_package(upstream, temporary / "upstream")
        metadata = {
            "schema_version": 1,
            "skill": current.name,
            "baseline_package_sha256": record["installed_baseline_sha256"],
            "local_package_sha256": current.package_sha256,
            "upstream_package_sha256": upstream.package_sha256,
            "upstream_commit": resolved_commit,
            "local_changes": _changed_manifest_paths(
                record.get("installed_manifest"), current
            ),
            "upstream_changes": _changed_manifest_paths(
                record.get("installed_manifest"), upstream
            ),
            "local_upstream_differences": _snapshot_difference_paths(
                current, upstream
            ),
            "staged_at": _now(),
        }
        safe_write_json(
            temporary / "comparison.json",
            metadata,
            bounds=temporary,
            follow_symlinks=False,
        )
        if destination.exists():
            previous = conflict_root / f".{current.name}.previous-{uuid.uuid4().hex}"
            os.replace(destination, previous)
        os.replace(temporary, destination)
        staged = True
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        if staged:
            shutil.rmtree(destination, ignore_errors=True)
        if previous is not None and previous.exists() and not destination.exists():
            try:
                os.replace(previous, destination)
            except BaseException as rollback_exc:
                raise SkillMutationOutcomeUncertain(
                    f"could not restore prior conflict stage for {current.name!r}"
                ) from rollback_exc
        raise
    return destination, previous


def _rollback_conflict_stage(destination, previous):
    if destination.exists() and not destination.is_symlink():
        shutil.rmtree(destination)
    if previous is not None and previous.exists():
        os.replace(previous, destination)


def _cleanup_displaced_conflict(previous):
    if previous is None:
        return None
    try:
        shutil.rmtree(previous)
    except OSError as exc:
        return f"Prior conflict-stage cleanup did not complete: {exc}"
    return None


def _changed_manifest_paths(baseline_value, snapshot):
    baseline = {
        item["path"]: (item["sha256"], item["executable"])
        for item in baseline_value
    }
    return _difference_paths(baseline, _snapshot_identity(snapshot))


def _snapshot_difference_paths(left, right):
    return _difference_paths(_snapshot_identity(left), _snapshot_identity(right))


def _snapshot_identity(snapshot):
    return {
        item.path: (item.sha256, item.executable)
        for item in snapshot.files
    }


def _difference_paths(left, right):
    return sorted(
        path
        for path in left.keys() | right.keys()
        if left.get(path) != right.get(path)
    )


def _record_from_existing(record, source):
    value = _record(
        source_repository=str(record["repository"]),
        source_skill_path=str(record["skill_path"]),
        configured_ref=str(record["configured_ref"]),
        resolved_commit=source.resolved_commit,
        source=source.package,
        core_lineage=record.get("core_lineage"),
        installed_at=str(record["installed_at"]),
    )
    return value


def _record(
    *,
    source_repository,
    source_skill_path,
    configured_ref,
    resolved_commit,
    source,
    core_lineage,
    installed_at=None,
):
    now = _now()
    return {
        "repository": source_repository,
        "skill_path": source_skill_path,
        "configured_ref": configured_ref,
        "resolved_commit": resolved_commit,
        "source_package_sha256": source.package_sha256,
        "installed_baseline_sha256": source.package_sha256,
        "installed_manifest": manifest_value(source),
        "installed_at": installed_at or now,
        "last_checked_at": now,
        "available_commit": resolved_commit,
        "available_package_sha256": source.package_sha256,
        "source_error": None,
        "core_lineage": core_lineage,
    }


def _commit_package_and_tracking(
    root,
    snapshot,
    tracking,
    record,
    *,
    replace,
    archive_existing,
):
    name = snapshot.name
    old_tracking = deepcopy(tracking)
    updated = deepcopy(tracking)
    updated["managed"][name] = record
    destination = root / USER_SKILLS_REL / name
    backup = _install_snapshot(
        root,
        snapshot,
        replace=replace,
        archive_existing=archive_existing,
    )
    try:
        write_tracking(root, updated)
    except BaseException:
        try:
            _rollback_install(root, destination, backup, replace)
            write_tracking(root, old_tracking)
        except BaseException as rollback_exc:
            raise SkillMutationOutcomeUncertain(
                f"could not recover failed skill installation for {name!r}"
            ) from rollback_exc
        raise
    changed = [str(USER_SKILLS_REL / name), str(Path(".brain") / "skill-sources.json")]
    cleanup_warning = None
    if backup is not None:
        changed.append(str(backup.relative_to(root)))
        cleanup_warning = _trim_backups(root, name)
    return (
        tuple(changed),
        str(backup.relative_to(root)) if backup else None,
        cleanup_warning,
    )


def _install_snapshot(root, snapshot, *, replace, archive_existing):
    skills_root = root / USER_SKILLS_REL
    if skills_root.is_symlink():
        raise SkillLibraryError(f"user skill root is a symlink: {skills_root}")
    skills_root.mkdir(parents=True, exist_ok=True)
    destination = skills_root / snapshot.name
    if destination.is_symlink():
        raise SkillLibraryError(f"user skill destination is a symlink: {destination}")
    if destination.exists() and not replace:
        raise SkillLibraryError(f"user skill already exists: {snapshot.name}")
    temporary = skills_root / f".{snapshot.name}.brain-stage-{uuid.uuid4().hex}"
    copy_package(snapshot, temporary)
    backup = None
    try:
        if destination.exists():
            backup = _archive_destination(
                root,
                snapshot.name,
                "pre-reconcile" if archive_existing else "previous",
            )
            backup.parent.mkdir(parents=True, exist_ok=True)
            os.replace(destination, backup)
        os.replace(temporary, destination)
    except BaseException:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
        if backup is not None and backup.exists() and not destination.exists():
            try:
                os.replace(backup, destination)
            except BaseException as rollback_exc:
                raise SkillMutationOutcomeUncertain(
                    f"could not restore previous package for {snapshot.name!r}"
                ) from rollback_exc
        raise
    return backup


def _rollback_install(root, destination, backup, replace):
    if destination.exists() and not destination.is_symlink():
        rollback = root / BACKUPS_REL / f".{destination.name}.rollback-{uuid.uuid4().hex}"
        rollback.parent.mkdir(parents=True, exist_ok=True)
        os.replace(destination, rollback)
        shutil.rmtree(rollback, ignore_errors=True)
    if replace and backup is not None and backup.exists():
        os.replace(backup, destination)


def _archive_destination(root, name, label):
    backup_root = root / BACKUPS_REL
    if backup_root.is_symlink():
        raise SkillLibraryError(f"skill backup root is a symlink: {backup_root}")
    stem = f"{name}.{label}"
    candidate = backup_root / stem
    suffix = 2
    while candidate.exists() or candidate.is_symlink():
        candidate = backup_root / f"{stem}-{suffix}"
        suffix += 1
    return candidate


def _trim_backups(root, name):
    backup_root = root / BACKUPS_REL
    try:
        candidates = sorted(
            (
                path
                for path in backup_root.iterdir()
                if path.name.startswith(f"{name}.") and path.is_dir() and not path.is_symlink()
            ),
            key=lambda path: path.stat().st_mtime,
        )
        for path in candidates[:-MAX_BACKUPS_PER_SKILL]:
            shutil.rmtree(path)
    except OSError as exc:
        return f"Backup cleanup for {name!r} did not complete: {exc}"
    return None


def _skill_names(root, tracking, core_sources):
    names = (
        set(tracking["managed"])
        | set(tracking["core_overrides"])
        | set(core_sources)
    )
    for base in (root / USER_SKILLS_REL, root / CORE_SKILLS_REL):
        try:
            names.update(
                path.name
                for path in base.iterdir()
                if path.is_dir() and not path.is_symlink() and (path / "SKILL.md").is_file()
            )
        except FileNotFoundError:
            pass
    return names


def _user_status(name, snapshot, record, shadows_core, *, core_override=None):
    if not isinstance(record, dict):
        if isinstance(core_override, dict):
            baseline = str(core_override["installed_baseline_sha256"])
            customised = snapshot.package_sha256 != baseline
            return SkillStatus(
                name,
                SkillSubstrate.USER,
                True,
                False,
                (
                    SkillState.LOCALLY_CUSTOMISED
                    if customised
                    else SkillState.IN_SYNC
                ),
                snapshot.package_sha256,
                core_lineage=name,
                detail=(
                    "core-derived user override is locally customised"
                    if customised
                    else "clean core-derived user override"
                ),
            )
        return SkillStatus(
            name,
            SkillSubstrate.USER,
            True,
            False,
            SkillState.USER_OWNED,
            snapshot.package_sha256,
            detail="shadows core" if shadows_core else None,
        )
    state, detail = _classify_managed(snapshot, record)
    return SkillStatus(
        name,
        SkillSubstrate.USER,
        True,
        False,
        state,
        snapshot.package_sha256,
        str(record["repository"]),
        str(record["configured_ref"]),
        str(record["resolved_commit"]),
        _optional_text(record.get("available_commit")),
        _optional_text(record.get("core_lineage")),
        detail,
    )


def _core_status(name, snapshot, descriptor, check, *, shadowed):
    state = SkillState.CORE
    detail = None
    available = None
    if isinstance(check, dict):
        available = _optional_text(check.get("resolved_commit"))
        if check.get("error"):
            state = SkillState.SOURCE_MISSING
            detail = str(check["error"])
        elif check.get("package_sha256") not in (None, snapshot.package_sha256):
            state = SkillState.UPDATE_READY
    return SkillStatus(
        name,
        SkillSubstrate.CORE,
        not shadowed,
        shadowed,
        state,
        snapshot.package_sha256,
        _optional_text(descriptor.get("repository")) if descriptor else None,
        _optional_text(descriptor.get("configured_ref")) if descriptor else None,
        _optional_text(descriptor.get("resolved_commit")) if descriptor else None,
        available,
        None,
        detail,
    )


def _classify_managed(snapshot, record):
    if record.get("source_error"):
        return SkillState.SOURCE_MISSING, str(record["source_error"])
    current = snapshot.package_sha256
    baseline = str(record["installed_baseline_sha256"])
    source_baseline = str(record["source_package_sha256"])
    available = record.get("available_package_sha256") or source_baseline
    transition = _classify_transition(
        current=current,
        installed_baseline=baseline,
        source_baseline=source_baseline,
        available_source=str(available),
    )
    if transition is _SkillTransition.MATCHES_SOURCE:
        return SkillState.IN_SYNC, "local and available source packages match"
    if transition is _SkillTransition.CONFLICT:
        return SkillState.CONFLICT, "local and source packages both changed"
    if transition is _SkillTransition.LOCAL_ONLY:
        return SkillState.LOCALLY_CUSTOMISED, "local package changed"
    if transition is _SkillTransition.SOURCE_ONLY:
        return SkillState.UPDATE_READY, "source update is available"
    return (
        SkillState.SOURCE_MISSING,
        "tracking baselines disagree with the installed package",
    )


def _optional_snapshot(path, expected_name):
    if not path.exists() and not path.is_symlink():
        return None
    try:
        return inspect_package(path, expected_name=expected_name)
    except ValueError as exc:
        raise SkillLibraryError(str(exc)) from exc


def _require_user_snapshot(root, name):
    snapshot = _optional_snapshot(root / USER_SKILLS_REL / name, name)
    if snapshot is None:
        raise SkillLibraryError(f"managed user skill package is missing: {name}")
    return snapshot


def _optional_text(value):
    return value if isinstance(value, str) and value else None


def _now():
    return datetime.now(timezone.utc).isoformat()
