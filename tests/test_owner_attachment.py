"""Real process checks for deliberate owner inheritance and isolation."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import pytest

from _bootstrap.consent_owner import ConsentOwner, OwnerConnectionError
from _bootstrap.consent_state import StoreClosedError
from _bootstrap.owner_attachment import (
    OWNER_CHANNEL_ENV, OwnerAttachment, private_child_channel,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'src/brain-core/scripts'
pytestmark = pytest.mark.skipif(os.name != 'posix', reason='private child inheritance requires POSIX')


@pytest.fixture
def owner(tmp_path):
    instance = ConsentOwner(tmp_path)
    yield instance
    instance.close()


def environment():
    return dict(os.environ, PYTHONPATH=os.pathsep.join((str(SCRIPTS), str(ROOT / 'cli'))))


PROBE = '''import json,os,sys,subprocess
from pathlib import Path
from _bootstrap.owner_attachment import OwnerAttachment,OWNER_CHANNEL_ENV
attachment=OwnerAttachment.capture()
store=attachment.connect(Path(sys.argv[1]))
key=sys.argv[2]
for i in range(10):
    assert store.compare_exchange({key:i},{key:{"value":i}})
    assert store.snapshot((key,)).values[key]=={"value":i}
print(json.dumps({"context":store.identity.context_id,"kind":attachment.kind,
                  "locator":OWNER_CHANNEL_ENV in os.environ,"keys":list(store.list_keys().keys)}))
attachment.close()
'''


def test_job_replies_are_private_for_parallel_siblings(owner):
    attachment = OwnerAttachment.for_job(owner)
    try:
        def run(index):
            result = subprocess.run([sys.executable, '-c', PROBE, str(owner.identity.vault_root), f'child-{index}'],
                                    **attachment.forwarded_process(environment()), capture_output=True, text=True, timeout=10)
            assert result.returncode == 0, result.stderr
            return json.loads(result.stdout)
        with ThreadPoolExecutor(max_workers=6) as workers:
            replies = list(workers.map(run, range(6)))
        assert {reply['context'] for reply in replies} == {owner.identity.context_id}
        assert all(reply['kind'] == 'cli-job' and not reply['locator'] for reply in replies)
        assert len(owner.store.list_keys().keys) == 6
    finally:
        attachment.close()


def test_capture_clears_locator_and_blocks_even_close_fds_false_provider_inheritance(owner, monkeypatch):
    fd = os.dup(owner.rendezvous_fd)
    os.set_inheritable(fd, True)
    monkeypatch.setenv(OWNER_CHANNEL_ENV, f'brain.owner-state/1:job:{fd}')
    attachment = OwnerAttachment.capture()
    try:
        assert OWNER_CHANNEL_ENV not in os.environ
        assert not os.get_inheritable(fd)
        probe = '''import json,os,sys
try: os.fstat(int(sys.argv[1])); alive=True
except OSError: alive=False
print(json.dumps([alive,"BRAIN_OWNER_CHANNEL" in os.environ]))
'''
        for options in ({'close_fds': False}, {'close_fds': True, 'start_new_session': True}):
            result = subprocess.run([sys.executable, '-c', probe, str(fd)], **options,
                                    capture_output=True, text=True, timeout=5)
            assert result.returncode == 0, result.stderr
            assert json.loads(result.stdout) == [False, False]
        assert attachment.connect(Path(owner.identity.vault_root)).snapshot(()).values == {}
    finally:
        attachment.close()


def test_private_streams_survive_child_replacement_and_close_parent_duplicates(owner):
    replies = []
    for index in range(2):
        with private_child_channel(owner, environment()) as options:
            fd = options['pass_fds'][0]
            process = subprocess.Popen([sys.executable, '-c', PROBE, str(owner.identity.vault_root), f'child-{index}'],
                                       **options, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        with pytest.raises(OSError):
            os.fstat(fd)
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr
        replies.append(json.loads(stdout))
    assert [reply['context'] for reply in replies] == [owner.identity.context_id] * 2
    assert replies[1]['keys'] == ['child-0', 'child-1']


def test_failed_spawn_closes_child_end_without_closing_owner(owner):
    with pytest.raises(FileNotFoundError):
        with private_child_channel(owner) as options:
            fd = options['pass_fds'][0]
            subprocess.Popen(['/nonexistent/brain-test-program'], **options)
    with pytest.raises(OSError):
        os.fstat(fd)
    assert owner.store.snapshot(()).values == {}


@pytest.mark.parametrize('raw', ['', 'brain.owner-state/2:job:10', 'brain.owner-state/1:job:0',
                                 'brain.owner-state/1:job:-4', 'brain.owner-state/1:job:9999999999'])
def test_invalid_or_closed_locator_never_becomes_absence(raw):
    env = {OWNER_CHANNEL_ENV: raw}
    with pytest.raises(OwnerConnectionError):
        OwnerAttachment.capture(env)
    assert OWNER_CHANNEL_ENV not in env
    assert OwnerAttachment.capture({}) is None


def test_wrong_socket_type_and_selected_brain_are_rejected(owner, tmp_path):
    left, right = socket.socketpair()
    try:
        with pytest.raises(OwnerConnectionError):
            OwnerAttachment.capture({OWNER_CHANNEL_ENV: f'brain.owner-state/1:job:{os.dup(left.fileno())}'})
    finally:
        left.close()
        right.close()
    other = tmp_path / 'other'
    other.mkdir()
    attachment = OwnerAttachment.for_job(owner)
    with pytest.raises(OwnerConnectionError, match='different Brain'):
        attachment.connect(other)
    with pytest.raises(OwnerConnectionError, match='closed'):
        attachment.connect(tmp_path)


def test_stream_cannot_be_shared_with_siblings_or_rehandshaken(owner):
    with private_child_channel(owner) as options:
        fd = os.dup(options['pass_fds'][0])
        attachment = OwnerAttachment.capture({OWNER_CHANNEL_ENV: f'brain.owner-state/1:stream:{fd}'})
    try:
        with pytest.raises(OwnerConnectionError, match='forwarded'):
            attachment.forwarded_process()
        first = attachment.connect(Path(owner.identity.vault_root))
        assert attachment.connect(Path(owner.identity.vault_root)) is first
        with pytest.raises(OwnerConnectionError, match='handshake'):
            with attachment.exec_environment():
                pass
    finally:
        attachment.close()


@pytest.mark.parametrize("transport", ["job", "stream"])
def test_real_managed_exec_preserves_attachment_and_scrubs_locator(owner, tmp_path, transport):
    attachment = OwnerAttachment.for_job(owner) if transport == "job" else None
    target = tmp_path / 'after_exec.py'
    target.write_text('from _bootstrap.owner_attachment import capture_process_identity\nidentity, seed = capture_process_identity()\nassert identity.context_id == "exec-context" and seed\n' + PROBE)
    before = '''import sys
from _bootstrap.owner_attachment import OwnerAttachment
from _bootstrap.runtime import exec_managed_runtime
attachment=OwnerAttachment.capture()
exec_managed_runtime(managed_python=sys.executable,script_path=sys.argv[1],forwarded_args=sys.argv[2:],summary={},owner_attachment=attachment)
'''
    try:
        channel = (nullcontext(attachment.forwarded_process(environment())) if attachment is not None
                   else private_child_channel(owner, environment()))
        with channel as options:
            from _bootstrap.owner_attachment import PROCESS_CONTEXT_ENV, ProcessIdentity
            options["env"][PROCESS_CONTEXT_ENV] = ProcessIdentity("cli-job" if transport == "job" else "mcp-instance", "exec-context").launch_value(initialise_owner=True)
            result = subprocess.run([sys.executable, '-c', before, str(target), str(tmp_path), 'exec'],
                                    **options, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)['context'] == owner.identity.context_id
    finally:
        if attachment is not None:
            attachment.close()


def test_failed_exec_restores_non_inheritance(owner, monkeypatch):
    from _bootstrap.runtime import exec_managed_runtime
    attachment = OwnerAttachment.for_job(owner)
    fd = attachment.forwarded_process()['pass_fds'][0]
    try:
        def fail(*_args):
            assert os.get_inheritable(fd)
            raise OSError('exec rejected')
        monkeypatch.setattr(os, 'execve', fail)
        with pytest.raises(OSError, match='exec rejected'):
            exec_managed_runtime(managed_python=sys.executable, script_path='unused',
                                 forwarded_args=[], summary={}, owner_attachment=attachment)
        assert not os.get_inheritable(fd)
        assert attachment.connect(Path(owner.identity.vault_root)).snapshot(()).values == {}
    finally:
        attachment.close()


def test_closed_owner_rejects_established_and_new_connections(owner):
    attachment = OwnerAttachment.for_job(owner)
    existing = attachment.connect(Path(owner.identity.vault_root))
    owner.close()
    with pytest.raises((OwnerConnectionError, StoreClosedError)):
        existing.snapshot(())
    with pytest.raises(OwnerConnectionError):
        fd = os.dup(attachment.forwarded_process()['pass_fds'][0])
        fresh = OwnerAttachment.capture({OWNER_CHANNEL_ENV: f'brain.owner-state/1:job:{fd}'})
        try:
            fresh.connect(Path(owner.identity.vault_root))
        finally:
            fresh.close()
    attachment.close()


def test_actual_proxy_spawn_retains_owner_across_replacement(owner, tmp_path, monkeypatch):
    from brain_mcp import proxy as proxy_module

    program = tmp_path / 'child.py'
    program.write_text('''import json,os
from pathlib import Path
from _bootstrap.owner_attachment import OwnerAttachment
attachment=OwnerAttachment.capture()
store=attachment.connect(Path(os.environ["BRAIN_VAULT_ROOT"]))
snapshot=store.snapshot(("replacements",))
value=snapshot.values.get("replacements",0)+1
assert store.compare_exchange(snapshot.versions,{"replacements":value})
print(json.dumps({"context":store.identity.context_id,"replacements":value}),flush=True)
attachment.close()
''')
    monkeypatch.setenv('BRAIN_VAULT_ROOT', str(tmp_path))
    monkeypatch.setenv('PYTHONPATH', environment()['PYTHONPATH'])
    proxy = proxy_module.Proxy(sys.executable, str(program), str(tmp_path), owner=owner)
    replies = []
    try:
        for _ in range(2):
            assert proxy._start_child()
            child = proxy._get_child()
            replies.append(json.loads(child.readline()))
            assert child.wait() == 0
        assert [reply['replacements'] for reply in replies] == [1, 2]
        assert {reply['context'] for reply in replies} == {owner.identity.context_id}
    finally:
        proxy._initiate_shutdown()
    with pytest.raises(StoreClosedError):
        owner.store.snapshot(())


def test_cli_discovery_and_execution_forward_the_same_job(owner, tmp_path, monkeypatch):
    from types import SimpleNamespace
    sys.path.insert(0, str(ROOT / 'cli'))
    from _local_cli import main
    from _local_cli.discovery import ComposedCommandEntry
    from _local_cli.execution import ApplicationProcessInvoker, SelectedBrainProcess
    from _local_cli.runtime import SelectedBrain

    script = tmp_path / '.brain-core/scripts/command.py'
    script.parent.mkdir(parents=True)
    script.write_text('''import json,sys
from pathlib import Path
from _bootstrap.owner_attachment import OwnerAttachment
attachment=OwnerAttachment.capture()
root=Path(sys.argv[sys.argv.index("--vault")+1])
store=attachment.connect(root)
command=".".join(sys.argv[1:3])
assert store.compare_exchange({command:0},{command:True})
print(json.dumps({"schema":"brain.command-result/1","command":command,"command_version":1,
                  "status":"ok","result":{"context":store.identity.context_id}}))
attachment.close()
''')
    attachment = OwnerAttachment.for_job(owner)
    monkeypatch.setenv('PYTHONPATH', environment()['PYTHONPATH'])
    monkeypatch.setattr(main, 'command_python', lambda *_args: Path(sys.executable))
    try:
        common = SimpleNamespace(operator_key=None, owner_attachment=attachment)
        discovered = main._invoke_application(SelectedBrain(tmp_path, None, 'test'), 'command.describe', {}, common,
                                             dependency_tier='stdlib')
        entry = ComposedCommandEntry('application', 'brain.command-catalogue/1', 'sha256:application',
                                     'artefact.read', 1, 'Read one thing.', {'command_id': 'artefact.read', 'command_version': 1})
        invoked = ApplicationProcessInvoker(SelectedBrainProcess(tmp_path, Path(sys.executable)),
                                            owner_attachment=attachment).invoke(entry, {})
        assert discovered['result']['context'] == invoked.structured_content['result']['context'] == owner.identity.context_id
        assert owner.store.snapshot(('command.describe', 'artefact.read')).values == {'command.describe': True, 'artefact.read': True}
    finally:
        attachment.close()


def test_supervisor_pins_before_spawn_preserves_exit_and_closes_surviving_descendant(tmp_path, monkeypatch):
    from types import SimpleNamespace
    sys.path.insert(0, str(ROOT / 'cli'))
    from _local_cli.session import run_owned_job

    monkeypatch.setenv('PYTHONPATH', environment()['PYTHONPATH'])
    worker = tmp_path / 'worker.py'
    worker.write_text('''import json,os,sys,time
from pathlib import Path
from _bootstrap.owner_attachment import OwnerAttachment
from _bootstrap.consent_owner import OwnerConnectionError
attachment=OwnerAttachment.capture()
root=Path(sys.argv[1]);store=attachment.connect(root)
assert store.snapshot(("pinned",)).values=={"pinned":True}
(root/"ready").write_text(str(os.getpid()))
end=time.monotonic()+10
while not (root/"finish").exists():
    if time.monotonic()>end: raise RuntimeError("supervisor test timed out")
    time.sleep(.01)
try: store.snapshot(()); result="unexpectedly-open"
except OwnerConnectionError: result="closed"
(root/"result").write_text(result)
attachment.close()
''')
    program = tmp_path / 'root.py'
    program.write_text('''import os,subprocess,sys,time
from pathlib import Path
from _bootstrap.owner_attachment import OWNER_CHANNEL_ENV
root=Path(sys.argv[1]);fd=int(os.environ[OWNER_CHANNEL_ENV].rsplit(":",1)[1])
subprocess.Popen([sys.executable,str(root/"worker.py"),str(root)],pass_fds=(fd,),
                 stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
end=time.monotonic()+5
while not (root/"ready").exists():
    if time.monotonic()>end: raise RuntimeError("worker did not connect")
    time.sleep(.01)
raise SystemExit(7)
''')
    owners = []
    def initialise(owner):
        owners.append(owner)
        assert owner.store.compare_exchange({'pinned': 0}, {'pinned': True})
    try:
        assert run_owned_job(SimpleNamespace(vault_root=tmp_path, workspace=None),
                             [sys.executable, str(program), str(tmp_path)], initialise_owner=initialise) == 7
        with pytest.raises(StoreClosedError):
            owners[0].store.snapshot(())
        (tmp_path / 'finish').touch()
        deadline = time.monotonic() + 5
        while not (tmp_path / 'result').exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert (tmp_path / 'result').read_text() == 'closed'
    finally:
        (tmp_path / 'finish').touch()


def test_actual_provider_and_detached_launchers_do_not_inherit_attachment(owner, tmp_path, monkeypatch):
    from _bootstrap import readiness
    from _lifecycle import fresh_interpreter
    from _lifecycle.runtime_warmup import warm_semantic
    import shape_presentation

    fd = os.dup(owner.rendezvous_fd)
    monkeypatch.setenv(OWNER_CHANNEL_ENV, f'brain.owner-state/1:job:{fd}')
    attachment = OwnerAttachment.capture()
    report = tmp_path / 'provider.json'
    executable = tmp_path / 'probe'
    executable.write_text(f'''#!{sys.executable}
import json,os
from pathlib import Path
try: os.fstat({fd}); inherited=True
except OSError: inherited=False
value={{"fd":inherited,"locator":"BRAIN_OWNER_CHANNEL" in os.environ}}
report=Path({str(report)!r})
pending=report.with_name(report.name+"."+str(os.getpid())+".pending")
pending.write_text(json.dumps(value))
pending.replace(report)
print(json.dumps({{"result":value}}))
''')
    executable.chmod(0o700)
    try:
        result = fresh_interpreter.run_lifecycle_in_fresh_interpreter(warm_semantic, tmp_path,
                                                                     python_executable=executable)
        assert result == {'fd': False, 'locator': False}
        report.unlink()
        with monkeypatch.context() as patch:
            patch.setattr(readiness.sys, 'executable', str(executable))
            readiness._spawn_worker(tmp_path, 'already-admitted-work')
        deadline = time.monotonic() + 5
        while not report.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert json.loads(report.read_text()) == {'fd': False, 'locator': False}
        report.unlink()
        (tmp_path / 'marp').symlink_to(executable)
        monkeypatch.setenv('PATH', str(tmp_path) + os.pathsep + os.environ['PATH'])
        result = shape_presentation._launch_preview('already-admitted.md', None)
        assert result['status'] == 'ok'
        deadline = time.monotonic() + 5
        while not report.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert json.loads(report.read_text()) == {'fd': False, 'locator': False}
    finally:
        attachment.close()


def test_copied_locator_without_forwarded_fd_fails_explicitly(owner):
    attachment = OwnerAttachment.for_job(owner)
    try:
        options = attachment.forwarded_process(environment())
        result = subprocess.run([sys.executable, '-c', PROBE, owner.identity.vault_root, 'missing'],
                                env=options['env'], capture_output=True, text=True, timeout=5)
        assert result.returncode != 0
        assert 'private owner channel is unavailable' in result.stderr
        assert owner.store.snapshot(('missing',)).values == {}
    finally:
        attachment.close()


def test_transport_import_does_not_load_application_or_mcp_runtime():
    source = f'''import sys
sys.path.insert(0,{str(SCRIPTS)!r})
import _bootstrap.owner_attachment
assert not any(name.split(".")[0] in {{"_application","_common","mcp","pydantic","numpy"}} for name in sys.modules)
'''
    result = subprocess.run([sys.executable, '-I', '-c', source], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('termination', ['terminate', 'kill'])
def test_supervisor_signal_or_process_death_closes_surviving_job(tmp_path, termination):
    import shutil

    worker = tmp_path / 'root_probe.py'
    worker.write_text('''import signal,sys,time
from pathlib import Path
from _bootstrap.owner_attachment import OwnerAttachment
from _bootstrap.consent_owner import OwnerConnectionError
signal.signal(signal.SIGTERM,signal.SIG_IGN)
root=Path(sys.argv[1]);attachment=OwnerAttachment.capture();store=attachment.connect(root)
assert store.snapshot(("pinned",)).values=={"pinned":True}
(root/"ready").touch()
end=time.monotonic()+10
while not (root/"finish").exists():
    if time.monotonic()>end: raise RuntimeError("test did not release root probe")
    time.sleep(.01)
try: store.snapshot(()); result="unexpectedly-open"
except OwnerConnectionError: result="closed"
(root/"result").write_text(result)
attachment.close()
''')
    supervisor = '''import sys
from pathlib import Path
from types import SimpleNamespace
from _local_cli.session import run_owned_job
root=Path(sys.argv[1])
def initialise(owner):
    assert owner.store.compare_exchange({"pinned":0},{"pinned":True})
    (root/"private_directory").write_text(str(owner.private_directory))
raise SystemExit(run_owned_job(SimpleNamespace(vault_root=root,workspace=None),
                 [sys.executable,str(root/"root_probe.py"),str(root)],initialise_owner=initialise))
'''
    process = subprocess.Popen([sys.executable, '-c', supervisor, str(tmp_path)], env=environment(),
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 5
        while not (tmp_path / 'ready').exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert (tmp_path / 'ready').exists(), f'supervisor failed before child attachment: {process.poll()}'
        getattr(process, termination)()
        assert process.wait(timeout=5) == (143 if termination == 'terminate' else -9)
        (tmp_path / 'finish').touch()
        deadline = time.monotonic() + 5
        while not (tmp_path / 'result').exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert (tmp_path / 'result').read_text() == 'closed'
    finally:
        (tmp_path / 'finish').touch()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        private = tmp_path / 'private_directory'
        if private.exists():
            shutil.rmtree(private.read_text(), ignore_errors=True)


def test_initial_owner_storage_failure_keeps_child_usable_with_sanitised_reason(tmp_path, monkeypatch):
    from brain_mcp import proxy as proxy_module
    from _bootstrap.owner_attachment import OWNER_UNAVAILABLE_REASONS

    def fail(_root):
        raise PermissionError('/private/sensitive-owner-path')
    monkeypatch.setattr(proxy_module, 'ConsentOwner', fail)
    owner, code = proxy_module._create_process_owner(str(tmp_path))
    assert owner is None and code == 'storage'
    program = tmp_path / 'unavailable.py'
    program.write_text('''import json,os
from _bootstrap.owner_attachment import OwnerAttachment,capture_owner_unavailable
attachment=OwnerAttachment.capture()
reason=capture_owner_unavailable()
print(json.dumps({"attached":attachment is not None,"reason":reason,
                  "environment_clean":not any(name.startswith("BRAIN_OWNER_") for name in os.environ)}),flush=True)
''')
    monkeypatch.setenv('PYTHONPATH', environment()['PYTHONPATH'])
    child = proxy_module.ChildProcess(sys.executable, str(program))
    child.start(owner=owner, owner_unavailable_code=code)
    payload = json.loads(child.readline())
    assert child.wait() == 0
    assert payload == {'attached': False, 'reason': OWNER_UNAVAILABLE_REASONS['storage'], 'environment_clean': True}
    assert 'sensitive' not in json.dumps(payload)


def test_unavailable_marker_cannot_mask_broken_established_attachment():
    from _bootstrap.owner_attachment import OWNER_UNAVAILABLE_ENV, capture_owner_unavailable

    env = {OWNER_CHANNEL_ENV: 'brain.owner-state/1:stream:999999999', OWNER_UNAVAILABLE_ENV: 'storage'}
    with pytest.raises(OwnerConnectionError):
        OwnerAttachment.capture(env)
    with pytest.raises(OwnerConnectionError):
        capture_owner_unavailable({OWNER_UNAVAILABLE_ENV: '/private/path-from-untrusted-input'})


@pytest.mark.parametrize('available', [True, False])
def test_real_server_captures_owner_before_eager_catalogue_composition(owner, command_vault_baseline, available):
    from _bootstrap.owner_attachment import OWNER_UNAVAILABLE_ENV, OWNER_UNAVAILABLE_REASONS, PROCESS_CONTEXT_ENV, ProcessIdentity

    # Use an installed-shaped Brain for actual server registration/authentication.
    selected = ConsentOwner(command_vault_baseline.vault_root)
    env = environment()
    env['PYTHONPATH'] = os.pathsep.join((str(ROOT / 'src/brain-core'), env['PYTHONPATH']))
    env['BRAIN_VAULT_ROOT'] = str(command_vault_baseline.vault_root)
    if not available:
        env[OWNER_UNAVAILABLE_ENV] = 'storage'
    channel = private_child_channel(selected, env) if available else nullcontext({'env': env})
    program = '''import json,os
from brain_mcp import server
composer=server._mcp_context_composer()
print(json.dumps({"kind":composer.owner_kind,"context":composer.owner_store.identity.context_id if composer.owner_store else None,
                  "unavailable":composer.owner_unavailable_reason,
                  "environment_clean":not any(name.startswith("BRAIN_OWNER_") for name in os.environ)}))
composer.close()
server._close_session_mirror()
'''
    try:
        with channel as options:
            options["env"][PROCESS_CONTEXT_ENV] = ProcessIdentity("mcp-instance", selected.identity.context_id).launch_value(initialise_owner=available)
            result = subprocess.run([sys.executable, '-c', program], **options,
                                    capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload == {'kind': 'mcp-instance' if available else None,
                           'context': selected.identity.context_id if available else None,
                           'unavailable': None if available else OWNER_UNAVAILABLE_REASONS['storage'],
                           'environment_clean': True}
    finally:
        selected.close()


def test_real_server_keeps_authenticated_key_out_of_provider_subprocesses(command_vault_clone):
    from _common import hash_key
    from _common._yaml import dump_mapping_text
    from _bootstrap.owner_attachment import PROCESS_CONTEXT_ENV, ProcessIdentity

    root = command_vault_clone.vault_root
    (root / '.brain/config.yaml').write_text(dump_mapping_text({'vault': {'operators': [
        {'id': 'mcp-test', 'profile': 'operator', 'auth': {'type': 'key', 'hash': hash_key('mcp-test-key')}}]}}))
    owner = ConsentOwner(root)
    env = environment()
    env.update(BRAIN_VAULT_ROOT=str(root), BRAIN_OPERATOR_KEY='mcp-test-key')
    env['PYTHONPATH'] = os.pathsep.join((str(ROOT / 'src/brain-core'), env['PYTHONPATH']))
    program = """import json,os,subprocess,sys
from brain_mcp import server
composer=server._mcp_context_composer()
assert composer.identity().principal == 'operator:mcp-test'
assert 'BRAIN_OPERATOR_KEY' not in os.environ
result=subprocess.run([sys.executable,'-c',"import os; assert 'BRAIN_OPERATOR_KEY' not in os.environ; assert 'BRAIN_OWNER_CHANNEL' not in os.environ; assert 'BRAIN_PROCESS_CONTEXT' not in os.environ"],capture_output=True,text=True)
assert result.returncode == 0, result.stderr
composer.close()
server._close_session_mirror()
"""
    try:
        for initialise in (True, False):
            with private_child_channel(owner, env) as options:
                options['env'][PROCESS_CONTEXT_ENV] = ProcessIdentity('mcp-instance', owner.identity.context_id).launch_value(initialise_owner=initialise)
                result = subprocess.run([sys.executable, '-c', program], **options, capture_output=True, text=True, timeout=15)
            assert result.returncode == 0, result.stderr
        assert env['BRAIN_OPERATOR_KEY'] == 'mcp-test-key', 'proxy replacement launch copy was mutated'
    finally:
        owner.close()
