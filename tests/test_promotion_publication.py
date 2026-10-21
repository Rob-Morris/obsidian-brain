"""Real local Git publication boundaries; no hosted services are contacted."""
from __future__ import annotations

import subprocess

import pytest

from test_promotion import _dev_repo, _receipt, _request, _passed, _git
from _promotion import workflow, git, model, candidates


def _queue(tmp_path):
    root, shas = _dev_repo(tmp_path)
    _receipt(root)
    first = workflow.prepare(root, _request(shas['two']), run_checks=None)
    workflow.finish(root, 'promotion/v1.1.0', ci=_passed)
    _receipt(root)
    second = workflow.prepare(root, _request(None, '1.2.0'), run_checks=None)
    workflow.finish(root, 'promotion/v1.2.0', ci=_passed)
    return root, shas['main'], first, second


def _refs(root):
    return git.remote_refs(root, ['refs/heads/main', 'refs/tags/v1.1.0', 'refs/tags/v1.2.0'])


def test_multi_release_certifies_each_exact_commit_and_tags_without_extra_commit(tmp_path):
    root, old, first, second = _queue(tmp_path)
    checked = []
    def ci(_root, sha, branch):
        checked.append((sha, branch))
        return {'state': 'passed'}
    workflow.publish(root, second, ci=ci)
    assert checked == [(first, 'promotion/v1.1.0'), (second, 'promotion/v1.2.0')]
    assert _refs(root) == {'refs/heads/main': second, 'refs/tags/v1.1.0': first, 'refs/tags/v1.2.0': second}
    assert git.read_commit(root, second).parents == (first,)
    assert not git.promotion_refs(root)


def test_first_release_failed_ci_refuses_entire_batch(tmp_path):
    root, old, first, second = _queue(tmp_path)
    with pytest.raises(model.PromotionError, match='not passed'):
        workflow.publish(root, second, ci=lambda _r, sha, _b: {'state': 'failed' if sha == first else 'passed'})
    assert _refs(root) == {'refs/heads/main': old}
    assert len(git.promotion_refs(root)) == 2


def test_conflicting_remote_tag_refuses_before_main_or_other_tag_changes(tmp_path):
    root, old, first, second = _queue(tmp_path)
    _git(root, 'push', 'origin', f'{old}:refs/tags/v1.1.0')
    with pytest.raises(model.PromotionError, match='conflicting published tag'):
        workflow.publish(root, second, ci=_passed)
    assert _refs(root) == {'refs/heads/main': old, 'refs/tags/v1.1.0': old}


def test_atomic_remote_rejection_preserves_main_all_tags_and_candidates(tmp_path):
    root, old, first, second = _queue(tmp_path)
    hook = tmp_path / 'repo.git/hooks/update'
    hook.write_text('#!/bin/sh\n[ "$1" != "refs/tags/v1.2.0" ]\n')
    hook.chmod(0o755)
    with pytest.raises(model.PromotionError, match='unchanged'):
        workflow.publish(root, second, ci=_passed)
    assert _refs(root) == {'refs/heads/main': old}
    assert len(git.promotion_refs(root)) == 2


def test_lost_push_response_is_recovered_by_observation(tmp_path, monkeypatch):
    root, old, first, second = _queue(tmp_path)
    real = git.run
    def lost(root, *args, **kwargs):
        result = real(root, *args, **kwargs)
        if args[:2] == ('push', '--atomic') and any(arg.endswith(':refs/heads/main') for arg in args):
            return subprocess.CompletedProcess(result.args, 1, '', 'response lost')
        return result
    monkeypatch.setattr(git, 'run', lost)
    workflow.publish(root, second, ci=_passed)
    assert _refs(root)['refs/heads/main'] == second
    assert not git.promotion_refs(root)


def test_already_published_repairs_missing_tags_for_retained_candidates_only(tmp_path):
    root, old, first, second = _queue(tmp_path)
    _git(root, 'push', 'origin', f'{second}:refs/heads/main')
    workflow.publish(root, second, ci=_passed)
    assert _refs(root) == {'refs/heads/main': second, 'refs/tags/v1.1.0': first, 'refs/tags/v1.2.0': second}
    assert not git.remote_refs(root, ['refs/tags/v1.0.0'])


def test_matching_annotated_tag_is_preserved(tmp_path):
    root, old, first, second = _queue(tmp_path)
    _git(root, 'tag', '-a', 'v1.1.0', first, '-m', 'Existing permanent identity')
    tag_object = _git(root, 'rev-parse', 'refs/tags/v1.1.0').stdout.strip()
    _git(root, 'push', 'origin', 'refs/tags/v1.1.0')
    workflow.publish(root, second, ci=_passed)
    assert _refs(root)['refs/tags/v1.1.0'] == tag_object
    assert _refs(root)['refs/tags/v1.1.0^{}'] == first


def test_push_guard_rejects_tag_delete_conflict_and_main_without_tags(tmp_path):
    root, old, first, second = _queue(tmp_path)
    for update in [model.PushUpdate('refs/tags/v1.1.0', first, model.ZERO_SHA),
                   model.PushUpdate('refs/tags/v1.1.0', old, first)]:
        with pytest.raises(model.PromotionError):
            candidates.assess_push(root, [update])
    with pytest.raises(model.PromotionError, match='requires refs/tags'):
        candidates.assess_push(root, [model.PushUpdate('refs/heads/main', old, second)])
    candidates.assess_push(root, [model.PushUpdate('refs/heads/main', old, second),
                                 model.PushUpdate('refs/tags/v1.1.0', model.ZERO_SHA, first),
                                 model.PushUpdate('refs/tags/v1.2.0', model.ZERO_SHA, second)])


def test_tag_created_after_preflight_refuses_atomic_batch_with_nonexistence_lease(tmp_path):
    root, old, first, second = _queue(tmp_path)
    changed = False
    def ci(_root, _sha, _branch):
        nonlocal changed
        if not changed:
            _git(root, 'push', 'origin', f'{old}:refs/tags/v1.1.0')
            changed = True
        return {'state': 'passed'}
    with pytest.raises(model.PromotionError, match='uncertain'):
        workflow.publish(root, second, ci=ci)
    assert _refs(root) == {'refs/heads/main': old, 'refs/tags/v1.1.0': old}
    assert len(git.promotion_refs(root)) == 2


@pytest.mark.parametrize('declaration', [None, 'next-release', '99.0.0'])
def test_candidate_certification_rejects_missing_unresolved_or_future_baseline(tmp_path, monkeypatch, declaration):
    from dataclasses import replace
    root, old, first, second = _queue(tmp_path)
    real = candidates.release.release_facts
    def altered(root, view):
        facts = real(root, view)
        return replace(facts, full_support_core_version=declaration) if view == second else facts
    monkeypatch.setattr(candidates.release, 'release_facts', altered)
    with pytest.raises(model.PromotionError, match='concrete full-support'):
        candidates.validate_candidate(root, git.read_commit(root, second))
