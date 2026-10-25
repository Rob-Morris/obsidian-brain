"""Candidate Lab compatibility fails before the expensive verification boundary."""
from __future__ import annotations

import json
import shutil
from types import SimpleNamespace

import pytest

from test_promotion import REPO_ROOT
from _promotion import model, workflow
import release


@pytest.fixture
def candidate(tmp_path):
    for path in (
        release.VERSION_PATH, release.README_PATH, release.UNIX_CLI_PATH,
        release.WINDOWS_CLI_PATH, release.PROXY_PATH, release.CHANGELOG_INDEX_PATH,
        release.FUNCTIONAL_CLI_PATH, release.USER_REFERENCE_PATH,
        release.VERSION_CONTRACT_PATH, "src/scripts/release.py",
        "tools/brain-lab/compatibility.json",
        "tools/brain-lab/brain_lab/compatibility.py",
    ):
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO_ROOT / path, target)
    (tmp_path / "tools/brain-lab/brain_lab/__init__.py").touch()
    (tmp_path / "template-vault").mkdir()
    return tmp_path


def _manifest(candidate, mutate):
    path = candidate / "tools/brain-lab/compatibility.json"
    payload = json.loads(path.read_text())
    mutate(payload)
    path.write_text(json.dumps(payload))


def _runner(monkeypatch):
    calls = []

    def run(argv, *, cwd):
        calls.append((argv, cwd))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(workflow, "subprocess", SimpleNamespace(run=run))
    return calls


@pytest.mark.parametrize("failure,match", [
    ("missing", "no unique supported compatibility adapter"),
    ("overlap", "no unique supported compatibility adapter"),
    ("baseline", "declares CLI full-support Core baseline"),
    ("absent-manifest", "compatibility preflight failed"),
    ("incoherent", "release facts are incoherent"),
])
def test_invalid_candidate_stops_before_contracts_or_serial_suite(candidate, monkeypatch, failure, match):
    facts = release.release_facts(candidate, "worktree")
    assert facts.coherent

    def mutate(payload):
        from importlib.util import module_from_spec, spec_from_file_location
        import sys
        spec = spec_from_file_location("_test_lab_compatibility", REPO_ROOT / "tools/brain-lab/brain_lab/compatibility.py")
        module = module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)
        selected = module.CompatibilityManifest(candidate / "tools/brain-lab/compatibility.json").select(facts.core)
        adapter = next(item for item in payload["adapters"] if item["id"] == selected.adapter_id)
        if failure == "missing":
            payload["adapters"].remove(adapter)
        elif failure == "overlap":
            payload["adapters"].append(dict(adapter, id="overlapping"))
        elif failure == "baseline":
            gate = next(gate for gate in adapter["health"] if gate["id"] == "cli-version")
            gate["expected_json"]["result.full_support_core_version"] = "0.0.1"

    _manifest(candidate, mutate)
    if failure == "absent-manifest":
        (candidate / "tools/brain-lab/compatibility.json").unlink()
    if failure == "incoherent":
        (candidate / release.VERSION_PATH).write_text("99.0.0\n")
    calls = _runner(monkeypatch)
    with pytest.raises(model.PromotionError, match=match):
        workflow.default_checks(candidate)
    assert calls == []
    assert not (candidate / "template-vault/.brain-core").is_symlink()


@pytest.mark.parametrize("declared_baseline", [True, False])
def test_compatible_candidate_keeps_contract_and_serial_gates(candidate, monkeypatch, declared_baseline):
    if not declared_baseline:
        def remove_baselines(payload):
            for adapter in payload["adapters"]:
                for gate in adapter["health"]:
                    if gate["id"] == "cli-version":
                        gate.get("expected_json", {}).pop("result.full_support_core_version", None)
        _manifest(candidate, remove_baselines)
    before = (candidate / "tools/brain-lab/compatibility.json").read_bytes()
    calls = _runner(monkeypatch)
    workflow.default_checks(candidate)
    assert len(calls) == 2
    assert calls[0][0][1] == str(candidate / "src/scripts/check_repository_contracts.py")
    assert calls[1][0][1:] == ["-m", "pytest", "-q"]
    assert all(cwd == candidate for _, cwd in calls)
    assert (candidate / "tools/brain-lab/compatibility.json").read_bytes() == before


