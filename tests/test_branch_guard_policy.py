"""Fast real-Git policy/continuity tests; no installed-runtime fixture."""

import subprocess

import pytest

from _bootstrap import branch_guard as guard
from _bootstrap.local_config import (
    LocalConfigError, patch_local_config, read_local_config,
)


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def checkout(tmp_path):
    git(tmp_path, "init", "-b", "dev")
    return tmp_path


def configure(root, branches=("dev",)):
    return guard.configure_guard(root, list(branches), read_local_config(root).revision)


def test_unconfigured_does_not_probe_git_at_effect(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "observe_git", lambda _: pytest.fail("unconfigured guard probed Git"))
    guard.checkpoint(tmp_path)


def test_unborn_allowed_branch_and_detached(checkout):
    configure(checkout)
    guard.checkpoint(checkout)
    git(checkout, "-c", "user.name=Test", "-c", "user.email=test@example.test", "commit", "--allow-empty", "-m", "seed")
    git(checkout, "checkout", "--detach")
    with pytest.raises(guard.BranchGuardError, match="detached-HEAD"):
        guard.checkpoint(checkout)


def test_wrong_branch_does_not_write(checkout):
    configure(checkout, ("main",))
    with pytest.raises(guard.BranchGuardError, match="branch-not-allowed"):
        guard.checkpoint(checkout)
    assert guard.inspect_guard(checkout)["status"] == "blocked"


def test_allowed_to_allowed_switch_is_sticky(checkout):
    configure(checkout, ("dev", "main"))
    with guard.operation(checkout):
        guard.checkpoint(checkout)
        git(checkout, "symbolic-ref", "HEAD", "refs/heads/main")
        with pytest.raises(guard.BranchGuardError, match="branch-changed"):
            guard.checkpoint(checkout)
        git(checkout, "symbolic-ref", "HEAD", "refs/heads/dev")
        with pytest.raises(guard.BranchGuardError, match="unverified-recovery-context"):
            guard.checkpoint(checkout)


def test_away_and_back_is_deliberately_not_claimed_detected(checkout):
    configure(checkout)
    with guard.operation(checkout):
        git(checkout, "symbolic-ref", "HEAD", "refs/heads/main")
        git(checkout, "symbolic-ref", "HEAD", "refs/heads/dev")
        guard.checkpoint(checkout)


def test_unrelated_local_patch_preserves_policy_and_operation(checkout):
    configure(checkout)
    with guard.operation(checkout):
        patch_local_config(checkout, lambda d: d.update(defaults={"local_runtime": {"ready": True}}))
        guard.checkpoint(checkout)
    assert read_local_config(checkout).document["branch_guard"]["allowed_branches"] == ["dev"]


def test_policy_edit_invalidates_operation_and_old_cas(checkout):
    configure(checkout)
    old = read_local_config(checkout).revision
    with guard.operation(checkout):
        guard.configure_guard(checkout, ["dev", "main"], old)
        with pytest.raises(guard.BranchGuardError, match="policy-changed"):
            guard.checkpoint(checkout)
    with pytest.raises(LocalConfigError, match="changed"):
        guard.configure_guard(checkout, None, old)


def test_guard_only_configuration_can_be_disabled_then_reconfigured(checkout):
    configure(checkout)
    enabled = read_local_config(checkout)
    guard.configure_guard(checkout, None, enabled.revision)
    assert read_local_config(checkout).document == {}
    assert guard.inspect_guard(checkout)["status"] == "unconfigured"
    guard.checkpoint(checkout)
    configure(checkout)
    guard.checkpoint(checkout)


def test_setting_policy_rechecks_applicability_under_patch_lock(checkout, monkeypatch):
    observations = iter([guard.GitState(str(checkout), "dev"), guard.GitState(None, None)])
    monkeypatch.setattr(guard, "observe_git", lambda _: next(observations))
    with pytest.raises(guard.BranchGuardError, match="outside-git"):
        guard.configure_guard(checkout, ["dev"], "missing")
    assert read_local_config(checkout).revision == "missing"


@pytest.mark.parametrize("section", [None, {}, [], {"allowed_branches": []},
    {"allowed_branches": ["dev", "dev"]}, {"allowed_branches": ["dev"], "enabled": True},
    {"allowed_branches": ["refs/heads/../main"]}, {"allowed_branches": [False]}])
