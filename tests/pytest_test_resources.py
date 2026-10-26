"""Bound successful test scratch while preserving complete failure evidence."""
from pathlib import Path
import re
import shutil
from tempfile import TemporaryFile
import warnings

import pytest

from brain_test_support import STORAGE_ERRORS


_PATH = pytest.StashKey[Path]()
_FAILED = pytest.StashKey[bool]()
_PHASES = pytest.StashKey[set[str]]()
_ROOT = pytest.StashKey[Path]()
_EXHAUSTED = pytest.StashKey[str]()


def pytest_configure(config):
    config.pluginmanager.register(ResourceExhaustionStop(config), "brain-resource-stop")


@pytest.fixture
def tmp_path(request, tmp_path_factory):
    """Own a test directory until its complete teardown outcome is known."""
    request.config.stash[_ROOT] = tmp_path_factory.getbasetemp()
    name = re.sub(r"[\W]", "_", request.node.name)[:30]
    path = tmp_path_factory.mktemp(name, numbered=True)
    request.node.stash[_PATH] = path
    return path


def _confirmed_storage_error(root):
    try:
        with TemporaryFile(dir=root) as stream:
            stream.write(b"\0")
            stream.flush()
    except OSError as exc:
        if exc.errno in STORAGE_ERRORS:
            return str(exc)
    return None


def _storage_failure(exception):
    pending, seen = [exception], set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, OSError) and current.errno in STORAGE_ERRORS:
            return True
        if any(text in str(current) for text in ("No space left on device", "Disk quota exceeded")):
            return True
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)
        cause = current.__cause__ or current.__context__
        if cause is not None:
            pending.append(cause)
    return False


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    phases = item.stash.setdefault(_PHASES, set())
    if report.passed:
        phases.add(report.when)
    else:
        item.stash[_FAILED] = True
    if report.failed and call.excinfo is not None:
        if _storage_failure(call.excinfo.value):
            root = item.config.stash.get(_ROOT, None)
            if root is not None:
                error = _confirmed_storage_error(root)
                if error is not None:
                    item.config.stash[_EXHAUSTED] = error
    if report.when == "teardown" and not item.stash.get(_FAILED, False):
        path = item.stash.get(_PATH, None)
        if path is not None and phases == {"setup", "call", "teardown"}:
            try:
                shutil.rmtree(path)
            except OSError as exc:
                warnings.warn(pytest.PytestWarning(f"Retained test scratch after cleanup failed: {exc}"))
    if report.when == "teardown" and _EXHAUSTED in item.config.stash:
        report.user_properties.append(("brain_storage_exhausted", item.config.stash[_EXHAUSTED]))
    return report


class ResourceExhaustionStop:
    def __init__(self, config):
        self.config = config

    @pytest.hookimpl(trylast=True)
    def pytest_runtest_logreport(self, report):
        if hasattr(self.config, "workerinput"):
            return
        for name, message in report.user_properties:
            if name == "brain_storage_exhausted":
                reason = (
                    f"Confirmed test filesystem exhaustion: {message}. "
                    "Remaining tests were not run; restore storage before retrying."
                )
                distributed = self.config.pluginmanager.getplugin("dsession")
                if distributed is not None:
                    # xdist's stop path drains reports and shuts down live workers.
                    distributed.shouldstop = reason
                else:
                    pytest.exit(reason, returncode=pytest.ExitCode.TESTS_FAILED)