@pytest.mark.parametrize("failed_call,message", [
    (1, "candidate repository contracts failed"),
    (2, "candidate tests failed"),
])
def test_compatible_candidate_still_refuses_verification_failures(candidate, monkeypatch, failed_call, message):
    calls = []

    def run(argv, *, cwd):
        calls.append(argv)
        return SimpleNamespace(returncode=int(len(calls) == failed_call))

    monkeypatch.setattr(workflow, "subprocess", SimpleNamespace(run=run))
    with pytest.raises(model.PromotionError, match=message):
        workflow.default_checks(candidate)
    assert len(calls) == failed_call


@pytest.mark.parametrize("owner", [
    "src/scripts/release.py",
    "tools/brain-lab/brain_lab/compatibility.py",
])
def test_preflight_uses_candidate_validation_implementation(candidate, monkeypatch, owner):
    path = candidate / owner
    with path.open("a") as stream:
        stream.write("\nraise ValueError('candidate validation owner was loaded')\n")
    calls = _runner(monkeypatch)
    with pytest.raises(model.PromotionError, match="candidate validation owner was loaded"):
        workflow.default_checks(candidate)
    assert calls == []


@pytest.mark.parametrize('baseline,latest,accepted', [
    ('{version}', True, True),
    ('{version}', False, False),
    ('older', False, True),
])
def test_preflight_renders_baselines_and_preserves_older_support(candidate, monkeypatch, baseline, latest, accepted):
    facts = release.release_facts(candidate, 'worktree')
    if latest or baseline == 'older':
        selected = facts.core if latest else '0.0.1'
        path = candidate / release.VERSION_CONTRACT_PATH
        path.write_text(path.read_text().replace(
            f'CLI_FULL_SUPPORT_CORE_VERSION = "{facts.full_support_core_version}"',
            f'CLI_FULL_SUPPORT_CORE_VERSION = "{selected}"'))
        if baseline == 'older':
            baseline = selected
    def update(payload):
        for adapter in payload['adapters']:
            for gate in adapter['health']:
                if gate['id'] == 'cli-version' and 'result.full_support_core_version' in gate.get('expected_json', {}):
                    gate['expected_json']['result.full_support_core_version'] = baseline
    _manifest(candidate, update)
    calls = _runner(monkeypatch)
    if accepted:
        workflow.default_checks(candidate)
        assert len(calls) == 2
    else:
        with pytest.raises(model.PromotionError, match='declares CLI full-support Core baseline'):
            workflow.default_checks(candidate)
        assert calls == []


def test_preflight_failure_diagnostic_is_bounded(candidate):
    from _promotion.preflight import check_lab_compatibility
    path = candidate / 'src/scripts/release.py'
    with path.open('a') as stream:
        stream.write("\nraise ValueError('x' * 20000 + 'diagnostic-tail')\n")
    with pytest.raises(model.PromotionError, match='diagnostic-tail') as failure:
        check_lab_compatibility(candidate)
    assert len(str(failure.value)) < 4300


@pytest.mark.parametrize('error,match', [('timeout', 'timed out'), ('launch', 'could not start')])
def test_preflight_process_failure_is_contextual(candidate, monkeypatch, error, match):
    import subprocess
    from _promotion import preflight
    def run(argv, **kwargs):
        assert kwargs['timeout'] == 30
        assert kwargs['stdout'] == subprocess.DEVNULL
        if error == 'timeout':
            raise subprocess.TimeoutExpired(argv, 30)
        raise OSError('launch failed')
    monkeypatch.setattr(preflight, 'subprocess', SimpleNamespace(
        run=run, DEVNULL=subprocess.DEVNULL, TimeoutExpired=subprocess.TimeoutExpired))
    with pytest.raises(model.PromotionError, match=match):
        preflight.check_lab_compatibility(candidate)
