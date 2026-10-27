"""Exercise scratch retention and real-resource abort decisions in child pytest."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


def run_sample(root, content, conftest="", extra=()):
    (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (root / "test_sample.py").write_text(content, encoding="utf-8")
    (root / "conftest.py").write_text(conftest, encoding="utf-8")
    env = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
           "PYTHONPATH": str(Path(__file__).parent)}
    return subprocess.run([
        sys.executable, "-m", "pytest", "-q", "-c", str(root / "pytest.ini"),
        "-p", "pytest_failure_log", "-p", "pytest_test_resources",
        *extra,
        "--basetemp", str(root / "scratch"), "--failure-log", str(root / "failure.log"),
        str(root / "test_sample.py"),
    ], cwd=root, env=env, capture_output=True, text=True, timeout=30)


def test_cleanup_waits_for_all_phases_and_keeps_each_failure(tmp_path):
    result = run_sample(tmp_path, '''
from pathlib import Path
import pytest

def record(tmp_path):
    (tmp_path / 'evidence').write_text('keep on failure')
    Path(tmp_path.name + '.path').write_text(str(tmp_path))

@pytest.fixture
def bad_setup(tmp_path):
    record(tmp_path)
    raise RuntimeError('setup failed')

@pytest.fixture
def bad_teardown(tmp_path):
    record(tmp_path)
    yield
    raise RuntimeError('teardown failed')

def test_1_pass(tmp_path):
    record(tmp_path)

def test_2_observe_cleanup():
    previous = Path(next(Path('.').glob('test_1_pass*.path')).read_text())
    assert not previous.exists()

def test_3_call(tmp_path):
    record(tmp_path)
    assert False, 'call failed'

def test_4_setup(bad_setup):
    pass

def test_5_teardown(bad_teardown):
    pass
''')
    assert result.returncode == 1, (result.stdout, result.stderr)
    for marker in tmp_path.glob("*.path"):
        directory = Path(marker.read_text())
        assert directory.exists() == (not directory.name.startswith("test_1_pass"))
    assert "1 failed, 3 passed, 2 errors" in result.stdout


def test_injected_capacity_failure_does_not_abort_a_healthy_filesystem(tmp_path):
    result = run_sample(tmp_path, '''
import errno
from pathlib import Path

def test_1_simulated(tmp_path):
    raise OSError(errno.ENOSPC, 'No space left on device')

def test_2_continues():
    Path('continued').touch()
''')
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert (tmp_path / "continued").exists()
    assert "Confirmed test filesystem exhaustion" not in result.stdout


@pytest.mark.parametrize("phase", ["setup", "call", "teardown"])
def test_confirmed_exhaustion_stops_after_evidence_and_teardown(tmp_path, phase):
    result = run_sample(tmp_path, f"phase = {phase!r}\n" + '''
import errno
from pathlib import Path
import pytest

@pytest.fixture
def resource(tmp_path):
    (tmp_path / 'evidence').touch()
    try:
        if phase == 'setup':
            raise OSError(errno.EDQUOT, 'Disk quota exceeded')
        yield
    finally:
        Path('torn-down').touch()
        if phase == 'teardown':
            raise OSError(errno.EDQUOT, 'Disk quota exceeded')

def test_1_exhausted(resource):
    if phase == 'call':
        raise OSError(errno.EDQUOT, 'Disk quota exceeded')

def test_2_not_run():
    Path('continued').touch()
''', '''
import errno
import pytest_test_resources

def pytest_configure():
    def exhausted(**kwargs):
        raise OSError(errno.EDQUOT, 'Disk quota exceeded')
    pytest_test_resources.TemporaryFile = exhausted
''')
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert (tmp_path / "torn-down").exists()
    assert not (tmp_path / "continued").exists()
    assert list((tmp_path / "scratch").glob("*/evidence"))
    assert "Disk quota exceeded" in (tmp_path / "failure.log").read_text()
    assert "Remaining tests were not run" in result.stdout + result.stderr


def test_interruption_retains_scratch(tmp_path):
    result = run_sample(tmp_path, '''
def test_interrupt(tmp_path):
    (tmp_path / 'evidence').touch()
    raise KeyboardInterrupt()
''')
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert list((tmp_path / "scratch").glob("*/evidence"))


def test_grouped_teardown_exhaustion_is_confirmed(tmp_path):
    result = run_sample(tmp_path, '''
import errno
import pytest
from pathlib import Path
@pytest.fixture
def first(tmp_path):
    (tmp_path / 'evidence').touch()
    yield
    raise OSError(errno.ENOSPC, 'capacity fault')
@pytest.fixture
def second():
    yield
    raise RuntimeError('independent teardown fault')
def test_1_group(first, second):
    pass
def test_2_not_run():
    Path('continued').touch()
''', '''
import errno
import pytest_test_resources
def pytest_configure():
    def exhausted(**kwargs):
        raise OSError(errno.ENOSPC, 'probe capacity fault')
    pytest_test_resources.TemporaryFile = exhausted
''')
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert not (tmp_path / 'continued').exists()
    evidence = (tmp_path / 'failure.log').read_text()
    assert 'independent teardown fault' in evidence and 'capacity fault' in evidence
    assert 'Confirmed test filesystem exhaustion' in result.stdout + result.stderr


def test_distributed_stop_drains_other_worker_failure_and_finalizer(tmp_path):
    result = run_sample(tmp_path, '''
import errno
from pathlib import Path
import time
import pytest
def wait_for(name):
    deadline = time.monotonic() + 15
    while not Path(name).exists():
        assert time.monotonic() < deadline, name
        time.sleep(.01)
@pytest.fixture
def cleanup():
    yield
    Path('other-finalizer').touch()
def test_1_capacity(tmp_path):
    wait_for('other-started')
    raise OSError(errno.ENOSPC, 'No space left on device')
def test_2_other(cleanup):
    Path('other-started').touch()
    wait_for('stop-observed')
    raise RuntimeError('other-worker-failure')
''', '''
import errno
from pathlib import Path
import pytest_test_resources
def pytest_configure(config):
    global controller
    controller = not hasattr(config, 'workerinput')
    def exhausted(**kwargs):
        raise OSError(errno.ENOSPC, 'probe capacity fault')
    pytest_test_resources.TemporaryFile = exhausted
def pytest_runtest_logreport(report):
    if controller and any(name == 'brain_storage_exhausted' for name, value in report.user_properties):
        Path('stop-observed').touch()
''', extra=('-p', 'xdist.plugin', '-n', '2'))
    assert result.returncode != 0, (result.stdout, result.stderr)
    assert (tmp_path / 'other-finalizer').exists()
    assert 'other-worker-failure' in (tmp_path / 'failure.log').read_text()
    assert 'Confirmed test filesystem exhaustion' in result.stdout + result.stderr
