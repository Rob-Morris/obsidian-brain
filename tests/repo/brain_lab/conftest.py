from __future__ import annotations

import sys
import os
from pathlib import Path
import pytest


TOOL_ROOT = Path(__file__).resolve().parents[3] / "tools" / "brain-lab"
if str(TOOL_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOL_ROOT))


def write_fake_docker(tmp_path: Path, commands: str) -> Path:
    executable = tmp_path / "docker"
    executable.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
if args[:2] == ['context', 'inspect']:
    if os.environ.get('BAD_CONTEXT'):
        print('missing context', file=sys.stderr)
        sys.exit(1)
    context = os.environ.get('DOCKER_CONTEXT', 'desktop-linux')
    host = os.environ.get('DOCKER_HOST') or ('unix:///context/' + context if context != 'desktop-linux' else 'unix:///configured-desktop')
    print(json.dumps({'Name': context, 'Endpoints': {'docker': {'Host': host}}}))
    sys.exit(0)
if args[0] == 'info':
    print('different-daemon' if os.environ.get('MISMATCH') and 'brain-lab-docker-' in os.environ.get('DOCKER_CONFIG', '') else 'original-daemon')
    sys.exit(0)
if args[0] == 'version':
    print(json.dumps({'Client': {'Version': '29.7.2'}, 'Server': {'Version': '29.7.2'}}))
    sys.exit(0)
''' + commands, encoding="utf-8")
    executable.chmod(0o755)
    return executable


@pytest.fixture
def docker(tmp_path, monkeypatch):
    executable = write_fake_docker(tmp_path, '''
config = json.loads((Path(os.environ['DOCKER_CONFIG']) / 'config.json').read_text())
Path(os.environ['CALL_LOG']).write_text(json.dumps({'config': config, 'environment': dict(os.environ)}))
print(os.environ.get('OUTPUT', 'ok'))
print(os.environ.get('OUTPUT', ''), file=sys.stderr)
''')
    monkeypatch.setenv("CALL_LOG", str(tmp_path / "call.json"))
    for key in tuple(os.environ):
        if key.startswith(("DOCKER_", "BUILDX_", "BUILDKIT_")):
            monkeypatch.delenv(key)
    return executable
