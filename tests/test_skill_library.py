"""Capability and invariant tests for Git-backed Brain skill packages."""

from __future__ import annotations

from contextlib import contextmanager
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import threading
from types import SimpleNamespace

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "src" / "brain-core" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from _skill_library import (
    SkillLibraryError,
    add_git_skill,
    detach_skill,
    list_skill_status,
    materialise_core_skill_for_edit,
    reconcile_core_overrides,
    update_skill,
)
from _skill_library.models import SkillState, SkillSubstrate
from _skill_library.packages import PackageValidationError, inspect_package
from _skill_library import service as skill_service
from _skill_library import git_source
from _skill_library import packages as skill_packages
from _skill_library.tracking import TrackingError, load_tracking


def _write_skill(root: Path, name: str, body: str, *, extra: str | None = None) -> Path:
    package = root / name
    package.mkdir(parents=True, exist_ok=True)
    (package / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Test skill\n---\n\n{body}\n",
        encoding="utf-8",
    )
    if extra is not None:
        (package / "reference.md").write_text(extra, encoding="utf-8")
    return package


def _git(args: list[str], *, cwd: Path) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )
    return completed.stdout.strip()


def _source_repo(tmp_path: Path, name: str = "shaping") -> tuple[Path, str]:
    repository = tmp_path / "source"
    repository.mkdir()
    _git(["init", "--quiet"], cwd=repository)
    _git(["config", "user.email", "tests@example.invalid"], cwd=repository)
    _git(["config", "user.name", "Brain Tests"], cwd=repository)
    _write_skill(repository / "skills", name, "version one", extra="one")
    _git(["add", "."], cwd=repository)
    _git(["commit", "--quiet", "-m", "initial"], cwd=repository)
    return repository, _git(["rev-parse", "HEAD"], cwd=repository)


def _source_repo_with_skills(
    tmp_path: Path, names: tuple[str, ...]
) -> tuple[Path, str]:
    repository = tmp_path / "source"
    repository.mkdir()
    _git(["init", "--quiet"], cwd=repository)
    _git(["config", "user.email", "tests@example.invalid"], cwd=repository)
    _git(["config", "user.name", "Brain Tests"], cwd=repository)
    for name in names:
        _write_skill(repository / "skills", name, f"{name} version one")
    _git(["add", "."], cwd=repository)
    _git(["commit", "--quiet", "-m", "initial"], cwd=repository)
    return repository, _git(["rev-parse", "HEAD"], cwd=repository)


def _advance(repository: Path, name: str, body: str) -> str:
    skill = repository / "skills" / name
    (skill / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Test skill\n---\n\n{body}\n",
        encoding="utf-8",
    )
    _git(["add", "."], cwd=repository)
    _git(["commit", "--quiet", "-m", body], cwd=repository)
    return _git(["rev-parse", "HEAD"], cwd=repository)


@pytest.fixture
def allow_local_git(monkeypatch):
    """Keep lifecycle tests local while boundary tests exercise remote validation."""
    monkeypatch.setattr(
        git_source,
        "validate_remote_repository",
        lambda repository: repository,
    )


def _core_manifest(vault: Path, name: str, repository: Path, commit: str) -> None:
    core = vault / ".brain-core"
    core.mkdir(parents=True, exist_ok=True)
    (core / "skill-sources.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "skills": {
                    name: {
                        "repository": str(repository),
                        "skill_path": f"skills/{name}",
                        "configured_ref": commit,
                        "resolved_commit": commit,
                    }
                },
            }
        ),
        encoding="utf-8",
    )


def test_package_identity_covers_complete_tree_and_executable_mode(tmp_path):
    package = _write_skill(tmp_path, "example", "body", extra="reference")
    first = inspect_package(package)
    (package / "reference.md").chmod(0o755)
    executable = inspect_package(package)
    assert first.package_sha256 != executable.package_sha256

    (package / "linked.md").symlink_to(package / "reference.md")
    with pytest.raises(PackageValidationError, match="symlink"):
        inspect_package(package)



def test_tracking_rejects_skill_names_that_can_escape_package_roots(tmp_path):
    vault = tmp_path / "vault"
    tracking = vault / ".brain/skill-sources.json"
    tracking.parent.mkdir(parents=True)
    tracking.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "managed": {},
                "core_overrides": {},
                "core_checks": {"../outside": {}},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(TrackingError, match="invalid skill name"):
        load_tracking(vault)


