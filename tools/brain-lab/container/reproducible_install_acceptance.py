#!/usr/bin/env python3
"""Exercise fresh source-selected installation inside an owned bare Lab run."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def run(argv, *, cwd=None, expected=(0,), env=None):
    result = subprocess.run([str(x) for x in argv], cwd=cwd, env=env, capture_output=True, text=True, timeout=600)
    if result.returncode not in expected:
        raise RuntimeError(f'{argv}: exit {result.returncode}\n{result.stdout[-4000:]}\n{result.stderr[-4000:]}')
    return result


def manifest(root):
    return {str(p.relative_to(root)): ('link:' + os.readlink(p) if p.is_symlink() else hashlib.sha256(p.read_bytes()).hexdigest())
            for p in sorted(root.rglob('*')) if p.is_symlink() or p.is_file()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    args = parser.parse_args()
    args.work.mkdir()
    binary = Path.home() / '.local/bin/brain'
    if binary.exists():
        raise RuntimeError('Fresh-bootstrap acceptance requires a bare base, not a prepared baseline')
    # Build a controlled Git origin from the captured product, using the real resolver transport.
    origin = args.work / 'origin'
    shutil.copytree(args.source, origin, symlinks=True, ignore=shutil.ignore_patterns('.git', '__pycache__'))
    run(['git', 'init', '-q', '-b', 'main'], cwd=origin)
    run(['git', 'config', 'user.name', 'Brain Lab'], cwd=origin)
    run(['git', 'config', 'user.email', 'lab@example.invalid'], cwd=origin)
    run(['git', 'add', '.'], cwd=origin)
    run(['git', 'commit', '-qm', 'Captured acceptance release'], cwd=origin)
    version = (origin / 'src/brain-core/VERSION').read_text().strip()
    run(['git', 'tag', '-a', 'v' + version, '-m', 'Acceptance'], cwd=origin)
    sys.path.insert(0, str(origin / 'cli'))
    from _acquisition import acquire, verify_acquired
    exact = acquire(version, args.work / 'exact', repository=str(origin))
    latest = acquire('latest', args.work / 'latest', repository=str(origin))
    if exact.receipt != latest.receipt:
        raise RuntimeError('Latest and exact selected different identities')
    verify_acquired(exact.source_root)
    first, second = args.work / 'brain-a', args.work / 'brain-b'
    for target in (first, second):
        run(['bash', exact.source_root / 'install.sh', '--non-interactive', '--skip-mcp', target], cwd=args.work)
        recorded = json.loads((target / '.brain/local/install-source.json').read_text())
        if recorded != exact.receipt:
            raise RuntimeError('Installed Core source receipt differs from selected source')
        if target == first:
            distribution = manifest(Path.home() / '.local/lib/brain-cli')
            executable = binary.read_bytes()
            first_tree = manifest(first)
        elif (manifest(Path.home() / '.local/lib/brain-cli') != distribution or binary.read_bytes() != executable
              or manifest(first) != first_tree):
            raise RuntimeError('Second Brain install changed shared CLI or first Brain')
    outside = args.work / 'outside'
    outside.mkdir()
    facts = json.loads(run([binary, 'version', '--json'], cwd=outside).stdout)['result']
    if facts['distributed_with_core_version'] != version or 'full_support_core_version' not in facts:
        raise RuntimeError('Offline version fields differ from installed bundle')
    # A controlled failing bootstrap must not reach target creation.
    damaged = args.work / 'bootstrap-failure-source'
    shutil.copytree(origin, damaged, symlinks=True, ignore=shutil.ignore_patterns('.git', '__pycache__'))
    (damaged / 'cli/_distribution.py').write_text('raise SystemExit(1)\n')
    failed_target = args.work / 'must-not-exist'
    failure = run(['bash', damaged / 'install.sh', '--non-interactive', '--skip-mcp', failed_target],
                  cwd=args.work, expected=(1,))
    if failed_target.exists() or 'CLI bootstrap failed before target-vault creation' not in failure.stderr:
        raise RuntimeError('Bootstrap failure created a vault or omitted remediation')
    print(json.dumps({'status': 'passed', 'source': exact.receipt, 'brains': [str(first), str(second)],
                      'cli': facts, 'checks': ['bare-bootstrap', 'real-git-latest-exact', 'selected-source-receipt',
                      'two-brain-cli-preservation', 'offline-fields', 'bootstrap-failure-before-vault']}))


if __name__ == '__main__':
    main()
