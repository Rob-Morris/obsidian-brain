"""A quiet test process publishes diagnostics before the remaining tests finish."""
import os
from pathlib import Path
import subprocess
import sys
import time


def run_pytest(tmp_path, content):
    (tmp_path / 'test_sample.py').write_text(content, encoding='utf-8')
    configuration = tmp_path / 'pytest.ini'
    configuration.write_text('[pytest]\n', encoding='utf-8')
    log = tmp_path / 'failure.log'
    env = {**os.environ, 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1',
           'PYTHONPATH': str(Path(__file__).parent)}
    process = subprocess.Popen([sys.executable, '-m', 'pytest', '-c', str(configuration),
        '-p', 'pytest_failure_log', '-q', '--basetemp', str(tmp_path / 'child-temp'),
        '--failure-log', str(log), str(tmp_path / 'test_sample.py')],
        cwd=tmp_path, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return process, log


def test_quiet_failures_are_logged_before_run_completion(tmp_path):
    process, log = run_pytest(tmp_path, '''
import os
from pathlib import Path
import pytest

@pytest.fixture
def bad_setup():
    raise RuntimeError('setup-diagnostic')

@pytest.fixture
def bad_teardown():
    yield
    raise RuntimeError('teardown-diagnostic')

def test_1_setup(bad_setup):
    pass

def test_2_call():
    print('captured-output')
    raise RuntimeError('call-diagnostic')

def test_3_teardown(bad_teardown):
    pass

def test_4_wait():
    Path('ready').touch()
    assert os.read(0, 1) == b'x'
''')
    try:
        deadline = time.monotonic() + 15
        while not (tmp_path / 'ready').exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.01)
        assert (tmp_path / 'ready').exists(), 'child did not reach its explicitly blocked final test'
        assert process.poll() is None
        text = log.read_text(encoding='utf-8')
        for expected in ('[setup]', '[call]', '[teardown]', 'setup-diagnostic',
                         'call-diagnostic', 'teardown-diagnostic', 'captured-output'):
            assert expected in text
        stdout, stderr = process.communicate(b'x', timeout=15)
        assert process.returncode == 1, (stdout, stderr)
        assert str(log).encode() in stdout
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


def test_collection_failure_is_logged(tmp_path):
    process, log = run_pytest(tmp_path, "raise RuntimeError('collection-diagnostic')\n")
    try:
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 2, (stdout, stderr)
        text = log.read_text(encoding='utf-8')
        assert '[collect]' in text and 'collection-diagnostic' in text
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