def test_package_rejects_casefold_collisions_and_resource_limits(
    tmp_path, monkeypatch
):
    folded_paths = {}
    skill_packages._record_portable_path(folded_paths, "Straße.md")
    with pytest.raises(PackageValidationError, match="case-folding"):
        skill_packages._record_portable_path(folded_paths, "strasse.md")

    oversized = _write_skill(tmp_path / "oversized", "example", "body")
    normal_size = sum(path.stat().st_size for path in oversized.rglob("*"))
    monkeypatch.setattr(skill_packages, "MAX_PACKAGE_BYTES", normal_size - 1)
    with pytest.raises(PackageValidationError, match="byte limit"):
        inspect_package(oversized)

    too_many = _write_skill(tmp_path / "too-many", "example", "body")
    (too_many / "one.md").write_text("one", encoding="utf-8")
    (too_many / "two.md").write_text("two", encoding="utf-8")
    monkeypatch.setattr(skill_packages, "MAX_PACKAGE_BYTES", 32 * 1024 * 1024)
    monkeypatch.setattr(skill_packages, "MAX_PACKAGE_FILES", 2)
    with pytest.raises(PackageValidationError, match="file limit"):
        inspect_package(too_many)


def test_git_archive_rejects_traversal_and_special_entries(tmp_path):
    traversal = io.BytesIO()
    with tarfile.open(fileobj=traversal, mode="w") as archive:
        info = tarfile.TarInfo("../outside")
        info.size = 1
        archive.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(git_source.GitSourceError, match="unsafe path"):
        git_source._extract_archive(traversal.getvalue(), tmp_path / "traversal")

    special = io.BytesIO()
    with tarfile.open(fileobj=special, mode="w") as archive:
        info = tarfile.TarInfo("linked")
        info.type = tarfile.SYMTYPE
        info.linkname = "target"
        archive.addfile(info)
    with pytest.raises(git_source.GitSourceError, match="unsupported entry"):
        git_source._extract_archive(special.getvalue(), tmp_path / "special")


def test_git_archive_capture_is_bounded_before_extraction(monkeypatch):
    monkeypatch.setattr(git_source, "MAX_ARCHIVE_BYTES", 10)

    with pytest.raises(git_source.GitSourceError, match="safe staging limits"):
        git_source._run_archive(
            sys.executable,
            "-c",
            "import sys; sys.stdout.buffer.write(b'x' * 11)",
        )


def test_repository_checkout_releases_each_package_stage_after_scope(
    tmp_path,
    allow_local_git,
):
    repository, commit = _source_repo(tmp_path)

    with git_source.checkout_repository(
        str(repository),
        configured_ref=commit,
    ) as checkout:
        with checkout.checkout_source(
            skill_path="skills/shaping",
            expected_name="shaping",
        ) as source:
            package_root = source.package.root
            assert package_root.is_dir()
        assert not package_root.exists()


@pytest.mark.parametrize(
    ("repository", "message"),
    (
        ("/srv/private/repository", "approved https or ssh"),
        ("file:///srv/private/repository", "approved https or ssh"),
        ("../repository", "approved https or ssh"),
        ("ext::sh -c command", "unsafe whitespace"),
        ("http://example.com/repository.git", "approved https or ssh"),
        (r"C:\private\repository", "approved https or ssh"),
        ("C:/private/repository", "approved https or ssh"),
        ("C:relative-repository", "approved https or ssh"),
        ("z:/repo.git", "approved https or ssh"),
    ),
)
def test_git_source_rejects_local_and_unapproved_repository_forms(
    repository,
    message,
):
    with pytest.raises(git_source.GitSourceError, match=message):
        git_source.validate_remote_repository(repository)


@pytest.mark.parametrize(
    "repository",
    (
        "https://example.com/owner/repository.git",
        "ssh://git@example.com/owner/repository.git",
        "ssh://git@[2001:db8::1]:2222/owner/repository.git",
        "git@example.com:owner/repository.git",
        "example.com:owner/repository.git",
    ),
)
def test_git_source_accepts_approved_remote_repository_forms(repository):
    assert git_source.validate_remote_repository(repository) == repository