def test_malformed_policy_can_be_inspected_and_explicitly_removed(checkout, section):
    if section is None:
        path = checkout / ".brain/local/config.yaml"
        path.parent.mkdir(parents=True)
        path.write_text("branch_guard: null\nsecret: keep\n")
    else:
        patch_local_config(checkout, lambda d: d.update(branch_guard=section, secret="keep"))
    status = guard.inspect_guard(checkout)
    assert status["reason"] == "invalid-policy"
    assert status["local_config_revision"].startswith("sha256:")
    with pytest.raises(guard.BranchGuardError):
        guard.checkpoint(checkout)
    guard.configure_guard(checkout, None, status["local_config_revision"])
    assert read_local_config(checkout).document == {"secret": "keep"}


def test_dormant_policy_outside_git_and_no_enable_offer(tmp_path):
    patch_local_config(tmp_path, lambda d: d.update(branch_guard={"allowed_branches": ["dev"]}))
    guard.checkpoint(tmp_path)
    assert guard.inspect_guard(tmp_path)["status"] == "dormant"
    with pytest.raises(guard.BranchGuardError, match="outside-git"):
        configure(tmp_path)


@pytest.mark.parametrize("section", [{}, {"allowed_branches": []}, "null"])
def test_invalid_retained_section_is_dormant_outside_git(tmp_path, section):
    from types import SimpleNamespace
    import create
    patch_local_config(tmp_path, lambda d: d.update(branch_guard=section))
    before = read_local_config(tmp_path)
    state = guard.inspect_guard(tmp_path)
    assert state["status"] == "dormant" and state["reason"] == "invalid-policy"
    plan = SimpleNamespace(path="_Config/Memories/allowed.md", content="body",
                           exclusive=True, resource="memory", name="allowed")
    create.apply_named_resource_creation(tmp_path, plan)
    assert (tmp_path / plan.path).read_text() == "body"
    assert read_local_config(tmp_path) == before


def test_invalid_section_and_failed_inspection_is_not_dormancy(tmp_path, monkeypatch):
    patch_local_config(tmp_path, lambda d: d.update(branch_guard={}))
    def fail(_):
        raise guard.BranchGuardError("inspection-failed")
    monkeypatch.setattr(guard, "observe_git", fail)
    with pytest.raises(guard.BranchGuardError, match="inspection-failed"):
        guard.checkpoint(tmp_path)


def test_removing_dormant_policy_does_not_create_a_branch_restriction(tmp_path):
    patch_local_config(tmp_path, lambda d: d.update(branch_guard={}))
    with guard.operation(tmp_path):
        guard.configure_guard(tmp_path, None, read_local_config(tmp_path).revision)
        guard.checkpoint(tmp_path)


def test_known_loss_is_not_forgotten_when_git_disappears(checkout, monkeypatch):
    configure(checkout, ("dev", "main"))
    with guard.operation(checkout):
        git(checkout, "symbolic-ref", "HEAD", "refs/heads/main")
        with pytest.raises(guard.BranchGuardError):
            guard.checkpoint(checkout)
        monkeypatch.setattr(guard, "observe_git", lambda _: guard.GitState(None, None))
        with pytest.raises(guard.BranchGuardError, match="unverified-recovery-context"):
            guard.checkpoint(checkout)


def test_child_inherits_original_branch_and_target(checkout, monkeypatch):
    configure(checkout, ("dev", "main"))
    context = guard.export_context(checkout)
    git(checkout, "symbolic-ref", "HEAD", "refs/heads/main")
    monkeypatch.setenv(guard.CONTEXT_ENV, context)
    with pytest.raises(guard.BranchGuardError, match="unverified-recovery-context"):
        guard.checkpoint(checkout)


def test_missing_legacy_recovery_context_is_not_current_permission(checkout):
    configure(checkout)
    with guard.operation(checkout, recovery={}):
        with pytest.raises(guard.BranchGuardError, match="unverified-recovery-context"):
            guard.checkpoint(checkout)


