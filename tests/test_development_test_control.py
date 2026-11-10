"""Real serial pytest ownership, interruption and surviving follow-up evidence."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import textwrap
import time

import pytest

from _test_control.records import atomic_json, outcomes, read_json, summary
from _test_control.records import input_identity
from _test_control.runner import combined, request, child_environment


pytestmark = pytest.mark.skipif(os.name != "posix", reason="Opt-in developer controller is POSIX-only")
REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "src/scripts/dev_tests.py"


CASES = '''
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
import pytest
from _test_control import checkpoint, register_cleanup

STATE = Path(os.environ['BRAIN_TEST_CONTROL_RUN'])
MODE = {mode!r}

@pytest.fixture(scope='session')
def shared():
    yield {{'busy': False}}

@pytest.fixture
def owned(shared, tmp_path):
    shared['busy'] = True
    child = None
    if MODE == 'subprocess':
        child = subprocess.Popen([sys.executable, '-u', '-c',
            "import sys; print('READY', flush=True); sys.stdin.read()"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        assert child.stdout.readline() == b'READY\\n'
    if MODE != 'missing':
        register_cleanup(lambda: not shared['busy'] and (child is None or child.poll() is not None))
    (STATE / 'scratch-path').write_text(str(tmp_path))
    try:
        yield
    finally:
        if MODE == 'hung-teardown':
            (STATE / 'teardown-entered').touch()
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            threading.Event().wait()
        if child is not None:
            child.terminate()
            child.wait(timeout=3)
        shared['busy'] = False
        (STATE / 'cleaned').touch()
        if MODE == 'failed-teardown':
            raise RuntimeError('deliberate cleanup failure')

def test_01_long(owned):
    (STATE / 'entered-from-start').touch()
    (STATE / 'ready').touch()
    if MODE == 'uncooperative':
        threading.Event().wait()
    if MODE == 'failure':
        assert False, 'genuine failure'
    if MODE == 'optional-skip':
        pytest.skip('legitimate optional applicability')
    while not (STATE / 'release').exists():
        checkpoint('waiting for release')
        time.sleep(.01)

def test_02_after(shared):
    assert not shared['busy']
    (STATE / 'next-started').touch()
'''


def source(tmp_path, mode):
    root = tmp_path / "source"
    root.mkdir()
    (root / "test_cases.py").write_text(textwrap.dedent(CASES.format(mode=mode)))
    return root


def launch(root, state, previous=None, extra=()):
    args = [sys.executable, str(SCRIPT)]
    if previous:
        args += ["follow-up", str(previous), "--state-dir", str(state)]
    else:
        args += ["run", "--root", str(root), "--state-dir", str(state), "--", "-q", *extra, "test_cases.py"]
    return subprocess.Popen(args, cwd=REPO, env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def wait_for(state, child, name, timeout=15):
    deadline = time.monotonic() + timeout
    path = state / name
    while time.monotonic() < deadline:
        if path.exists():
            return path
        if child.poll() is not None:
            out, err = child.communicate()
            raise AssertionError(f"Exited before {name}: {child.returncode}\n{out}\n{err}")
        time.sleep(.01)
    raise AssertionError(f"Missing {name}; files={list(state.glob('*'))}")


def finish(child, state, expected):
    try:
        out, err = child.communicate(timeout=20)
        assert child.returncode == expected, (out, err, list(state.glob('*')))
        return out, err
    finally:
        if child.poll() is None:
            process = state / "process.json"
            if process.exists():
                try:
                    os.killpg(read_json(process)["pytest_pid"], signal.SIGKILL)
                except ProcessLookupError:
                    pass
            child.kill()
            child.communicate(timeout=3)


@pytest.mark.parametrize("mode", ["python", "subprocess"])
def test_cooperative_wrapup_continues_and_whole_test_followup_closes(tmp_path, mode):
    root = source(tmp_path, mode)
    state = tmp_path / "first"
    child = launch(root, state, extra=("-p", "pytest_failure_log", "-p", "pytest_test_resources"))
    wait_for(state, child, "ready")
    attempt = read_json(state / "current.json")["attempt_id"]
    request(state, "interrupt", "need faster feedback")
    finish(child, state, 1)
    assert (state / "next-started").exists() and (state / "cleaned").exists()
    assert Path((state / "scratch-path").read_text()).exists()
    assert len(summary(state)["outstanding"]) == 1
    follow = tmp_path / "follow"
    child = launch(root, follow, previous=state)
    wait_for(follow, child, "ready")
    assert read_json(follow / "current.json")["attempt_id"] != attempt
    (follow / "release").touch()
    finish(child, follow, 0)
    result = combined(follow)
    assert result["complete"] and result["runs"] == 2 and result["selected"] == 2
    assert not result["release_certification"]
    assert not (follow / "next-started").exists()
    assert (follow / "entered-from-start").exists()


def test_keep_running_and_finish_current(tmp_path):
    root = source(tmp_path, "python")
    state = tmp_path / "run"
    child = launch(root, state)
    wait_for(state, child, "ready")
    assert child.poll() is None
    request(state, "finish", "leave the current useful work running")
    (state / "release").touch()
    finish(child, state, 1)
    assert (state / "cleaned").exists() and not (state / "next-started").exists()
    assert len(summary(state)["completed"]) == len(summary(state)["outstanding"]) == 1


@pytest.mark.parametrize("mode", ["uncooperative", "hung-teardown"])
def test_forced_stop_survives_without_sessionfinish(tmp_path, mode):
    root = source(tmp_path, mode)
    state = tmp_path / "run"
    child = launch(root, state)
    wait_for(state, child, "ready")
    request(state, "interrupt", "bounded stop requested")
    finish(child, state, 1)
    result = summary(state)
    assert result["scope"]["nodeids"] == ["test_cases.py::test_01_long", "test_cases.py::test_02_after"]
    assert result["receipt"]["escalated"] and result["receipt"]["reaped"]
    assert result["receipt"]["requires_full_rerun"]
    assert not (state / "next-started").exists()
    assert not combined(state)["complete"]
    if mode == "hung-teardown":
        assert not (state / "session.json").exists()


def test_missing_cleanup_proof_stops_and_source_change_refuses_followup(tmp_path):
    root = source(tmp_path, "missing")
    state = tmp_path / "run"
    child = launch(root, state)
    wait_for(state, child, "ready")
    request(state, "interrupt", "stop")
    finish(child, state, 1)
    assert not (state / "next-started").exists()
    assert summary(state)["receipt"]["requires_full_rerun"]
    with (root / "test_cases.py").open("a") as stream:
        stream.write("\n# changed actual input\n")
    follow = tmp_path / "follow"
    child = launch(root, follow, previous=state)
    _, err = finish(child, follow, 2)
    assert "Inputs changed" in err and not follow.exists()


@pytest.mark.parametrize("mode", ["failure", "failed-teardown"])
def test_genuine_failures_cannot_be_erased_by_followup(tmp_path, mode):
    root = source(tmp_path, mode)
    state = tmp_path / "run"
    child = launch(root, state)
    wait_for(state, child, "entered-from-start")
    if mode == "failed-teardown":
        request(state, "interrupt", "stop")
    finish(child, state, 1)
    assert summary(state)["failed"]
    follow = tmp_path / "follow"
    child = launch(root, follow, previous=state)
    _, err = finish(child, follow, 2)
    assert "Genuine test failures" in err


def test_legitimate_skip_is_not_an_operator_interruption(tmp_path):
    root = source(tmp_path, "optional-skip")
    state = tmp_path / "run"
    child = launch(root, state)
    finish(child, state, 0)
    assert combined(state)["complete"] and not summary(state)["outstanding"]


def test_torn_record_never_credits_pass_and_foreign_record_refused(tmp_path):
    scope = {"run_id": "run", "identity": "inputs", "nodeids": ["a", "b"]}
    good = {"run_id": "run", "identity": "inputs", "nodeid": "a", "attempt_id": "one", "outcome": "passed",
            "phases": {"setup": "passed", "call": "passed", "teardown": "passed"}}
    path = tmp_path / "outcomes.jsonl"
    path.write_text(json.dumps(good) + '\n{"nodeid":"b"')
    assert set(outcomes(tmp_path, scope)) == {"a"}
    path.write_text(json.dumps({**good, "identity": "foreign"}) + "\n")
    with pytest.raises(ValueError, match="foreign"):
        outcomes(tmp_path, scope)


@pytest.mark.parametrize("phases", [None, {}, {"setup": "passed", "call": "failed", "teardown": "passed"},
                                    {"setup": "passed", "call": "passed"}])
def test_pass_requires_consistent_complete_phase_evidence(tmp_path, phases):
    scope = {"run_id": "run", "identity": "inputs", "nodeids": ["a"]}
    record = {"run_id": "run", "identity": "inputs", "nodeid": "a", "attempt_id": "one",
              "outcome": "passed", "phases": phases}
    (tmp_path / "outcomes.jsonl").write_text(json.dumps(record) + "\n")
    with pytest.raises(ValueError, match="phase"):
        outcomes(tmp_path, scope)


@pytest.mark.parametrize("phases", [{"setup": "skipped", "teardown": "passed"},
                                    {"setup": "passed", "call": "skipped", "teardown": "passed"}])
def test_legitimate_skip_phase_shapes_are_preserved(tmp_path, phases):
    scope = {"run_id": "run", "identity": "inputs", "nodeids": ["a"]}
    record = {"run_id": "run", "identity": "inputs", "nodeid": "a", "attempt_id": "one",
              "outcome": "skipped", "phases": phases}
    (tmp_path / "outcomes.jsonl").write_text(json.dumps(record) + "\n")
    assert outcomes(tmp_path, scope)["a"]["outcome"] == "skipped"


def test_declared_directory_inputs_include_contents_and_reject_symlink_traversal(tmp_path):
    root = source(tmp_path, "python")
    external = tmp_path / "data"
    external.mkdir()
    data = external / "data.txt"
    data.write_text("before")
    env = child_environment(root)
    before = input_identity(root, [str(external)], env)
    data.write_text("after")
    assert input_identity(root, [str(external)], env) != before
    (external / "linked").symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinked"):
        input_identity(root, [str(external)], env)


@pytest.mark.parametrize("options", [("-p", "xdist.plugin", "-n", "2"),
    ("-p", "xdist.looponfail", "-f"), ("-p", "xdist.plugin", "--dist=load", "--tx=popen")])
def test_unsupported_worker_modes_refused_before_execution(tmp_path, options):
    root = source(tmp_path, "python")
    state = tmp_path / "run"
    child = launch(root, state, extra=options)
    finish(child, state, 1)
    assert not (state / "ready").exists()
    assert not (state / "scope.json").exists()
    assert "serial pytest" in (state / "stderr.log").read_text()


def test_collection_error_cannot_disappear_in_successful_followup(tmp_path):
    root = source(tmp_path, "python")
    (root / "bad.py").write_text("this is a syntax error !!!")
    state = tmp_path / "first"
    child = launch(root, state, extra=("--continue-on-collection-errors", "bad.py"))
    wait_for(state, child, "ready")
    (state / "release").touch()
    finish(child, state, 1)
    assert len(summary(state)["completed"]) == 2
    assert summary(state)["receipt"]["run_error"] and not combined(state)["complete"]
    follow = tmp_path / "follow"
    child = launch(root, follow, previous=state)
    wait_for(follow, child, "ready")
    (follow / "release").touch()
    finish(child, follow, 1)
    assert combined(follow)["runs"] == 1 and not combined(follow)["complete"]


@pytest.mark.parametrize("hook", ["pytest_sessionfinish", "pytest_unconfigure"])
def test_session_error_after_interruption_requires_fresh_verification(tmp_path, hook):
    root = source(tmp_path, "python")
    (root / "fault_plugin.py").write_text(textwrap.dedent(f'''
        import os
        from pathlib import Path
        def {hook}(**kwargs):
            state = Path(os.environ['BRAIN_TEST_CONTROL_RUN'])
            if not (state / 'disable-fault').exists():
                raise RuntimeError('shared session cleanup failed')
    '''))
    state = tmp_path / "first"
    child = launch(root, state, extra=("-p", "fault_plugin"))
    wait_for(state, child, "ready")
    request(state, "interrupt", "need feedback")
    finish(child, state, 1)
    assert "shared session cleanup failed" in (state / "stderr.log").read_text()
    assert not (state / "session.json").exists()
    assert summary(state)["receipt"]["requires_full_rerun"]
    follow = tmp_path / "follow"
    child = launch(root, follow, previous=state)
    wait_for(follow, child, "ready")
    (follow / "disable-fault").touch()
    (follow / "release").touch()
    finish(child, follow, 0)
    assert combined(follow)["complete"] and combined(follow)["runs"] == 1
    assert (follow / "next-started").exists()


def test_unsafe_interruption_keeps_deadline_through_session_cleanup(tmp_path):
    root = source(tmp_path, "missing")
    (root / "conftest.py").write_text(textwrap.dedent('''
        import os
        import signal
        import threading
        from pathlib import Path
        import pytest
        @pytest.fixture(scope='session', autouse=True)
        def session_cleanup():
            yield
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            (Path(os.environ['BRAIN_TEST_CONTROL_RUN']) / 'session-cleanup').touch()
            threading.Event().wait()
    '''))
    state = tmp_path / "first"
    child = launch(root, state)
    wait_for(state, child, "ready")
    request(state, "interrupt", "bounded wrap-up")
    wait_for(state, child, "session-cleanup")
    assert read_json(state / "current.json")["stop_pending"]
    finish(child, state, 1)
    receipt = summary(state)["receipt"]
    assert receipt["escalated"] and receipt["reaped"] and receipt["requires_full_rerun"]


@pytest.mark.parametrize("receipt", [[], {}, {"run_id": "foreign", "returncode": 0,
    "escalated": False, "cleanup_uncertain": False, "inputs_changed": False,
    "requires_full_rerun": False, "run_error": False}])
def test_foreign_or_incomplete_receipt_cannot_credit_evidence(tmp_path, receipt):
    atomic_json(tmp_path / "launch.json", {"run_id": "actual", "identity": "inputs"})
    atomic_json(tmp_path / "scope.json", {"run_id": "actual", "identity": "inputs", "nodeids": ["node"]})
    (tmp_path / "outcomes.jsonl").write_text(json.dumps({"run_id": "actual", "identity": "inputs",
        "nodeid": "node", "attempt_id": "attempt", "outcome": "passed",
        "phases": {"setup": "passed", "call": "passed", "teardown": "passed"}}) + "\n")
    atomic_json(tmp_path / "receipt.json", receipt)
    with pytest.raises(ValueError, match="receipt"):
        combined(tmp_path)


def test_controller_termination_owns_cleanup_and_preserves_receipt(tmp_path):
    root = source(tmp_path, "python")
    state = tmp_path / "run"
    child = launch(root, state)
    wait_for(state, child, "ready")
    child.terminate()
    finish(child, state, 130)
    receipt = read_json(state / "receipt.json")
    assert receipt["escalated"] and receipt["reaped"]
    assert receipt["requires_full_rerun"] and summary(state)["scope"]


def test_stale_requests_and_cleanup_unwinding_cannot_interrupt_new_attempt(tmp_path):
    from types import SimpleNamespace
    from _test_control.client import CURRENT, checkpoint
    from _test_control.plugin import Attempt
    item = SimpleNamespace(nodeid="node")
    attempt = Attempt(item, {"run_id": "run"}, tmp_path)
    attempt.phase = "call"
    atomic_json(tmp_path / "request.json", {"run_id": "run", "nodeid": "node", "attempt_id": "stale",
        "action": "interrupt", "reason": "old request"})
    token = CURRENT.set(attempt)
    try:
        assert checkpoint()
        atomic_json(tmp_path / "request.json", {"run_id": "run", "nodeid": "node", "attempt_id": attempt.attempt_id,
            "action": "interrupt", "reason": "new request"})
        try:
            raise AssertionError("original failure")
        except AssertionError:
            assert checkpoint() is False
        attempt.phase = "teardown"
        assert checkpoint()
        assert not attempt.interrupted
    finally:
        CURRENT.reset(token)


def test_forced_stop_during_append_preserves_prior_pass_and_followup_scope(tmp_path):
    root = source(tmp_path, "python")
    tests = root / "test_cases.py"
    tests.write_text(tests.read_text().replace("def test_01_long(owned):", "def test_00_fast():\n    assert True\n\ndef test_01_long(owned):"))
    (root / "fault_plugin.py").write_text(textwrap.dedent('''
        import json
        import signal
        import threading
        import pytest
        from _test_control import plugin
        @pytest.hookimpl(trylast=True)
        def pytest_configure(config):
            original = plugin.append_outcome
            def append(directory, record):
                if record['nodeid'].endswith('test_01_long') and not (directory / 'disable-fault').exists():
                    with (directory / 'outcomes.jsonl').open('ab', buffering=0) as stream:
                        payload = json.dumps(record).encode()
                        stream.write(payload[:len(payload)//2])
                    signal.signal(signal.SIGINT, signal.SIG_IGN)
                    (directory / 'append-hung').touch()
                    threading.Event().wait()
                original(directory, record)
            plugin.append_outcome = append
    '''))
    state = tmp_path / "first"
    child = launch(root, state, extra=("-p", "fault_plugin"))
    wait_for(state, child, "ready")
    (state / "release").touch()
    wait_for(state, child, "append-hung")
    request(state, "interrupt", "stop blocked record write")
    finish(child, state, 1)
    initial = summary(state)
    assert initial["completed"] == ["test_cases.py::test_00_fast"]
    assert initial["outstanding"] == ["test_cases.py::test_01_long", "test_cases.py::test_02_after"]
    assert not (state / "session.json").exists()
    follow = tmp_path / "follow"
    child = launch(root, follow, previous=state)
    wait_for(follow, child, "ready")
    (follow / "disable-fault").touch()
    (follow / "release").touch()
    finish(child, follow, 0)
    assert combined(follow)["complete"] and combined(follow)["selected"] == 3
    # Unknown shared cleanup after escalation requires a fresh selected-scope run.
    assert combined(follow)["runs"] == 1