@pytest.mark.parametrize(
    "repository",
    (
        "https://PatientSSN123@example.com/owner/repository.git",
        "https://user:PatientSSN123@example.com/owner/repository.git",
        "https://example.com/owner/repository.git?token=PatientSSN123",
    ),
)
def test_git_source_rejects_https_user_information_without_echoing_it(repository):
    with pytest.raises(git_source.GitSourceError) as failure:
        git_source.validate_remote_repository(repository)

    assert "PatientSSN123" not in str(failure.value)


@pytest.mark.parametrize(
    "repository",
    (
        "https://PatientSSN123%40example.com/owner/repository.git",
        "example.com:owner/repository.git?token=PatientSSN123",
        "example.com:owner/repository.git#PatientSSN123",
        "https:/PatientSSN123/repository.git",
        "-p@example.com:owner/repository.git",
        "C:/private/repository",
        "C:relative-repository",
        "z:/repo.git",
    ),
)
def test_rejected_remote_grammar_never_reaches_git_or_echoes_secret(
    repository,
    monkeypatch,
):
    git_calls = []
    monkeypatch.setattr(
        git_source,
        "_run",
        lambda *arguments: git_calls.append(arguments),
    )

    with pytest.raises(git_source.GitSourceError) as failure:
        with git_source.checkout_repository(repository):
            raise AssertionError("invalid repository reached checkout")

    assert git_calls == []
    assert "PatientSSN123" not in str(failure.value)


@pytest.mark.parametrize(
    "configured_ref",
    ("--upload-pack=command", "refs/heads/main^{}", "../main", "main lock"),
)
def test_git_source_rejects_option_shaped_and_unsafe_refs(configured_ref):
    with pytest.raises(git_source.GitSourceError, match="unsafe or unsupported"):
        git_source.validate_configured_ref(configured_ref)


def test_git_import_rejects_a_repository_skill_symlink(tmp_path, allow_local_git):
    repository, commit = _source_repo(tmp_path)
    linked = repository / "skills/shaping/linked.md"
    linked.symlink_to("reference.md")
    _git(["add", "linked.md"], cwd=linked.parent)
    _git(["commit", "--quiet", "-m", "add symlink"], cwd=repository)
    commit = _git(["rev-parse", "HEAD"], cwd=repository)

    with pytest.raises(SkillLibraryError, match="unsupported entry"):
        add_git_skill(
            tmp_path / "vault",
            repository=str(repository),
            skill_path="skills/shaping",
            configured_ref=commit,
        )


def test_add_refresh_update_and_detach_managed_user_skill(tmp_path, allow_local_git):
    repository, first_commit = _source_repo(tmp_path)
    vault = tmp_path / "vault"
    vault.mkdir()

    installed = add_git_skill(
        vault,
        repository=str(repository),
        skill_path="skills/shaping",
        configured_ref=first_commit,
    )
    assert installed.action == "installed"
    assert installed.state is SkillState.IN_SYNC
    assert (vault / "_Config/Skills/shaping/reference.md").read_text() == "one"

    second_commit = _advance(repository, "shaping", "version two")
    status = list_skill_status(vault, name="shaping", refresh=True)
    assert status[0].state is SkillState.IN_SYNC  # pinned to the configured commit

    updated = update_skill(vault, name="shaping", to_commit=second_commit)
    assert updated.action == "updated"
    assert "version two" in (vault / "_Config/Skills/shaping/SKILL.md").read_text()

    detached = detach_skill(vault, name="shaping")
    assert detached.state is SkillState.USER_OWNED
    assert list_skill_status(vault, name="shaping")[0].state is SkillState.USER_OWNED


def test_refresh_reports_moving_branch_update_without_mutating_package(
    tmp_path, allow_local_git
):
    repository, _first_commit = _source_repo(tmp_path)
    branch = _git(["branch", "--show-current"], cwd=repository)
    vault = tmp_path / "vault"
    vault.mkdir()
    add_git_skill(
        vault,
        repository=str(repository),
        skill_path="skills/shaping",
        configured_ref=branch,
    )
    installed = vault / "_Config/Skills/shaping/SKILL.md"
    installed_before = installed.read_bytes()
    second_commit = _advance(repository, "shaping", "version two")

    status = list_skill_status(vault, name="shaping", refresh=True)[0]
    tracking = load_tracking(vault)["managed"]["shaping"]

    assert status.state is SkillState.UPDATE_READY
    assert status.available_commit == second_commit
    assert tracking["available_package_sha256"] != tracking["source_package_sha256"]
    assert installed.read_bytes() == installed_before


