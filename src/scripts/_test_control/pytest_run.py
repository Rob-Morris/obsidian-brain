"""Confirm normal pytest return, including session and plugin finalisation."""
import os
from pathlib import Path
import sys

import pytest

from .records import RUN_ENV, atomic_json, read_json


def main():
    directory = Path(os.environ[RUN_ENV])
    launch = read_json(directory / "launch.json")
    exitstatus = pytest.main(["-p", "_test_control.plugin", *sys.argv[1:]])
    atomic_json(directory / "session.json", {"run_id": launch["run_id"], "exitstatus": int(exitstatus)})
    return exitstatus


if __name__ == "__main__":
    raise SystemExit(main())