def test_nested_recovery_does_not_bypass_existing_operation(checkout):
    configure(checkout)
    with guard.operation(checkout):
        with guard.operation(checkout, recovery={}):
            with pytest.raises(guard.BranchGuardError, match="unverified-recovery-context"):
                guard.checkpoint(checkout)


def test_child_refusal_remains_sticky_after_parent_branch_restored(checkout):
    configure(checkout)
    with guard.operation(checkout):
        guard.retain_context_loss(checkout)
        with pytest.raises(guard.BranchGuardError, match="unverified-recovery-context"):
            guard.checkpoint(checkout)


def test_legacy_recovery_without_opt_in_remains_available(tmp_path):
    with guard.operation(tmp_path, recovery={}):
        guard.checkpoint(tmp_path)


def test_git_environment_cannot_redirect_target(checkout, tmp_path_factory, monkeypatch):
    other = tmp_path_factory.mktemp("other")
    git(other, "init", "-b", "main")
    configure(checkout)
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    assert guard.observe_git(checkout).branch == "dev"
    guard.checkpoint(checkout)


def test_broken_git_is_not_absence(checkout):
    configure(checkout)
    (checkout / ".git" / "HEAD").write_text("broken\n")
    with pytest.raises(guard.BranchGuardError, match="inspection-failed"):
        guard.checkpoint(checkout)


def test_invalid_whole_yaml_not_force_replaced(checkout):
    path = checkout / ".brain/local/config.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("broken: [not closed\n")
    before = path.read_bytes()
    assert guard.inspect_guard(checkout)["reason"] == "unreadable-policy"
    with pytest.raises(LocalConfigError):
        guard.configure_guard(checkout, None, "anything")
    assert path.read_bytes() == before


def test_local_config_redirect_is_refused(checkout, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    (checkout / ".brain").mkdir()
    (checkout / ".brain/local").symlink_to(outside, target_is_directory=True)
    with pytest.raises(LocalConfigError):
        read_local_config(checkout)
    assert not (outside / "config.yaml").exists()


def test_multiple_vaults_in_one_repo_have_independent_policy(checkout):
    first, second = checkout / "one", checkout / "two"
    first.mkdir()
    second.mkdir()
    configure(first, ("dev",))
    configure(second, ("main",))
    guard.checkpoint(first)
    with pytest.raises(guard.BranchGuardError, match="branch-not-allowed"):
        guard.checkpoint(second)
    assert guard.observe_git(first).repository == guard.observe_git(second).repository


def test_worktree_does_not_inherit_ignored_policy(checkout, tmp_path_factory):
    git(checkout, "-c", "user.name=Test", "-c", "user.email=test@example.test", "commit", "--allow-empty", "-m", "seed")
    configure(checkout)
    destination = tmp_path_factory.mktemp("worktree-parent") / "checkout"
    git(checkout, "worktree", "add", "-b", "other", str(destination))
    assert guard.inspect_guard(destination)["status"] == "unconfigured"
    configure(destination, ("other",))
    guard.checkpoint(destination)
    assert guard.observe_git(destination).branch == "other"
    guard.checkpoint(checkout)


def test_nested_repository_and_vault_alias_resolve_selected_target(checkout):
    inner = checkout / "nested"
    inner.mkdir()
    git(inner, "init", "-b", "nested-dev")
    configure(inner, ("nested-dev",))
    alias = checkout / "alias"
    alias.symlink_to(inner, target_is_directory=True)
    with guard.operation(inner) as original:
        with guard.operation(alias) as nested:
            assert original is nested
            guard.checkpoint(alias)
    assert guard.observe_git(inner).branch == "nested-dev"


def test_git_unavailable_does_not_become_dormant(checkout, monkeypatch):
    configure(checkout)
    monkeypatch.setenv("PATH", "")
    with pytest.raises(guard.BranchGuardError, match="git-unavailable"):
        guard.checkpoint(checkout)
    assert guard.inspect_guard(checkout)["reason"] == "git-unavailable"


def test_no_git_is_not_unhealthy_without_opt_in(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "")
    state = guard.inspect_guard(tmp_path)
    assert state["status"] == "unconfigured"
    assert state["reason"] == "git-unavailable"
    assert state["repository"] is None
    guard.checkpoint(tmp_path)
