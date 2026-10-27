"""Persist failure diagnostics as reports arrive without changing console verbosity."""
from pathlib import Path
import tempfile

import pytest

from brain_test_support import STORAGE_ERRORS


def pytest_addoption(parser):
    parser.addoption('--failure-log', metavar='PATH',
                     help='Write failure diagnostics immediately to PATH (default: a unique pytest cache log).')


@pytest.hookimpl(trylast=True)
def pytest_configure(config):
    # xdist's controller receives worker reports; do not duplicate their logs.
    if hasattr(config, 'workerinput'):
        return
    requested = config.getoption('failure_log')
    if requested:
        path = Path(requested)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('', encoding='utf-8')
    else:
        import os
        descriptor, name = tempfile.mkstemp(prefix='run-', suffix='.log',
                                          dir=config.cache.mkdir('failure-logs'))
        os.close(descriptor)
        path = Path(name)
    config.pluginmanager.register(FailureLog(config, path), 'brain-failure-log')


class FailureLog:
    def __init__(self, config, path):
        self.config, self.path = config, path

    def pytest_sessionstart(self):
        terminal = self.config.pluginmanager.getplugin('terminalreporter')
        if terminal is not None:
            terminal.write_line(f'Failure log: {self.path}')

    def _record(self, report):
        if not report.failed:
            return
        # Closing each append makes the complete report available to a tailer
        # before the next test begins, including when this run is interrupted.
        try:
            with self.path.open('a', encoding='utf-8') as stream:
                stream.write(f'\n=== {report.nodeid} [{getattr(report, "when", "collection")}] ===\n')
                stream.write(str(report.longrepr) + '\n')
                for name, content in report.sections:
                    stream.write(f'--- {name} ---\n{content}\n')
        except OSError as exc:
            if exc.errno not in STORAGE_ERRORS:
                raise
            terminal = self.config.pluginmanager.getplugin('terminalreporter')
            if terminal is not None:
                terminal.write_line('Failure log unavailable: filesystem capacity exhausted; see console failure report.')

    def pytest_runtest_logreport(self, report):
        self._record(report)

    def pytest_collectreport(self, report):
        self._record(report)