def test_unscoped_refresh_fetches_shared_source_once_and_isolates_path_errors(
    tmp_path, monkeypatch, allow_local_git
):
    repository, commit = _source_repo_with_skills(tmp_path, ("review", "shaping"))
    vault = tmp_path / "vault"
    for name in ("review", "shaping"):
        _write_skill(vault / ".brain-core/skills", name, f"bundled {name}")
    manifest = {
        "schema_version": 1,
        "skills": {
            "review": {
                "repository": str(repository),
                "skill_path": "skills/missing",
                "configured_ref": commit,
                "resolved_commit": commit,
            },
            "shaping": {
                "repository": str(repository),
                "skill_path": "skills/shaping",
                "configured_ref": commit,
                "resolved_commit": commit,
            },
        },
    }
    manifest_path = vault / ".brain-core/skill-sources.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    fetches = 0
    original_run = git_source._run

    def count_fetches(program, *arguments):
        nonlocal fetches
        if "fetch" in arguments:
            fetches += 1
        return original_run(program, *arguments)

    monkeypatch.setattr(git_source, "_run", count_fetches)
    list_skill_status(vault, refresh=True)
    checks = load_tracking(vault)["core_checks"]

    assert fetches == 1
    assert checks["shaping"]["resolved_commit"] == commit
    assert checks["shaping"]["error"] is None
    assert checks["review"]["resolved_commit"] is None
    assert checks["review"]["error"]


def test_unscoped_refresh_bounds_independent_groups_and_isolates_failures(
    tmp_path,
    monkeypatch,
):
    names = ("alpha", "bravo", "charlie", "delta", "echo")
    vault = tmp_path / "vault"
    for name in names:
        _write_skill(vault / ".brain-core/skills", name, f"bundled {name}")
    manifest = {
        "schema_version": 1,
        "skills": {
            name: {
                "repository": f"https://{name}.example.invalid/repository.git",
                "skill_path": f"skills/{name}",
                "configured_ref": "main",
                "resolved_commit": f"old-{name}",
            }
            for name in names
        },
    }
    manifest_path = vault / ".brain-core/skill-sources.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    release = threading.Event()
    state_lock = threading.Lock()
    state = {"active": 0, "maximum": 0}

    class Checkout:
        def __init__(self, repository):
            self.repository = repository

        @contextmanager
        def checkout_source(self, *, skill_path, expected_name):
            assert skill_path == f"skills/{expected_name}"
            yield SimpleNamespace(
                resolved_commit=f"new-{expected_name}",
                package=SimpleNamespace(package_sha256=f"sha-{expected_name}"),
            )

    @contextmanager
    def checkout(repository, *, configured_ref):
        assert configured_ref == "main"
        with state_lock:
            state["active"] += 1
            state["maximum"] = max(state["maximum"], state["active"])
            if state["active"] == skill_service._MAX_SOURCE_REFRESH_WORKERS:
                release.set()
        try:
            assert release.wait(timeout=1)
            if "charlie" in repository:
                raise git_source.GitSourceError("source unavailable")
            yield Checkout(repository)
        finally:
            with state_lock:
                state["active"] -= 1

    monkeypatch.setattr(skill_service, "checkout_repository", checkout)

    rows = list_skill_status(vault, refresh=True)
    checks = load_tracking(vault)["core_checks"]

    assert tuple(row.name for row in rows) == names
    assert state["maximum"] == skill_service._MAX_SOURCE_REFRESH_WORKERS
    assert checks["alpha"]["resolved_commit"] == "new-alpha"
    assert checks["bravo"]["resolved_commit"] == "new-bravo"
    assert checks["charlie"]["resolved_commit"] is None
    assert checks["charlie"]["error"] == "source unavailable"
    assert checks["delta"]["resolved_commit"] == "new-delta"
    assert checks["echo"]["resolved_commit"] == "new-echo"


