"""Pytest lifecycle adapter loaded only by the opt-in contributor controller."""
from __future__ import annotations

import os
from pathlib import Path
import time
import uuid

import pytest

from .client import CURRENT, OperatorInterrupted
from .records import RUN_ENV, append_outcome, atomic_json, read_json, terminal_outcome


class Attempt:
    def __init__(self, item, launch, directory):
        self.item, self.launch, self.directory = item, launch, directory
        self.attempt_id = uuid.uuid4().hex
        self.started = time.time()
        self.phase = "setup"
        self.progress = None
        self.reports = {}
        self.interrupted = False
        self.reason = None
        self.cleanup_checks = []
        self.stop_pending = False
        self.publish()

    def publish(self):
        atomic_json(self.directory / "current.json", {
            "run_id": self.launch["run_id"], "attempt_id": self.attempt_id,
            "nodeid": self.item.nodeid, "phase": self.phase, "started": self.started,
            "updated": time.time(),
            "progress": self.progress, "interrupted": self.interrupted,
            "stop_pending": self.stop_pending})

    def request(self):
        path = self.directory / "request.json"
        if not path.exists():
            return None
        value = read_json(path)
        if (value.get("run_id"), value.get("attempt_id"), value.get("nodeid")) != (
                self.launch["run_id"], self.attempt_id, self.item.nodeid):
            return None
        return value

    def checkpoint(self, progress):
        if progress is not None and progress != self.progress:
            self.progress = str(progress)[:512]
            self.publish()
        if self.phase != "call":
            return
        command = self.request()
        if command and command.get("action") == "interrupt":
            self.interrupted = True
            self.reason = command["reason"]
            self.publish()
            raise OperatorInterrupted(self.reason)


def _require_serial(config):
    if (getattr(config.option, "numprocesses", None) or hasattr(config, "workerinput")
            or getattr(config.option, "looponfail", False) or getattr(config.option, "tx", None)
            or getattr(config.option, "dist", "no") != "no"):
        raise pytest.UsageError("Controlled tests require serial pytest; xdist is unsupported")


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_cmdline_main(config):
    # xdist's loop-on-fail replaces the command before normal configuration.
    _require_serial(config)
    return (yield)


def pytest_configure(config):
    value = os.environ.get(RUN_ENV)
    if not value:
        raise pytest.UsageError("Load test control through dev_tests.py run")
    _require_serial(config)
    config._control_directory = Path(value)
    config._control_launch = read_json(config._control_directory / "launch.json")


def pytest_collection_finish(session):
    launch = session.config._control_launch
    nodes = [item.nodeid for item in session.items]
    if len(nodes) != len(set(nodes)):
        raise pytest.UsageError("Controlled selection contains duplicate test identities")
    atomic_json(session.config._control_directory / "scope.json", {
        "run_id": launch["run_id"], "identity": launch["identity"],
        "nodeids": nodes})


def pytest_collection_modifyitems(config, items):
    only = config._control_launch.get("only")
    if only is not None:
        selected = set(only)
        missing = selected - {item.nodeid for item in items}
        if missing:
            raise pytest.UsageError("Follow-up selection changed: " + ", ".join(sorted(missing)))
        removed = [item for item in items if item.nodeid not in selected]
        items[:] = [item for item in items if item.nodeid in selected]
        config.hook.pytest_deselected(items=removed)


@pytest.hookimpl(wrapper=True)
def pytest_make_collect_report(collector):
    report = yield
    if report.failed:
        atomic_json(collector.config._control_directory / "run-error.json", {
            "phase": "collection", "nodeid": report.nodeid[:1024]})
    return report


@pytest.hookimpl(wrapper=True)
def pytest_runtest_protocol(item, nextitem):
    config = item.config
    attempt = Attempt(item, config._control_launch, config._control_directory)
    token = CURRENT.set(attempt)
    try:
        result = yield
        return result
    finally:
        try:
            failed = any(r == "failed" for r in attempt.reports.values())
            complete = set(attempt.reports) == {"setup", "call", "teardown"}
            cleanup_ok = False
            if attempt.interrupted and not failed and complete:
                try:
                    cleanup_ok = bool(attempt.cleanup_checks) and all(check() is True for check in attempt.cleanup_checks)
                except Exception:
                    cleanup_ok = False
            outcome = terminal_outcome(attempt.reports, attempt.interrupted)
            append_outcome(attempt.directory, {
                "run_id": attempt.launch["run_id"], "identity": attempt.launch["identity"],
                "attempt_id": attempt.attempt_id, "nodeid": item.nodeid,
                "outcome": outcome, "phases": attempt.reports, "reason": attempt.reason,
                "cleanup_confirmed": cleanup_ok})
            command = attempt.request()
            attempt.stop_pending = attempt.interrupted and (not cleanup_ok or nextitem is None
                or bool(item.session.shouldstop) or bool(item.session.shouldfail))
            attempt.phase = "finished"
            attempt.publish()
            if attempt.interrupted:
                config._control_incomplete = True
                if not cleanup_ok:
                    item.session.shouldstop = "Operator interruption: cleanup unconfirmed; stopping whole run"
            if command and command.get("action") == "finish":
                item.session.shouldstop = "Operator requested finish-current then stop"
        finally:
            CURRENT.reset(token)


def _phase(item, phase):
    attempt = CURRENT.get()
    if attempt is not None:
        attempt.phase = phase
        attempt.publish()


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    _phase(item, "setup")


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_call(item):
    _phase(item, "call")


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_teardown(item):
    _phase(item, "teardown")


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    attempt = CURRENT.get()
    if attempt is not None:
        if attempt.interrupted and call.excinfo is not None and isinstance(call.excinfo.value, OperatorInterrupted):
            report.outcome = "skipped"
            report.longrepr = (str(item.path), 0, "Operator interrupted: " + str(call.excinfo.value))
        attempt.reports[report.when] = report.outcome
    return report


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    if getattr(session.config, "_control_incomplete", False) and session.exitstatus == 0:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