def test_conflict_stages_upstream_and_replacement_archives_local(
    tmp_path, allow_local_git
):
    repository, first_commit = _source_repo(tmp_path)
    vault = tmp_path / "vault"
    vault.mkdir()
    add_git_skill(
        vault,
        repository=str(repository),
        skill_path="skills/shaping",
        configured_ref=first_commit,
    )
    local = vault / "_Config/Skills/shaping/SKILL.md"
    local.write_text(local.read_text() + "\nlocal edit\n", encoding="utf-8")
    second_commit = _advance(repository, "shaping", "upstream edit")

    conflict = update_skill(vault, name="shaping", to_commit=second_commit)
    assert conflict.action == "conflict_staged"
    assert conflict.state is SkillState.CONFLICT
    comparison = json.loads(
        (vault / ".brain/skill-conflicts/shaping/comparison.json").read_text()
    )
    assert comparison["local_changes"] == ["SKILL.md"]
    assert comparison["upstream_changes"] == ["SKILL.md"]
    assert comparison["local_upstream_differences"] == ["SKILL.md"]
    assert "local edit" in local.read_text()

    replaced = update_skill(
        vault,
        name="shaping",
        to_commit=second_commit,
        replace_conflict=True,
    )
    assert replaced.action == "replaced"
    assert replaced.backup_path is not None
    assert "local edit" in (vault / replaced.backup_path / "SKILL.md").read_text()
    assert "upstream edit" in local.read_text()


def test_manually_reconciled_conflict_rebaselines_without_replacement(
    tmp_path, allow_local_git
):
    repository, first_commit = _source_repo(tmp_path)
    vault = tmp_path / "vault"
    vault.mkdir()
    add_git_skill(
        vault,
        repository=str(repository),
        skill_path="skills/shaping",
        configured_ref=first_commit,
    )
    local = vault / "_Config/Skills/shaping"
    skill_file = local / "SKILL.md"
    skill_file.write_text(skill_file.read_text() + "\nlocal edit\n", encoding="utf-8")
    second_commit = _advance(repository, "shaping", "upstream two")

    conflict = update_skill(vault, name="shaping", to_commit=second_commit)
    upstream = vault / conflict.changed_paths[0] / "upstream"
    shutil.rmtree(local)
    shutil.copytree(upstream, local)

    reconciled = update_skill(vault, name="shaping", to_commit=second_commit)
    tracking = load_tracking(vault)["managed"]["shaping"]

    assert reconciled.action == "rebaselined"
    assert reconciled.backup_path is None
    assert tracking["resolved_commit"] == second_commit
    assert tracking["installed_baseline_sha256"] == reconciled.package_sha256


def test_conflict_stage_rolls_back_when_tracking_write_fails(
    tmp_path, monkeypatch, allow_local_git
):
    repository, first_commit = _source_repo(tmp_path)
    vault = tmp_path / "vault"
    vault.mkdir()
    add_git_skill(
        vault,
        repository=str(repository),
        skill_path="skills/shaping",
        configured_ref=first_commit,
    )
    local = vault / "_Config/Skills/shaping/SKILL.md"
    local.write_text(local.read_text() + "\nlocal edit\n", encoding="utf-8")
    second_commit = _advance(repository, "shaping", "upstream two")
    update_skill(vault, name="shaping", to_commit=second_commit)
    comparison_path = vault / ".brain/skill-conflicts/shaping/comparison.json"
    original_comparison = comparison_path.read_bytes()

    third_commit = _advance(repository, "shaping", "upstream three")

    def fail_write(*_args, **_kwargs):
        raise OSError("simulated tracking failure")

    monkeypatch.setattr(skill_service, "write_tracking", fail_write)
    with pytest.raises(SkillLibraryError, match="simulated tracking failure"):
        update_skill(vault, name="shaping", to_commit=third_commit)

    assert comparison_path.read_bytes() == original_comparison
    assert "local edit" in local.read_text()


def test_conflict_stage_reports_displaced_stage_cleanup_failure(
    tmp_path, monkeypatch, allow_local_git
):
    repository, first_commit = _source_repo(tmp_path)
    vault = tmp_path / "vault"
    vault.mkdir()
    add_git_skill(
        vault,
        repository=str(repository),
        skill_path="skills/shaping",
        configured_ref=first_commit,
    )
    local = vault / "_Config/Skills/shaping/SKILL.md"
    local.write_text(local.read_text() + "\nlocal edit\n", encoding="utf-8")
    second_commit = _advance(repository, "shaping", "upstream two")
    update_skill(vault, name="shaping", to_commit=second_commit)
    third_commit = _advance(repository, "shaping", "upstream three")
    original_rmtree = skill_service.shutil.rmtree

    def fail_displaced_cleanup(path, *args, **kwargs):
        if ".previous-" in Path(path).name:
            raise OSError("simulated displaced-stage cleanup failure")
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(skill_service.shutil, "rmtree", fail_displaced_cleanup)
    conflict = update_skill(vault, name="shaping", to_commit=third_commit)

    assert "Prior conflict-stage cleanup did not complete" in conflict.detail
    comparison = json.loads(
        (vault / ".brain/skill-conflicts/shaping/comparison.json").read_text()
    )
    assert comparison["upstream_commit"] == third_commit


def test_core_update_materialises_only_when_no_user_copy_exists(
    tmp_path, allow_local_git
):
    repository, first_commit = _source_repo(tmp_path)
    vault = tmp_path / "vault"
    _write_skill(vault / ".brain-core/skills", "shaping", "bundled")
    _core_manifest(vault, "shaping", repository, first_commit)
    second_commit = _advance(repository, "shaping", "new upstream")

    mutation = update_skill(vault, name="shaping", to_commit=second_commit)
    assert mutation.action == "installed"
    assert "new upstream" in (vault / "_Config/Skills/shaping/SKILL.md").read_text()
    rows = list_skill_status(vault, name="shaping")
    assert [(row.substrate, row.effective) for row in rows] == [
        (SkillSubstrate.USER, True),
        (SkillSubstrate.CORE, False),
    ]

    other_vault = tmp_path / "other-vault"
    _write_skill(other_vault / ".brain-core/skills", "shaping", "bundled")
    _write_skill(other_vault / "_Config/Skills", "shaping", "independent user")
    _core_manifest(other_vault, "shaping", repository, first_commit)
    with pytest.raises(SkillLibraryError, match="no configured update source"):
        update_skill(other_vault, name="shaping", to_commit=second_commit)
    assert "independent user" in (
        other_vault / "_Config/Skills/shaping/SKILL.md"
    ).read_text()


def test_copy_on_write_and_core_upgrade_reconciliation_are_lineage_safe(tmp_path):
    repository, commit = _source_repo(tmp_path)
    vault = tmp_path / "vault"
    core = _write_skill(vault / ".brain-core/skills", "shaping", "bundled")
    _core_manifest(vault, "shaping", repository, commit)

    materialised = materialise_core_skill_for_edit(vault, name="shaping")
    assert materialised is not None
    assert (core / "SKILL.md").read_text() == (
        vault / "_Config/Skills/shaping/SKILL.md"
    ).read_text()
    collapsed = reconcile_core_overrides(vault)
    assert collapsed[0].action == "collapsed_to_core"
    assert not (vault / "_Config/Skills/shaping").exists()
    assert (vault / collapsed[0].archived_path / "SKILL.md").is_file()

    materialise_core_skill_for_edit(vault, name="shaping")
    user = vault / "_Config/Skills/shaping/SKILL.md"
    user.write_text(user.read_text() + "\ncustom\n", encoding="utf-8")
    assert reconcile_core_overrides(vault) == ()
    assert user.is_file()


def test_copy_on_write_tracks_lineage_for_directly_authored_core_skill(tmp_path):
    vault = tmp_path / "vault"
    _write_skill(vault / ".brain-core/skills", "shaping", "bundled")

    mutation = materialise_core_skill_for_edit(vault, name="shaping")

    assert mutation is not None
    tracking = json.loads((vault / ".brain/skill-sources.json").read_text())
    override = tracking["core_overrides"]["shaping"]
    assert override["core_lineage"] == "shaping"
    assert override["installed_baseline_sha256"] == mutation.package_sha256
    assert list_skill_status(vault, name="shaping")[0].state is SkillState.IN_SYNC

    reconciled = reconcile_core_overrides(vault)
    assert reconciled[0].action == "collapsed_to_core"
    assert not (vault / "_Config/Skills/shaping").exists()
