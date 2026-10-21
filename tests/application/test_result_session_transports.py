"""Reserved transport context and private CLI credential delivery boundaries."""

import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp.server import MCPServer

from _application.adapter import ApplicationAdapter, project_adapter_result
from _application.registry import current_application_catalogue, current_request_resolver
from _application.results import Ok
from _command_interface import script
from brain_mcp._command_adapter import register_application_tools


def _registered(factory):
    server = MCPServer("result-session-test")
    register_application_tools(
        server, catalogue=current_application_catalogue(),
        resolver=current_request_resolver(), context_factory=factory,
        invocation_guard=lambda: None,
    )
    return server


def test_cli_credential_parent_alias_preserves_leaf_security(tmp_path):
    parent = tmp_path / 'physical'
    parent.mkdir(mode=0o755)
    alias = tmp_path / 'alias'
    try:
        alias.symlink_to(parent, target_is_directory=True)
    except OSError:
        pytest.skip('This host cannot create a directory symlink')
    selected = alias / 'session'
    script._write_result_session(selected, 'private-alias-credential')
    assert script._read_result_session(selected, 'result.read') == 'private-alias-credential'
    assert (parent / 'session').read_text() == 'private-alias-credential'
    with pytest.raises(FileExistsError):
        script._write_result_session(selected, 'replacement')
    leaf = alias / 'leaf-link'
    leaf.symlink_to(parent / 'session')
    with pytest.raises(script.ScriptUsageError):
        script._read_result_session(leaf, 'result.read')
    assert (parent / 'session').read_text() == 'private-alias-credential'


@pytest.mark.parametrize("value", [None, "", " ", 123, "x" * 513, "new", "\ud800"])
def test_invalid_reserved_context_precedes_composition(value):
    def forbidden(**kwargs):
        pytest.fail("invalid context reached composition")
    server = _registered(forbidden)
    result = asyncio.run(server.call_tool("command_list", {"brain_session": value}))
    assert result.structured_content["error"]["code"] == "invalid_request"
    assert "x" * 513 not in str(result)


def test_real_registration_and_reserved_stripping(monkeypatch):
    metadata = []
    payloads = []

    def factory(**kwargs):
        metadata.append(kwargs)
        return SimpleNamespace(invocation_id="inv-test", derived_snapshots=None)

    def invoke(self, context, command, payload):
        payloads.append(payload)
        entry = next(e for e in current_application_catalogue().entries if e.command_id == command)
        return project_adapter_result(Ok(command, entry.command_version, {}))

    monkeypatch.setattr(ApplicationAdapter, "invoke", invoke)
    server = _registered(factory)
    tool = server._tool_manager.get_tool("session_start")
    assert "brain_session" in inspect.signature(tool.fn).parameters
    assert tool.parameters["properties"]["brain_session"]["maxLength"] == 512
    asyncio.run(server.call_tool("session_start", {"brain_session": "new"}))
    assert metadata[-1]["result_session"] == "new"
    assert payloads == [{}]
    asyncio.run(server.call_tool("session_start", {}))
    assert "result_session" not in metadata[-1]


@pytest.mark.parametrize("mode", [0o644, 0o660, 0o604])
def test_cli_rejects_public_file_before_context(tmp_path, mode):
    path = tmp_path / "credential"
    path.write_text("private-test-credential")
    path.chmod(mode)
    def forbidden(**kwargs):
        pytest.fail("unsafe file reached context composition")
    assert script.run(["session", "start", "--result-session-file", str(path)],
                      context_factory=forbidden) == 2


def test_private_issuance_resume_and_no_overwrite(tmp_path):
    path = tmp_path / "credential"
    assert script._read_result_session(path, "session.start") == "new"
    with pytest.raises(script.ScriptUsageError):
        script._read_result_session(path, "document.read")
    script._write_result_session(path, "private-test-credential")
    assert path.stat().st_mode & 0o777 == 0o600
    assert script._read_result_session(path, "document.read") == "private-test-credential"
    with pytest.raises(FileExistsError):
        script._write_result_session(path, "replacement")
    assert path.read_text() == "private-test-credential"
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(script.ScriptUsageError):
        script._read_result_session(link, "session.start")


def test_cli_issuance_and_resume_keep_credential_out_of_output(tmp_path, monkeypatch):
    import io
    path = tmp_path / "credential"
    contexts = []
    payloads = []
    secret = "private-test-credential"

    def factory(**kwargs):
        contexts.append(kwargs)
        return SimpleNamespace()

    def invoke(self, context, command, payload):
        payloads.append(payload)
        return SimpleNamespace(result_session_credential=secret,
                               json_text='{"status":"ok"}', concise_text="session.start: ok",
                               is_error=False, exit_code=0)

    monkeypatch.setattr(ApplicationAdapter, "invoke", invoke)
    monkeypatch.setattr(script, "resolve_direct_vault", lambda *a, **kw: tmp_path)
    for expected in ("new", secret):
        out, err = io.StringIO(), io.StringIO()
        assert script.run(["session", "start", "--result-session-file", str(path), "--json"],
                          context_factory=factory, stdout=out, stderr=err) == 0
        assert contexts[-1]["result_session"] == expected
        assert secret not in out.getvalue() + err.getvalue()
    assert payloads == [{}, {}]


def test_forged_credential_error_does_not_echo_secret():
    secret = "private-test-credential"
    def reject(**kwargs):
        from _command_interface.direct import InvalidResultSession
        raise InvalidResultSession(secret)
    server = _registered(reject)
    result = asyncio.run(server.call_tool("session_start", {"brain_session": secret}))
    assert result.structured_content["error"]["code"] == "invalid_request"
    assert secret not in str(result)


def test_launcher_parser_and_forwarding_carry_only_file_path(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "cli"))
    from _local_cli.main import _parse_common, _result_session_runner
    import _common._venv as venv
    common, argv = _parse_common(["session", "start", "--result-session-file", "/private/context"])
    assert argv == ["session", "start"]
    assert common.result_session_file == "/private/context"
    calls = []
    monkeypatch.setattr(venv, "run_managed", lambda argv, **kw: calls.append((argv, kw)))
    _result_session_runner(common.result_session_file)(["python", "command.py", *argv], role="cli")
    assert calls == [(["python", "command.py", "session", "start", "--result-session-file", "/private/context"], {"role": "cli"})]


def test_automatic_client_file_recovers_retained_bytes(tmp_path, monkeypatch):
    import io
    import json
    from _command_interface.result_store import FileResultStore

    credentials = []
    calls = []
    body = {"text": "retained source bytes " * 100}

    def factory(**kwargs):
        store = FileResultStore(tmp_path, "test-brain", "test-principal")
        supplied = kwargs.get("result_session")
        credential = supplied or store.issue()
        credentials.append(credential)
        return SimpleNamespace(store=store, owner=store.authenticate(credential),
                               credential=None if supplied else credential)

    def invoke(self, context, command, payload):
        calls.append(command)
        if command == "result.read":
            delivered = context.store.load(context.owner, payload["result_id"])["envelope"]
            envelope = {"status": "ok", "result": delivered}
        else:
            ticket = context.store.admit(context.owner, command, "inv-source")
            result_id = context.store.publish(context.owner, ticket, body, {"scope": "normal"})
            envelope = {"status": "ok", "delivery": {"result_id": result_id}}
        return SimpleNamespace(result_session_credential=context.credential,
                               json_text=json.dumps(envelope), concise_text="ok",
                               is_error=False, exit_code=0)

    monkeypatch.setattr(ApplicationAdapter, "invoke", invoke)
    monkeypatch.setattr(script, "resolve_direct_vault", lambda *a, **kw: tmp_path)
    out, err = io.StringIO(), io.StringIO()
    assert script.run(["command", "list", "--json"], context_factory=factory, stdout=out, stderr=err) == 0
    first = json.loads(out.getvalue())
    path = Path(first["result_session_file"])
    assert path.is_relative_to(tmp_path / ".brain/local/result-clients")
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.read_text() == credentials[0]
    assert credentials[0] not in out.getvalue() + err.getvalue()
    out, err = io.StringIO(), io.StringIO()
    assert script.run(["result", "read", "--result-session-file", str(path), "--json",
                       "--request-json", json.dumps({"result_id": first["delivery"]["result_id"]})],
                      context_factory=factory, stdout=out, stderr=err) == 0
    assert json.loads(out.getvalue())["result"] == body
    assert credentials[0] == credentials[1]
    assert credentials[0] not in out.getvalue() + err.getvalue()
    assert calls == ["command.list", "result.read"]


def test_managed_job_reuses_only_private_channel_namespace(tmp_path, monkeypatch):
    import io
    import json
    issuances = []
    contexts = []

    class Attachment:
        kind = "cli-job"
        def __init__(self, context_id):
            self.context_id = context_id
        def connect(self, root):
            assert root == tmp_path
            return SimpleNamespace(identity=SimpleNamespace(context_id=self.context_id))
        def close(self):
            pass

    def factory(**kwargs):
        contexts.append(kwargs.get("result_session"))
        credential = None
        if contexts[-1] is None:
            credential = "job-credential-" + str(len(issuances))
            issuances.append(credential)
        return SimpleNamespace(credential=credential)

    def invoke(self, context, command, payload):
        return SimpleNamespace(result_session_credential=context.credential,
                               json_text='{"status":"ok"}', concise_text="ok",
                               is_error=False, exit_code=0)

    monkeypatch.setattr(ApplicationAdapter, "invoke", invoke)
    monkeypatch.setattr(script, "resolve_direct_vault", lambda *a, **kw: tmp_path)
    paths = []
    for job in ("job-one", "job-one", "job-two"):
        out, err = io.StringIO(), io.StringIO()
        assert script.run(["command", "list", "--json"], owner_attachment=Attachment(job),
                          context_factory=factory, stdout=out, stderr=err) == 0
        paths.append(json.loads(out.getvalue())["result_session_file"])
        for secret in issuances:
            assert secret not in out.getvalue() + err.getvalue()
    assert contexts == [None, issuances[0], None]
    assert paths[0] == paths[1] != paths[2]
    assert len(issuances) == 2


def test_automatic_client_directory_symlink_fails_before_invocation(tmp_path, monkeypatch):
    import io
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".brain").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(script, "resolve_direct_vault", lambda *a, **kw: tmp_path)
    def forbidden(**kwargs):
        pytest.fail("unsafe client directory reached invocation")
    assert script.run(["command", "list", "--json"], context_factory=forbidden,
                      stdout=io.StringIO(), stderr=io.StringIO()) == 2
    assert list(outside.iterdir()) == []


def test_concurrent_managed_children_publish_one_credential(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import io
    import json
    from threading import Barrier
    from _command_interface.result_store import FileResultStore

    barrier = Barrier(2)
    issued = []
    class Attachment:
        kind = "cli-job"
        def connect(self, root):
            return SimpleNamespace(identity=SimpleNamespace(context_id="concurrent-job"))
        def close(self):
            pass

    def factory(**kwargs):
        store = FileResultStore(tmp_path, "test-brain", "test-principal")
        credential = kwargs.get("result_session")
        fresh = credential is None
        if fresh:
            credential = store.issue()
            issued.append(credential)
        return SimpleNamespace(credential=credential if fresh else None,
                               owner=store.authenticate(credential))

    def invoke(self, context, command, payload):
        return SimpleNamespace(result_session_credential=context.credential,
                               json_text='{"status":"ok"}', concise_text="ok",
                               is_error=False, exit_code=0)

    monkeypatch.setattr(ApplicationAdapter, "invoke", invoke)
    monkeypatch.setattr(script, "resolve_direct_vault", lambda *a, **kw: tmp_path)
    def child(_):
        out, err = io.StringIO(), io.StringIO()
        barrier.wait(timeout=10)
        code = script.run(["command", "list", "--json"], owner_attachment=Attachment(),
                          context_factory=factory, stdout=out, stderr=err)
        assert code == 0, err.getvalue()
        return json.loads(out.getvalue())["result_session_file"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        paths = list(pool.map(child, range(2)))
    assert paths[0] == paths[1]
    assert len(issued) == 1
    assert Path(paths[0]).read_text() == issued[0]


def test_context_without_result_delivery_does_not_create_client_files(tmp_path, monkeypatch):
    import io
    monkeypatch.setattr(script, "resolve_direct_vault", lambda *a, **kw: tmp_path)
    monkeypatch.setattr(ApplicationAdapter, "invoke", lambda *a, **kw: SimpleNamespace(
        result_session_credential=None, json_text='{"status":"ok"}',
        concise_text="ok", is_error=False, exit_code=0))
    out, err = io.StringIO(), io.StringIO()
    assert script.run(["runtime", "status", "--json"], context_factory=lambda **kw: SimpleNamespace(),
                      stdout=out, stderr=err) == 0
    assert "result_session_file" not in out.getvalue()
    assert not (tmp_path / ".brain").exists()


@pytest.mark.parametrize('failure_type', [OSError, RuntimeError, AttributeError])
@pytest.mark.parametrize('large_effect', [False, True])
def test_file_publication_failure_preserves_source_outcome(tmp_path, monkeypatch, failure_type, large_effect):
    import io
    import json
    secret = "private-publication-failure-credential"
    effects = ['saved' * (3000 if large_effect else 1)]
    monkeypatch.setattr(script, "resolve_direct_vault", lambda *a, **kw: tmp_path)
    monkeypatch.setattr(ApplicationAdapter, "invoke", lambda *a, **kw: SimpleNamespace(
        result_session_credential=secret, json_text=json.dumps({'status': 'ok', 'committed_effects': effects}),
        concise_text="ok", is_error=False, exit_code=0))
    def fail(*args):
        raise failure_type(secret)
    monkeypatch.setattr(script, "_write_result_session", fail)
    out, err = io.StringIO(), io.StringIO()
    assert script.run(["command", "list", "--json"], context_factory=lambda **kw: SimpleNamespace(),
                      stdout=out, stderr=err) == 0
    envelope = json.loads(out.getvalue())
    if large_effect:
        assert envelope['committed_effect_summary']['count'] == 1
    else:
        assert envelope["committed_effects"] == ["saved"]
    assert envelope["status"] == "ok"
    assert envelope["delivery"]["status"] == envelope["delivery"]["availability"] == "unavailable"
    assert envelope["delivery"]["reason"] == "result_session_unavailable"
    assert "do not rerun a mutation" in err.getvalue()
    assert secret not in out.getvalue() + err.getvalue()
    assert "result_session_file" not in out.getvalue()


def test_session_run_accepts_result_file_for_private_initialisation(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "cli"))
    import _local_cli.main as cli_main
    import _local_cli.session as cli_session
    common, command = cli_main._parse_common([
        "--result-session-file", "/private/context", "session", "run", "--", "program"])
    selected = SimpleNamespace()
    monkeypatch.setattr(cli_main, "_resolve_optional", lambda *a, **kw: selected)
    monkeypatch.setattr(cli_main, "_invoke_application", lambda *a, **kw: {
        "schema": "brain.command-result/1", "command": "command.list", "status": "ok",
        "result": {"interface_epoch": 3}})
    seeded = []
    monkeypatch.setattr(cli_main, "_initialise_selected_job", lambda target, owner, args:
                        seeded.append((target, owner, args.result_session_file)))
    def run(target, argv, *, initialise_owner):
        assert argv == ["program"]
        initialise_owner("private-job")
        return 0
    monkeypatch.setattr(cli_session, "run_owned_job", run)
    assert cli_main._run_session(command, common) == 0
    assert seeded == [(selected, "private-job", "/private/context")]


def test_job_file_validation_precedes_identity_setup(tmp_path, monkeypatch, capsys):
    from _bootstrap.owner_attachment import ProcessIdentity
    import _command_interface.direct as direct
    credential = tmp_path / "credential"
    credential.write_text("private-test-credential")
    credential.chmod(0o644)
    def forbidden(**kwargs):
        pytest.fail("unsafe file reached identity setup")
    monkeypatch.setattr(direct, "initialise_attached_job_owner", forbidden)
    assert script.initialise_job(["--initialise-job-owner", "--vault", str(tmp_path),
                                  "--result-session-file", str(credential)],
        owner_attachment=SimpleNamespace(kind="cli-job"),
        transport_identity=ProcessIdentity("cli-job", "job-test"),
        owner_initialisation_allowed=True) == 4
    assert "private-test-credential" not in capsys.readouterr().err


def test_job_file_seeds_subprocess_namespace_with_fresh_consent(tmp_path, monkeypatch):
    import json
    import os
    import shutil
    import subprocess
    import sys
    from _bootstrap.consent_owner import ConsentOwner
    from _bootstrap.owner_attachment import OwnerAttachment

    if os.name != "posix":
        pytest.skip("private job descriptor inheritance requires POSIX")
    repo = Path(__file__).resolve().parents[2]
    scripts = repo / "src/brain-core/scripts"
    monkeypatch.syspath_prepend(str(repo / "cli"))
    import _local_cli.main as cli_main
    from _local_cli.runtime import SelectedBrain
    root = tmp_path / "Brain"
    (root / ".brain-core/scripts").mkdir(parents=True)
    (root / ".brain-core/VERSION").write_text((repo / "src/brain-core/VERSION").read_text())
    shutil.copyfile(scripts / "command.py", root / ".brain-core/scripts/command.py")
    (root / ".brain").mkdir()
    from _common._yaml import dump_mapping_text
    permitted = ["artefact.delete", *(entry.command_id for entry in current_application_catalogue().entries
                                      if entry.initial_class.value == "control")]
    (root / ".brain/config.yaml").write_text(dump_mapping_text({
        "vault": {"profiles": {"operator": {"allow": permitted}},
                  "access": {"request_policy": "allowed"}}}))
    monkeypatch.setenv("PYTHONPATH", str(scripts))
    monkeypatch.setattr(cli_main, "command_python", lambda *a: Path(sys.executable))
    selected = SelectedBrain(root, None, "test")
    path = tmp_path / "credential"
    common = SimpleNamespace(operator_key=None, result_session_file=str(path))
    probe = '''
import json,sys
from pathlib import Path
from _bootstrap.owner_attachment import OwnerAttachment
from _command_interface.direct import DirectContextComposer
from _application.consent import ConsentScope
attachment = OwnerAttachment.capture()
composer = DirectContextComposer(vault_root=Path(sys.argv[1]), owner_attachment=attachment)
try:
    context = composer.compose(command_id='session.start')
    service = context.authorisation.service
    before = service.state('artefact.delete')
    if sys.argv[2] == 'grant':
        service.request(request_id='test-grant', scope=ConsentScope.COMMAND,
                        command_id='artefact.delete', review=service.command_review('artefact.delete'))
    print(json.dumps({'owner':context.result_delivery.owner, 'before':before,
                      'after':service.state('artefact.delete'),
                      'consent_owner':service.identity.context_id}))
finally:
    composer.close()
'''
    def child(owner, action):
        attachment = OwnerAttachment.for_job(owner)
        try:
            completed = subprocess.run([sys.executable, "-c", probe, str(root), action],
                **attachment.forwarded_process(), capture_output=True, text=True, timeout=20)
            assert completed.returncode == 0, completed.stderr
            assert path.read_text() not in completed.stdout + completed.stderr
            return json.loads(completed.stdout)
        finally:
            attachment.close()

    first = ConsentOwner(root)
    try:
        cli_main._initialise_selected_job(selected, first, common)
        assert path.stat().st_mode & 0o777 == 0o600
        one = child(first, "grant")
        two = child(first, "observe")
        assert one["owner"] == two["owner"]
        assert one["before"] == "authorisation_required"
        assert two["before"] == "authorised"
    finally:
        first.close()
    second = ConsentOwner(root)
    try:
        cli_main._initialise_selected_job(selected, second, common)
        resumed = child(second, "observe")
        assert resumed["owner"] == one["owner"]
        assert resumed["consent_owner"] != one["consent_owner"]
        assert resumed["before"] == "authorisation_required"
    finally:
        second.close()

    from _local_cli.session import run_owned_job
    marker = tmp_path / "root-executed"
    missing_parent = tmp_path / "missing/credential"
    with pytest.raises(cli_main.CliError, match="could not initialise"):
        run_owned_job(selected, [sys.executable, "-c", "from pathlib import Path; Path(" + repr(str(marker)) + ").touch()"],
            initialise_owner=lambda owner: cli_main._initialise_selected_job(selected, owner,
                SimpleNamespace(operator_key=None, result_session_file=str(missing_parent))))
    assert not marker.exists()


@pytest.mark.parametrize("status", ["ok", "partial", "error"])
def test_cli_final_guard_counts_file_metadata_and_preserves_execution(status):
    import json
    effects = [{"kind": "saved", "subject": "test.md"}]
    envelope = {"schema": "brain.command-result/1", "command": "test.write", "command_version": 1,
                "status": status, "result": {"text": "x" * 7600}, "committed_effects": effects,
                "delivery": {"result_id": "r" * 64}, "error": {"code": "internal_error", "effects": "known"}}
    assert script._cli_result_fits(envelope, "ok")
    path = "/private/" + "segment/" * 60 + "credential"
    envelope["result_session_file"] = path
    envelope["result_session"] = {"credential": "never-emit-this-credential"}
    bounded, text = script._guard_cli_result(envelope, "ok")
    assert script._cli_result_fits(bounded, text)
    assert bounded["status"] == status
    assert bounded["committed_effects"] == effects
    assert bounded["result_session_file"] == path
    assert bounded["delivery"]["result_id"] == "r" * 64
    assert bounded["delivery"]["status"] == "unavailable"
    assert "never-emit-this-credential" not in json.dumps(bounded) + text


def test_cli_guard_bounds_escaped_unicode_and_oversized_effect_metadata():
    effects = [{"subject": "😀" * 10000}]
    envelope = {"schema": "brain.command-result/1", "command": "test.write", "command_version": 1,
                "status": "ok", "result": {"text": "😀" * 1900}, "committed_effects": effects,
                "result_session_file": "/private/credential", "delivery": {"result_id": "r" * 64}}
    bounded, text = script._guard_cli_result(envelope, "😀" * 10000)
    assert script._cli_result_fits(bounded, text)
    assert bounded["status"] == "ok"
    assert bounded["committed_effect_summary"]["count"] == 1
    assert bounded["result_session_file"] == "/private/credential"
    assert bounded["delivery"]["result_id"] == "r" * 64


def test_cli_emission_guards_after_adding_private_file_path(tmp_path, monkeypatch):
    import io
    import json
    path = tmp_path.joinpath(*(["segment"] * 60), "credential")
    path.parent.mkdir(parents=True)
    script._write_result_session(path, "private-emission-credential")
    effects = [{"kind": "saved", "subject": "test.md"}]
    source = {"schema": "brain.command-result/1", "command": "command.list", "command_version": 1,
              "status": "ok", "result": {"text": "x" * 7600}, "committed_effects": effects,
              "delivery": {"result_id": "r" * 64}}
    assert script._cli_result_fits(source, "ok")
    monkeypatch.setattr(script, "resolve_direct_vault", lambda *a, **kw: tmp_path)
    monkeypatch.setattr(ApplicationAdapter, "invoke", lambda *a, **kw: SimpleNamespace(
        result_session_credential=None, json_text=json.dumps(source), concise_text="ok", is_error=False, exit_code=0))
    out, err = io.StringIO(), io.StringIO()
    assert script.run(["command", "list", "--json", "--result-session-file", str(path)],
                      context_factory=lambda **kw: SimpleNamespace(), stdout=out, stderr=err) == 0
    delivered = json.loads(out.getvalue())
    assert delivered["status"] == "ok"
    assert delivered["committed_effects"] == effects
    assert delivered["result_session_file"] == str(path)
    assert delivered["delivery"]["status"] == "unavailable"
    assert script._cli_result_fits(delivered, "")
    assert "private-emission-credential" not in out.getvalue() + err.getvalue()


def test_composed_application_discovery_keeps_delivery_context(monkeypatch, capsys):
    import json
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "cli"))
    import _local_cli.main as cli
    source = {"schema": "brain.command-result/1", "command": "command.list", "command_version": 1,
        "status": "ok", "result_session_file": "/private/client", "delivery": {
            "result_id": "r" * 64, "view_id": "v", "page": 1, "total_pages": 3,
            "continuation": {"result_id": "r" * 64, "page": 2}}, "result": {
            "catalogue_schema": "brain.command-catalogue/1", "catalogue_fingerprint": "sha256:test",
            "entries": [{"command_id": "test.read", "command_version": 1, "summary": "Read."}],
            "next_cursor": {"snapshot_token": "snapshot", "command_id": "test.read"}}}
    monkeypatch.setattr(cli, "_invoke_application", lambda *a, **kw: source)
    common, argv = cli._parse_common(["command", "list", "--owner", "application", "--json"])
    assert cli._run_discovery(argv, common=common, selected=SimpleNamespace(supports_command_interface=True),
                              cli_binary=None, distribution_root=None) == 0
    delivered = json.loads(capsys.readouterr().out)
    assert delivered["entries"][0]["command_id"] == "test.read"
    assert delivered["delivery"]["continuation"] == source["delivery"]["continuation"]
    assert delivered["delivery"]["count_scope"] == "application-command-window"
    assert delivered["result_session_file"] == "/private/client"
    assert cli._discovery_fits(delivered)


def test_composed_launcher_byte_pages_never_skip_entries(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "cli"))
    import _local_cli.main as cli
    records = [{"command_id": f"test.command-{i:02}", "owner": "launcher", "summary": "x" * 800} for i in range(25)]
    recovered = []
    cursor = None
    for _ in range(25):
        start = 0 if cursor is None else next(i + 1 for i, row in enumerate(records) if row["command_id"] == cursor["command_id"])
        payload = {"schema": "brain.local-command-list/2", "catalogues": {"launcher": {"fingerprint": "sha256:test"}},
                   "entries": records[start:], "launcher_next_cursor": None}
        frame = cli._bound_discovery(payload, cursor)
        assert cli._discovery_fits(frame)
        recovered.extend(frame["entries"])
        cursor = frame["launcher_next_cursor"]
        if cursor is None:
            break
    assert recovered == records


def test_dispatch_metadata_reads_declared_detail_with_same_private_file(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "cli"))
    import _local_cli.main as cli
    envelope = {"schema": "brain.command-result/1", "command": "command.describe", "status": "ok",
                "result_session_file": "/private/client", "delivery": {"result_id": "r" * 64},
                "result": {"dependency_tier": {"detail_reference": "tier-detail"},
                           "request_schema_json": {"detail_reference": "schema-detail"}}}
    calls = []
    def read(selected, command, payload, common, **kwargs):
        calls.append((command, payload, kwargs))
        return {"schema": "brain.command-result/1", "command": "result.read", "status": "ok",
                "result": {"envelope": {"result": {"fragments": [
                    {"text": "stdlib", "line_ending": ""}]}}}, "delivery": {"next_detail": None}}
    monkeypatch.setattr(cli, "_invoke_application", read)
    resolved = cli._application_description_metadata(None, envelope, SimpleNamespace())
    assert resolved["dependency_tier"] == "stdlib"
    assert resolved["request_schema_json"] == {"detail_reference": "schema-detail"}
    assert calls == [("result.read", {"result_id": "r" * 64, "detail": "tier-detail"},
                      {"dependency_tier": "stdlib", "result_session_file": "/private/client"})]


def test_real_composer_maps_unknown_holder_credential_to_invalid_request(tmp_path, monkeypatch, caplog):
    from _command_interface.direct import DirectContextComposer
    root = tmp_path / "Brain"
    (root / ".brain-core").mkdir(parents=True)
    (root / ".brain-core/VERSION").write_text("0.73.0\n")
    secret = "f" * 64
    composer = DirectContextComposer(vault_root=root)
    def forbidden(*args, **kwargs):
        pytest.fail("invalid result credential reached domain invocation")
    monkeypatch.setattr(ApplicationAdapter, "invoke", forbidden)
    def factory(**metadata):
        return composer.compose(command_id=metadata["command_id"], invocation_id="unknown-credential-test",
                                result_session=metadata["result_session"])
    try:
        server = _registered(factory)
        result = asyncio.run(server.call_tool("session_start", {"brain_session": secret}))
        assert result.structured_content["error"]["code"] == "invalid_request"
        assert secret not in str(result) + caplog.text
    finally:
        composer.close()


def test_real_cli_composer_rejects_unknown_file_credential_before_domain(tmp_path, monkeypatch):
    import io
    from _command_interface.direct import compose_direct_context
    root = tmp_path / "Brain"
    (root / ".brain-core").mkdir(parents=True)
    (root / ".brain-core/VERSION").write_text("0.73.0\n")
    path = tmp_path / "credential"
    secret = "f" * 64
    script._write_result_session(path, secret)
    def forbidden(*args, **kwargs):
        pytest.fail("invalid result credential reached domain invocation")
    monkeypatch.setattr(ApplicationAdapter, "invoke", forbidden)
    out, err = io.StringIO(), io.StringIO()
    assert script.run(["session", "start", "--vault", str(root), "--result-session-file", str(path), "--json"],
                      context_factory=compose_direct_context, stdout=out, stderr=err) == 2
    assert "invalid request" in err.getvalue()
    assert secret not in out.getvalue() + err.getvalue()


@pytest.fixture
def retained_detail_system(tmp_path):
    from _command_interface.result_store import FileResultStore
    from _command_interface.result_delivery import ResultDeliverySession
    from _application.result_navigation import build_view
    store = FileResultStore(tmp_path, "test-brain", "test-principal")
    session = ResultDeliverySession(store, store.issue())
    context = SimpleNamespace(invocation_id="detail-test", authorisation=SimpleNamespace(service=SimpleNamespace(
        check_result_delivery=lambda *args: None,
        result_delivery_stamp=lambda *args: {"owner": "test"})))
    def publish(payload, *, status="ok", query=None, command="test.read"):
        source = {"schema": "brain.command-result/1", "command": command, "command_version": 1,
                  "status": status, "warnings": [], "result": payload}
        view = build_view(source)
        key = session._view_key(view.unit, view.page_size)
        ticket = store.admit(session.owner, command, "invocation")
        result_id = store.publish(session.owner, ticket, source,
                                 {"command_id": command, "stamp": None, "default_view": key}, query)
        session._save(result_id, key, view, None)
        return result_id, view
    return context, store, session, publish


def test_outcome_all_is_frozen_cached_and_preserves_partial_effects(retained_detail_system, monkeypatch):
    from _application.result.read import ResultReadRequest
    context, store, session, publish = retained_detail_system
    effects = [{"kind": "saved", "subject": "test.md"}]
    result_id, _ = publish({"committed_effects": effects}, status="partial",
                           query={"command_id": "artefact.list", "revision": "old", "payload": {}})
    def forbidden(*args, **kwargs):
        pytest.fail("outcome navigation reloaded the source body or refreshed its live query")
    monkeypatch.setattr(session, "_refresh", forbidden)
    first = session.read(context, ResultReadRequest(result_id, detail="@outcome", max_pages="all"))
    assert first["status"] == "partial"
    assert first["result"]["committed_effects"] == effects
    assert first["delivery"]["backing"] == "retained"
    assert first["delivery"]["max_pages"] is None
    monkeypatch.setattr(store, "load", forbidden)
    again = session.read(context, ResultReadRequest(result_id, detail="@outcome", max_pages="all"))
    assert again == first


def test_child_generic_continuation_addresses_actual_stored_detail(retained_detail_system, monkeypatch):
    from _application.result.read import ResultReadRequest
    context, store, session, publish = retained_detail_system
    original = "雪😀" * 1800
    result_id, view = publish({"report": original})
    reference = view.page()["result"]["report"]["detail_reference"]
    def forbidden(*args, **kwargs):
        pytest.fail("detail continuation loaded or replayed the original source")
    monkeypatch.setattr(store, "load", forbidden)
    first = session.read(context, ResultReadRequest(result_id, detail=reference))
    pieces = []
    seen = set()
    frame = first
    while True:
        pieces.extend(item["text"] + item["line_ending"] for item in frame["result"]["fragments"])
        cursor = frame["delivery"]["continuation"]
        if cursor is None:
            assert frame["delivery"]["next_detail"] is None
            break
        assert cursor == {"result_id": result_id, "detail": frame["delivery"]["next_detail"]}
        assert cursor["detail"] not in seen
        seen.add(cursor["detail"])
        frame = session.read(context, ResultReadRequest(result_id, continuation=cursor))
    assert seen
    assert "".join(pieces) == original


@pytest.mark.parametrize("cursor", [
    {"result_id": "foreign", "detail": "@outcome"},
    {"detail": "@outcome"},
    {"detail": 123},
    {"detail": "x" * 129},
    {"detail": "@outcome", "start_page": True},
])
def test_detail_continuations_validate_identity_before_body_reads(retained_detail_system, monkeypatch, cursor):
    from _application.result.read import ResultReadRequest
    context, store, session, publish = retained_detail_system
    result_id, _ = publish({"small": "result"})
    cursor = {"result_id": result_id, **cursor}
    if cursor == {"result_id": result_id, "detail": "@outcome"}:
        cursor.pop("result_id")
    def forbidden(*args, **kwargs):
        pytest.fail("invalid detail continuation read the retained body")
    monkeypatch.setattr(store, "load", forbidden)
    with pytest.raises(ValueError):
        session.read(context, ResultReadRequest(result_id, continuation=cursor))


@pytest.mark.parametrize('detail', [None, '@outcome'])
def test_explicit_page_one_differs_from_omitted_continuation_page(retained_detail_system, monkeypatch, detail):
    from _application.result.read import ResultReadRequest, decode
    assert decode({'result_id': 'result'}).page is None
    assert decode({'result_id': 'result', 'page': None}).page is None
    assert decode({'result_id': 'result', 'page': 1}).page == 1
    context, store, session, publish = retained_detail_system
    result_id, _ = publish({f'field-{i}': 'x' * 150 for i in range(60)}, status='partial')
    options = {'detail': detail, 'page_size': 1, 'max_pages': 2}
    first = session.read(context, ResultReadRequest(result_id, page=0, **options))
    selected = session.read(context, ResultReadRequest(result_id, anchor=4, **options))
    assert (selected['delivery']['page'], selected['delivery']['start_page']) == (5, 5)
    cursor = selected['delivery']['continuation']

    def forbidden(*args, **kwargs):
        pytest.fail('page-one navigation reloaded or rebuilt retained output')

    monkeypatch.setattr(store, 'load', forbidden)
    from _command_interface import result_delivery
    monkeypatch.setattr(result_delivery, 'build_view', forbidden)
    omitted = session.read(context, decode({'result_id': result_id, 'continuation': cursor}))
    assert (omitted['delivery']['page'], omitted['delivery']['start_page']) == (6, 5)
    assert omitted['delivery']['selection_id'] == selected['delivery']['selection_id']
    explicit = session.read(context, decode({'result_id': result_id, 'page': 1, 'continuation': cursor}))
    info = explicit['delivery']
    assert (info['page'], info['start_page'], info['end_page'], info['max_pages']) == (1, 1, 2, 2)
    assert info['selection_id'] != selected['delivery']['selection_id']
    assert explicit['result'] == first['result']
    assert (info['continuation']['page'], info['continuation']['start_page']) == (2, 1)
    following = session.read(context, decode({'result_id': result_id, 'continuation': info['continuation']}))
    assert following['delivery']['page'] == 2
    assert following['delivery']['selection_id'] == info['selection_id']


def test_cached_outcome_selection_continuation_keeps_its_view(retained_detail_system, monkeypatch):
    from _application.result.read import ResultReadRequest
    from _application.result_navigation import build_view
    context, store, session, publish = retained_detail_system
    result_id, _ = publish({"small": "source"}, query={
        "command_id": "artefact.list", "revision": "old", "payload": {}})
    # Exercise the cached-view port with an exact multi-page outcome map.
    view = build_view({"schema": "brain.command-result/1", "command": "test.read", "command_version": 1,
                       "status": "ok", "result": {"items": list(range(5))}}, page_size=1)
    session._save(result_id, "outcome:1", view, None)
    def forbidden(*args, **kwargs):
        pytest.fail("cached outcome continuation left its immutable view")
    monkeypatch.setattr(store, "load", forbidden)
    monkeypatch.setattr(session, "_refresh", forbidden)
    first = session.read(context, ResultReadRequest(result_id, detail="@outcome", page_size=1, max_pages=2))
    cursor = first["delivery"]["continuation"]
    assert cursor["detail"] == "@outcome"
    second = session.read(context, ResultReadRequest(result_id, continuation=cursor, page="+1"))
    assert second["result"]["items"] == [1]
    assert second["delivery"]["start_page"] == 1
    assert second["delivery"]["max_pages"] == 2
    assert not second["delivery"]["has_more"]
    outside = second["delivery"]["outside_continuation"]
    assert outside["detail"] == "@outcome"
    third = session.read(context, ResultReadRequest(result_id, continuation=outside))
    assert third["result"]["items"] == [2]
    assert third["delivery"]["start_page"] == 3
    assert third["delivery"]["selection_id"] != first["delivery"]["selection_id"]
    assert third["delivery"]["backing"] == "retained"


def test_cached_outcome_anchor_uses_stored_selection_without_body_reload(retained_detail_system, monkeypatch):
    from _application.result.read import ResultReadRequest
    context, store, session, publish = retained_detail_system
    result_id, _ = publish({"committed_effects": [{"kind": "saved", "subject": "test.md"}]}, status="partial")
    first = session.read(context, ResultReadRequest(result_id, detail="@outcome"))
    def forbidden(*args, **kwargs):
        pytest.fail("cached outcome anchor reloaded, rebuilt or refreshed the source")
    monkeypatch.setattr(store, "load", forbidden)
    monkeypatch.setattr(session, "_save", forbidden)
    monkeypatch.setattr(session, "_refresh", forbidden)
    anchored = session.read(context, ResultReadRequest(result_id, detail="@outcome", anchor=1, max_pages="all"))
    assert anchored["status"] == "partial"
    assert anchored["result"] == first["result"]
    assert anchored["delivery"]["requested_anchor"] == 1
    assert anchored["delivery"]["overlap_notice"]
    assert anchored["delivery"]["start_page"] == anchored["delivery"]["page"] == 1
    assert anchored["delivery"]["backing"] == "retained"


def test_cached_outcome_anchor_resets_selection_and_reuses_resized_view(retained_detail_system, monkeypatch):
    from _application.result.read import ResultReadRequest
    from _application.result_navigation import build_view
    context, store, session, publish = retained_detail_system
    result_id, _ = publish({"source": "immutable"})
    # Exercise the cached-view port with exact multi-page maps for both sizes.
    envelope = {"schema": "brain.command-result/1", "command": "test.read", "command_version": 1,
                "status": "ok", "result": {"items": list(range(10))}}
    for size in (2, 3):
        session._save(result_id, f"outcome:{size}", build_view(envelope, page_size=size), None)
    def forbidden(*args, **kwargs):
        pytest.fail("cached anchored outcome rebuilt or loaded the original body")
    monkeypatch.setattr(store, "load", forbidden)
    monkeypatch.setattr(session, "_save", forbidden)
    anchored = session.read(context, ResultReadRequest(result_id, detail="@outcome", page_size=3,
                                                     anchor=4, max_pages=2))
    assert anchored["result"]["items"] == [3, 4, 5]
    assert anchored["delivery"]["page"] == anchored["delivery"]["start_page"] == 2
    assert anchored["delivery"]["requested_anchor"] == 4
    assert anchored["delivery"]["overlap_notice"]
    cursor = anchored["delivery"]["continuation"]
    assert cursor["detail"] == "@outcome"
    resized = session.read(context, ResultReadRequest(result_id, continuation=cursor, page_size=2, anchor=5))
    assert resized["result"]["items"] == [4, 5]
    assert resized["delivery"]["page"] == resized["delivery"]["start_page"] == 3
    assert resized["delivery"]["max_pages"] == 2
    assert resized["delivery"]["requested_anchor"] == 5
    assert resized["delivery"]["overlap_notice"]
    assert resized["delivery"]["continuation"]["detail"] == "@outcome"


@pytest.mark.parametrize("status", ["partial", "error"])
def test_large_outcome_recovers_every_original_value_through_final_encoder(retained_detail_system, status, monkeypatch):
    import hashlib
    import json
    from copy import deepcopy
    from _application.adapter import AdapterProjection
    from _application.projection import canonical_result_envelope
    from _application.result.read import ResultReadPayload, ResultReadRequest
    from _application.result_navigation import build_view
    from brain_mcp._command_adapter import _tool_result
    from brain_mcp._result_content import (
        canonical_tool_result_size, encoded_tool_result_size, tool_result_fits,
        delivery_unavailable_projection,
    )
    context, store, session, _ = retained_detail_system
    effects = [{"kind": "saved", "subject": f"{i}-" + "雪😀" * 180} for i in range(18)]
    error = {"code": "conflict", "effects": "unknown", "message": "雪😀" * 1800,
             "details": {"subjects": effects, "reason": "\n".join(["雪😀" * 200] * 12)}}
    warnings = [{"code": "source_warning", "message": "雪😀" * 1600}]
    source = {"schema": "brain.command-result/1", "command": "test.write", "command_version": 1,
              "status": status, "warnings": warnings, "result": {"committed_effects": effects}}
    if status == "error":
        source["error"] = error
        source["result"] = None
    original = deepcopy(source)
    ticket = store.admit(session.owner, "test.write", "huge-outcome")
    result_id = store.publish(session.owner, ticket, source, {"command_id": "test.write", "stamp": None})
    # Establish the actual failure requiring recovery: the original source page
    # cannot be delivered. Its outcome reference must lead to bounded pages.
    original_page = build_view(source).page()
    original_wire = {"structuredContent": original_page}
    assert not tool_result_fits(original_wire)
    fallback = delivery_unavailable_projection(original_wire)
    assert fallback["structuredContent"]["status"] == status

    checked = []
    def read(**navigation):
        frame = session.read(context, ResultReadRequest(result_id, **navigation))
        assert frame["status"] == status
        assert frame["delivery"]["availability"] == "available"
        assert "committed_effects" not in {k: v for k, v in frame.items() if k != "result"}
        if status == "error":
            assert frame["error"] == {"code": "conflict", "effects": "unknown"}
        # Match ApplicationAdapter's result.read wrapping, then use the real MCP
        # result builder and the proxy's final ASCII JSON size measurement.
        body = deepcopy(frame)
        descriptor = body.pop("delivery")
        result = Ok("result.read", 1, ResultReadPayload(body))
        envelope = canonical_result_envelope(result)
        envelope["delivery"] = descriptor
        wire = _tool_result(AdapterProjection(result, False, 0, envelope)).model_dump(by_alias=True, exclude_none=True)
        assert canonical_tool_result_size(wire["structuredContent"]) <= 8000
        assert encoded_tool_result_size(wire) <= 32768
        assert tool_result_fits(wire)
        checked.append(frame)
        return frame

    active = set()
    def resolve(value):
        if isinstance(value, dict) and "detail_reference" in value:
            detail = value["detail_reference"]
            assert detail not in active
            active.add(detail)
            frames = [read(detail=detail)]
            while frames[-1]["delivery"]["continuation"] is not None:
                frames.append(read(continuation=frames[-1]["delivery"]["continuation"]))
            bodies = [frame["result"] for frame in frames]
            if "fragments" in bodies[0]:
                restored = "".join(fragment["text"] + fragment["line_ending"]
                                   for body in bodies for fragment in body["fragments"])
            elif bodies[0].get("root_type") == "list":
                fields = [field for body in bodies for field in body["fields"]]
                assert [field["index"] for field in fields] == list(range(len(fields)))
                restored = [resolve(field["value"]) for field in fields]
            elif bodies[0].get("root_type") == "dict":
                restored = {resolve(field["field"]): resolve(field["value"])
                            for body in bodies for field in body["fields"]}
            else:
                assert len(bodies) == 1
                restored = resolve(bodies[0])
            active.remove(detail)
            return restored
        if isinstance(value, dict):
            return {key: resolve(item) for key, item in value.items()}
        if isinstance(value, list):
            return [resolve(item) for item in value]
        return value

    root = read(detail="@outcome", max_pages="all")
    assert store.load(session.owner, result_id)["envelope"] == original
    def forbidden(*args, **kwargs):
        pytest.fail("valid cached outcome or child read reloaded the source body")
    monkeypatch.setattr(store, "load", forbidden)
    assert read(detail="@outcome", max_pages="all") == root
    recovered = resolve(root["result"])
    assert recovered["warnings"] == warnings
    if status == "partial":
        assert recovered["committed_effects"] == effects
        assert root["committed_effect_summary"] == {
            "count": len(effects),
            "sha256": hashlib.sha256(json.dumps(effects, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
        }
    else:
        assert recovered["error"] == error
    assert len(checked) > 5


@pytest.mark.parametrize("command", ["artefact.search", "resource.search"])
def test_ranked_owner_windows_are_explicit_and_top_k_opens_a_new_invocation(
        retained_detail_system, monkeypatch, command, tmp_path):
    from _application import _search_support
    from _application.delivery_failure import DeliveryFailure
    from _application.artefact import search as artefact_search
    from _application.resource import search as resource_search
    from _application.projection import canonical_wire_value
    from _application.result.read import ResultReadRequest
    from _search import lexical_query, mode, resource
    context, store, session, publish = retained_detail_system
    calls = []
    def retrieve(*args, top_k, **kwargs):
        calls.append(top_k)
        return [{"path": f"Notes/{i}.md", "title": f"Rank {i}", "score": 1 / (i + 1)}
                for i in range(top_k)]
    monkeypatch.setattr(artefact_search, "load_router", lambda *args: {})
    monkeypatch.setattr(artefact_search, "semantic_provider_ready", lambda *args: False)
    monkeypatch.setattr(lexical_query, "load_index", lambda *args: {})
    monkeypatch.setattr(mode, "dispatch_search", retrieve)
    monkeypatch.setattr(_search_support, "load_router", lambda *args: {})
    monkeypatch.setattr(resource, "search_resource", retrieve)
    source_context = SimpleNamespace(selected_brain=SimpleNamespace(vault_root=tmp_path))
    def invoke(top_k):
        if command == "artefact.search":
            request = artefact_search.ArtefactSearchRequest("rank", top_k=top_k)
            result = artefact_search.execute(source_context, request)
        else:
            request = resource_search.ResourceSearchRequest(resource_search.SearchableResource.MEMORY, "rank", top_k)
            result = resource_search.execute(source_context, request)
        assert isinstance(result, Ok)
        payload = canonical_wire_value(result.result)
        return publish(payload, command=command)[0]
    first_id = invoke(30)
    first = session.read(context, ResultReadRequest(first_id))
    continuation = first["delivery"]["continuation"]
    assert continuation is not None
    second = session.read(context, ResultReadRequest(first_id, continuation=continuation))
    for page in (first, second):
        assert page["result"]["candidate_policy"] == "fixed-top-k-window"
        assert page["result"]["requested_top_k"] == 30
        assert page["result"]["retrieval_mode"] == "lexical"
        assert page["delivery"]["count_scope"] == "ranked-candidate-window"
        assert page["delivery"]["total_items"] == 30
    assert first["result"]["items"] + second["result"]["items"] == [
        {"path": f"Notes/{i}.md", "title": f"Rank {i}", "resource_type": "", "status": None,
         "score": 1 / (i + 1), "snippet": "", **({"primary_workspace": None, "relevant_workspaces": []}
         if command == "artefact.search" else {})} for i in range(30)]
    assert calls == [30]
    second_id = invoke(45)
    assert second_id != first_id
    fresh = session.read(context, ResultReadRequest(second_id))
    assert fresh["result"]["requested_top_k"] == 45
    assert fresh["delivery"]["total_items"] == 45
    assert calls == [30, 45]
    with pytest.raises(DeliveryFailure) as unavailable:
        session.read(context, ResultReadRequest(first_id, continuation=continuation))
    assert unavailable.value.reason == "result_unavailable"
    assert unavailable.value.message == "Private result storage is unavailable."
    assert calls == [30, 45]


@pytest.mark.parametrize("command", ["relationship.query", "history.query"])
def test_graph_generic_pages_count_produced_records_and_preserve_native_cursor(
        retained_detail_system, monkeypatch, command):
    from _application.relationship._types import GraphCursor, GraphResult
    from _application.projection import canonical_wire_value
    from _application.result.read import ResultReadRequest
    context, store, session, publish = retained_detail_system
    records = tuple({"id": f"record-{i}", "predicate": "related"} for i in range(31))
    source = GraphResult(records, ({"code": "depth_budget"},), generation="generation-1",
                         complete=False, source_freshness="observed-projection-only", visited=99,
                         next_cursor=GraphCursor("generation-1", 31, "selectors", "opaque-native-stack"),
                         continuation="native-continuation")
    payload = canonical_wire_value(source)
    result_id, _ = publish(payload, command=command)
    def forbidden(*args, **kwargs):
        pytest.fail("generic graph paging loaded or reexecuted the original traversal")
    monkeypatch.setattr(store, "load", forbidden)
    frame = session.read(context, ResultReadRequest(result_id))
    recovered = []
    while True:
        assert frame["delivery"]["unit"] == "records"
        assert frame["delivery"]["total_items"] == 31
        assert frame["delivery"]["count_scope"] == "produced-traversal-window"
        assert {key: value for key, value in frame["result"].items() if key != "records"} == {
            key: value for key, value in payload.items() if key != "records"}
        recovered.extend(frame["result"]["records"])
        continuation = frame["delivery"]["continuation"]
        if continuation is None:
            break
        frame = session.read(context, ResultReadRequest(result_id, continuation=continuation))
    assert recovered == payload["records"]


@pytest.mark.parametrize("phase,status", [("admit", "ok"), ("publish", "ok"),
                                          ("publish", "partial"), ("save_view", "partial")])
def test_capacity_boundary_preserves_source_truth_and_owning_context_can_clear(
        command_vault_clone, monkeypatch, caplog, phase, status):
    from dataclasses import replace
    from command_application import context_for
    from _application.application import CommandApplication
    from _application.projection import canonical_result_envelope
    from _application.receipts import CommittedEffect
    from _application.results import CommandError, ErrorCode, Partial
    from _command_interface.result_delivery import ResultDeliverySession
    from _command_interface.result_store import FileResultStore, ResultStoreError, ResultStoreLimits
    root = command_vault_clone.vault_root
    base = context_for(root, profile="operator")
    principal = base.authorisation.service.identity.principal
    limits = ResultStoreLimits(max_result_bytes=200000, max_owner_bytes=500000, max_brain_bytes=1000000)
    store = FileResultStore(root, base.selected_brain.brain_id, principal, limits)
    credential = store.issue()
    delivery = ResultDeliverySession(store, credential)
    context = replace(base, result_delivery=delivery)
    catalogue = current_application_catalogue()
    adapter = ApplicationAdapter(catalogue, current_request_resolver())
    entry = next(entry for entry in catalogue.entries if entry.command_id == "artefact.create")
    effects = (CommittedEffect("artefact.created", "雪😀" * 3000 if phase == "save_view" else "Notes/created.md"),)
    source = (Ok(entry.command_id, entry.command_version, {"created": True}, committed_effects=effects)
              if status == "ok" else Partial(entry.command_id, entry.command_version,
                  CommandError(ErrorCode.CONFLICT, "A secondary source step failed."), effects))
    calls = []
    marker = root / "capacity-source-marker"
    def execute(self, request):
        calls.append(request)
        marker.write_text("committed once")
        delivery.source_finished(context, entry, None, source)
        return source
    secret = "backend-path-and-credential-must-not-be-echoed"
    def capacity(*args, **kwargs):
        raise ResultStoreError("capacity_exceeded", secret)
    other = store.authenticate(store.issue())
    other_ticket = store.admit(other, "test.read", "other")
    other_id = store.publish(other, other_ticket, {"sentinel": "other namespace"}, {})
    with monkeypatch.context() as fault:
        fault.setattr(CommandApplication, "invoke", execute)
        fault.setattr(store, phase, capacity)
        projection = adapter.invoke(context, entry.command_id, {"type": "living/wiki", "title": "Capacity probe"})
    envelope = projection.structured_content
    message = envelope["delivery"]["message"]
    assert envelope["delivery"]["reason"] == "capacity_exceeded"
    for value in ("result=200000", "owner=500000", "Brain=1000000", "result.clear", "owning"):
        assert value in message
    assert secret not in str(envelope) + caplog.text
    if phase == "admit":
        assert calls == [] and not marker.exists()
        assert envelope["status"] == "error"
        assert envelope["error"]["code"] == "capability_unavailable"
        assert envelope["error"]["effects"] == "none"
        assert "No command was executed" in message
    else:
        assert len(calls) == 1 and marker.read_text() == "committed once"
        assert projection.result == source
        assert envelope["status"] == status
        assert envelope["committed_effects"] == canonical_result_envelope(source).get(
            "committed_effects", canonical_result_envelope(source)["result"].get("committed_effects"))
        assert "Do not rerun a mutation" in message
        guarded, concise = script._guard_cli_result(envelope, projection.concise_text)
        assert guarded["delivery"]["reason"] == "capacity_exceeded"
        assert "result.clear" in guarded["delivery"]["message"]
        assert script._cli_result_fits(guarded, concise)
        if phase == "save_view":
            assert guarded["committed_effect_summary"]["count"] == 1
            import hashlib
            import io
            import json
            import sys
            from copy import deepcopy
            from brain_mcp import proxy
            from brain_mcp._result_content import result_text_wire, tool_result_fits
            wire_envelope = deepcopy(envelope)
            wire_envelope["result_ref"] = {"result_id": envelope["delivery"]["result_id"]}
            issued = {"credential": "synthetic-one-time-credential", "instruction": "Keep private."}
            wire_envelope["result_session"] = issued
            wire_envelope["warnings"] = [{"code": "follow_up_required", "message":
                "Brain MCP transport observation: call brain_proxy_status({}) before host discovery."}]
            wire = {"structuredContent": wire_envelope,
                    "content": result_text_wire(projection.concise_text, wire_envelope), "isError": True}
            stream = io.BytesIO()
            relay = proxy.Proxy(sys.executable, "brain_mcp.server", str(root))
            relay._output_stream = stream
            correlation = "correlation" * 4000
            relay._outbound.put({"jsonrpc": "2.0", "id": correlation, "result": wire})
            relay._outbound.put(None)
            relay._writer_thread()
            response = json.loads(stream.getvalue())
            assert response["id"] == correlation
            final = response["result"]
            assert tool_result_fits(final)
            output = final["structuredContent"]
            assert output["status"] == "partial" and final["isError"] is True
            assert output["delivery"]["reason"] == "capacity_exceeded"
            assert output["delivery"]["capacity_limits"] == {
                "result_bytes": 200000, "owner_bytes": 500000, "brain_bytes": 1000000}
            assert "result.clear" in output["delivery"]["message"]
            assert "Do not rerun a mutation" in output["delivery"]["message"]
            assert output["result_ref"] == wire_envelope["result_ref"]
            assert output["result_session"] == issued
            assert any(warning["code"] == "follow_up_required" and "brain_proxy_status({})" in warning["message"]
                       for warning in output["warnings"])
            expected = canonical_result_envelope(source)["result"]["committed_effects"]
            assert output["committed_effect_summary"]["count"] == len(expected)
            assert output["committed_effect_summary"]["sha256"] == hashlib.sha256(
                json.dumps(expected, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            assert json.loads(final["content"][0]["text"]) == output
    # Reopen through the supplied valid private credential, then use the actual
    # result.clear command, which must bypass result admission/publication.
    reopened = FileResultStore(root, base.selected_brain.brain_id, principal, limits)
    recovered = ResultDeliverySession(reopened, credential)
    cleared = adapter.invoke(replace(base, result_delivery=recovered), "result.clear", {})
    assert cleared.structured_content["status"] == "ok"
    assert cleared.structured_content["result"]["cleared"] is True
    assert store.load(other, other_id)["envelope"] == {"sentinel": "other namespace"}
    assert reopened.authenticate(credential) == delivery.owner


def test_real_composer_capacity_issuance_is_safe_and_actionable_on_both_transports(tmp_path, monkeypatch, caplog):
    import io
    import json
    from _command_interface.direct import DirectContextComposer, compose_direct_context
    from _command_interface.result_store import FileResultStore, ResultStoreError, ResultStoreLimits
    root = tmp_path / "Brain"
    (root / ".brain-core").mkdir(parents=True)
    (root / ".brain-core/VERSION").write_text("0.73.0\n")
    secret = "secret-backend-path-holder-credential"
    def capacity(self):
        raise ResultStoreError("capacity_exceeded", secret)
    def forbidden(*args, **kwargs):
        pytest.fail("capacity issuance failure reached the domain source")
    monkeypatch.setattr(FileResultStore, "issue", capacity)
    monkeypatch.setattr(ApplicationAdapter, "invoke", forbidden)
    composer = DirectContextComposer(vault_root=root)
    def factory(**metadata):
        return composer.compose(command_id=metadata["command_id"], result_session="new")
    try:
        result = asyncio.run(_registered(factory).call_tool("session_start", {"brain_session": "new"}))
        envelope = result.structured_content
        assert envelope["error"]["code"] == "capability_unavailable"
        assert envelope["delivery"]["reason"] == "capacity_exceeded"
        assert "No command was executed" in envelope["error"]["message"]
        assert str(ResultStoreLimits().max_brain_bytes) in envelope["delivery"]["message"]
    finally:
        composer.close()
    out, err = io.StringIO(), io.StringIO()
    assert script.run(["session", "start", "--vault", str(root), "--json"],
                      context_factory=compose_direct_context, stdout=out, stderr=err) == 3
    assert json.loads(out.getvalue())["delivery"]["reason"] == "capacity_exceeded"
    assert secret not in str(result) + out.getvalue() + err.getvalue() + caplog.text


def test_unknown_context_failures_remain_generic_instead_of_becoming_capacity(tmp_path, caplog):
    import io
    root = tmp_path / "Brain"
    (root / ".brain-core").mkdir(parents=True)
    (root / ".brain-core/VERSION").write_text("0.73.0\n")
    secret = "unexpected-backend-secret"
    def fail(**kwargs):
        raise RuntimeError(secret)
    result = asyncio.run(_registered(fail).call_tool("session_start", {}))
    assert result.structured_content["error"]["code"] == "internal_error"
    out, err = io.StringIO(), io.StringIO()
    assert script.run(["session", "start", "--vault", str(root), "--json"],
                      context_factory=fail, stdout=out, stderr=err) == 4
    assert "internal_error" in err.getvalue()
    assert secret not in str(result) + out.getvalue() + err.getvalue() + caplog.text


@pytest.mark.parametrize("limits", [
    {"result_bytes": True, "owner_bytes": 1, "brain_bytes": 1},
    {"result_bytes": "backend-secret", "owner_bytes": 1, "brain_bytes": 1},
    {"result_bytes": 1, "owner_bytes": 1, "brain_bytes": 2**64},
    {"result_bytes": 1, "owner_bytes": 1, "brain_bytes": 1, "secret": "backend-secret"},
])
def test_capacity_final_guards_rebuild_only_from_validated_primitives(limits):
    from brain_mcp._result_content import guard_tool_result, result_text_wire, tool_result_fits
    envelope = {"schema": "brain.command-result/1", "command": "artefact.create", "command_version": 5,
                "status": "partial", "committed_effects": [{"subject": "雪😀" * 5000}],
                "result": None, "delivery": {"reason": "capacity_exceeded", "message": "backend-secret",
                                              "capacity_limits": limits}}
    cli, concise = script._guard_cli_result(envelope, "partial")
    wire = guard_tool_result({"structuredContent": envelope, "content": result_text_wire("partial", envelope), "isError": True})
    assert tool_result_fits(wire) and script._cli_result_fits(cli, concise)
    for output in (cli, wire["structuredContent"]):
        assert output["delivery"]["reason"] == "capacity_exceeded"
        assert "capacity_limits" not in output["delivery"]
        assert "result.clear" in output["delivery"]["message"]
        assert "backend-secret" not in str(output)


def test_supported_results_exports_delivery_failure_for_custom_ports():
    from brain_application.results import DeliveryFailure
    from _application.delivery_failure import DeliveryFailure as InternalFailure
    assert DeliveryFailure is InternalFailure
    failure = DeliveryFailure("capacity_exceeded", "Safe recovery guidance.", capacity_limits={
        "result_bytes": 1, "owner_bytes": 2, "brain_bytes": 3})
    assert failure.reason == "capacity_exceeded"
    assert str(failure) == "Safe recovery guidance."


def test_representation_limit_keeps_retained_outcome_recoverable_without_clear(retained_detail_system, monkeypatch):
    from brain_application.results import DeliveryFailure
    from _application.projection import canonical_result_envelope, unavailable_delivery_envelope
    from _application.receipts import CommittedEffect
    from _application.results import CommandError, ErrorCode, Partial
    from _application.result.read import ResultReadRequest
    from brain_mcp._result_content import guard_tool_result, result_text_wire, tool_result_fits
    context, store, session, _ = retained_detail_system
    subject = "雪😀" * 6000
    source = Partial("test.write", 1, CommandError(ErrorCode.CONFLICT, "Secondary step failed."),
                     (CommittedEffect("saved", subject),))
    ticket = session.begin(context, "test.write")
    session._authority = {"command_id": "test.write", "stamp": None}
    with pytest.raises(DeliveryFailure) as caught:
        session.finish(context, SimpleNamespace(), source, ticket)
    assert caught.value.reason == "representation_too_large"
    assert caught.value.capacity_limits is None
    assert "detail=@outcome" in caught.value.message
    result_id = ticket["result_id"]
    assert store.load(session.owner, result_id)["envelope"] == canonical_result_envelope(source)
    unavailable = unavailable_delivery_envelope(canonical_result_envelope(source), caught.value.message,
                                                reason=caught.value.reason)
    unavailable["delivery"]["result_id"] = result_id
    cli, concise = script._guard_cli_result(unavailable, "partial")
    final = guard_tool_result({"structuredContent": unavailable,
        "content": result_text_wire("partial", unavailable), "isError": True})
    assert script._cli_result_fits(cli, concise) and tool_result_fits(final)
    for output in (cli, final["structuredContent"]):
        assert output["status"] == "partial"
        assert output["delivery"]["reason"] == "representation_too_large"
        assert output["delivery"]["result_id"] == result_id
        assert "detail=@outcome" in output["delivery"]["message"]
        assert "result.clear" not in output["delivery"]["message"]
        assert "capacity_limits" not in output["delivery"]
    root = session.read(context, ResultReadRequest(result_id, detail="@outcome"))
    def forbidden(*args, **kwargs):
        pytest.fail("cached outcome recovery reloaded the source body")
    monkeypatch.setattr(store, "load", forbidden)
    pending = [root]
    seen = set()
    fragments = []
    def references(value):
        if isinstance(value, dict):
            if "detail_reference" in value:
                yield value["detail_reference"]
            else:
                for item in value.values():
                    yield from references(item)
        elif isinstance(value, list):
            for item in value:
                yield from references(item)
    while pending:
        page = pending.pop(0)
        assert page["status"] == "partial"
        wire = {"structuredContent": page, "content": result_text_wire("partial", page), "isError": True}
        assert tool_result_fits(wire)
        fragments.extend(fragment["text"] + fragment["line_ending"]
                         for fragment in page["result"].get("fragments", []))
        addresses = list(references(page["result"]))
        if page["delivery"]["continuation"] is not None:
            addresses.append(page["delivery"]["continuation"]["detail"])
        for address in addresses:
            if address not in seen:
                seen.add(address)
                pending.append(session.read(context, ResultReadRequest(result_id, detail=address)))
    assert "".join(fragments) == subject
    assert seen


@pytest.mark.parametrize("revoke", ["source", "publication", None])
@pytest.mark.parametrize("source_error", [True, False])
def test_real_read_initial_disclosure_and_error_retention(command_vault_clone, monkeypatch, revoke, source_error):
    from dataclasses import replace
    from command_application import context_for
    from _application.results import Error, ErrorCode, CommandError, RequestErrorDetails
    from _application.projection import canonical_result_envelope
    from _command_interface.result_delivery import ResultDeliverySession
    from _command_interface.result_store import FileResultStore
    from brain_mcp._command_adapter import _tool_result
    root = command_vault_clone.vault_root
    target = "Designs/project~command-fixture/Command Fixture Design.md"
    secret = "source-business-body-that-must-not-be-disclosed"
    (root / target).write_text((root / target).read_text() + "\n" + secret)
    catalogue = current_application_catalogue()
    original = next(entry for entry in catalogue.entries if entry.command_id == "artefact.read")
    calls = []
    def execute(context, request):
        calls.append(request)
        source = original.executor(context, request)
        assert isinstance(source, Ok)
        assert secret in source.result.content
        if source_error:
            source = Error(original.command_id, original.command_version,
                CommandError(ErrorCode.CONFLICT, secret, RequestErrorDetails("reference", secret)))
        if revoke == "source":
            context.authorisation.service.reduce(commands=(original.command_id,))
        return source
    catalogue = replace(catalogue, entries=tuple(replace(entry, executor=execute)
        if entry.command_id == original.command_id else entry for entry in catalogue.entries))
    base = context_for(root, profile="operator", catalogue=catalogue)
    store = FileResultStore(root, base.selected_brain.brain_id, base.authorisation.service.identity.principal)
    if revoke == "publication":
        publish = store.publish
        def publish_then_revoke(*args, **kwargs):
            result_id = publish(*args, **kwargs)
            base.authorisation.service.reduce(commands=(original.command_id,))
            return result_id
        monkeypatch.setattr(store, "publish", publish_then_revoke)
    credential = store.issue()
    session = ResultDeliverySession(store, credential)
    adapter = ApplicationAdapter(catalogue, current_request_resolver())
    context = replace(base, result_delivery=session)
    projection = adapter.invoke(context, original.command_id, {"reference": target})
    envelope = projection.structured_content
    assert len(calls) == 1
    assert envelope["status"] == ("error" if source_error else "ok")
    if revoke:
        assert envelope["delivery"]["reason"] == "disclosure_ended"
        assert envelope["result"] is None and envelope["warnings"] == []
        assert envelope["committed_effects_withheld"] is True
        assert envelope["delivery"]["total_items"] is None
        assert envelope["delivery"]["total_pages"] is None
        assert "committed_effects" not in envelope and "committed_effect_summary" not in envelope
        if source_error:
            assert envelope["error"]["code"] == "conflict"
            assert envelope["error"]["effects"] == "none"
            assert "details" not in envelope["error"]
        assert secret not in str(_tool_result(projection)) + projection.concise_text
        assert target not in str(envelope)
    else:
        result_id = envelope["delivery"]["result_id"]
        assert store.load(session.owner, result_id)["envelope"] == canonical_result_envelope(projection.result)
        recovered = adapter.invoke(replace(base, result_delivery=ResultDeliverySession(store, credential)),
                                   "result.read", {"result_id": result_id, "detail": "@outcome"})
        assert recovered.structured_content["status"] == "ok"
        if source_error:
            source = recovered.structured_content["result"]["envelope"]
            assert source["status"] == "error"
            assert source["result"]["error"]["message"] == secret
        assert len(calls) == 1
        base.authorisation.service.reduce(commands=(original.command_id,))
        denied = adapter.invoke(replace(base, result_delivery=ResultDeliverySession(store, credential)),
                                "result.read", {"result_id": result_id})
        assert denied.structured_content["error"]["code"] == "authorisation_required"
        guidance = denied.structured_content["error"]["message"]
        assert "Authorisation for artefact.read" in guidance
        assert "access.status" in guidance and "access.request" in guidance and "retry result.read" in guidance
        assert secret not in str(denied.structured_content)


@pytest.mark.parametrize("status", ["ok", "partial", "error"])
def test_privacy_projection_withholds_subjects_without_changing_effect_truth(status):
    from _application.projection import disclosure_ended_envelope
    secret = "undisclosed-effect-subject"
    source = {"schema": "brain.command-result/1", "command": "test.write", "command_version": 1,
              "status": status, "result": {"items": [secret] * 90}, "warnings": [{"message": secret}],
              "committed_effects": [{"subject": secret}],
              "error": {"code": "conflict", "effects": "unknown" if status == "error" else "known",
                        "message": secret, "details": {"subject": secret},
                        "outcome_reference": {"invocation_id": "receipt-id"}}}
    output = disclosure_ended_envelope(source)
    assert output["status"] == status
    assert output["error"]["code"] == "conflict"
    assert output["error"]["effects"] == source["error"]["effects"]
    assert output["error"]["outcome_reference"] == {"invocation_id": "receipt-id"}
    assert output["committed_effects_withheld"] is True
    assert "committed_effects" not in output and "committed_effect_summary" not in output
    assert output["delivery"]["total_items"] is None
    assert secret not in str(output)


def test_safe_preflight_refusal_is_not_mislabelled_or_retained(retained_detail_system, monkeypatch):
    from _application.results import Error, ErrorCode, CommandError
    context, store, session, _ = retained_detail_system
    ticket = session.begin(context, "test.read")
    refusal = Error("test.read", 1, CommandError(ErrorCode.AUTHORISATION_REQUIRED, "Prepare and authorise this read."))
    def forbidden(*args, **kwargs):
        pytest.fail("a preflight permission refusal was retained")
    monkeypatch.setattr(store, "publish", forbidden)
    frame = session.finish(context, SimpleNamespace(), refusal, ticket)
    assert frame["error"]["code"] == "authorisation_required"
    assert frame["error"]["message"] == "Prepare and authorise this read."
    assert frame["delivery"].get("reason") != "disclosure_ended"


@pytest.mark.parametrize("partial", [False, True])
def test_business_reply_without_completed_source_proof_fails_closed(retained_detail_system, monkeypatch, partial):
    from brain_application.results import DeliveryFailure
    from _application.results import Partial, CommandError, ErrorCode
    from _application.receipts import CommittedEffect
    context, store, session, _ = retained_detail_system
    ticket = session.begin(context, "test.write")
    source = (Partial("test.write", 1, CommandError(ErrorCode.CONFLICT, "private failure"),
                      (CommittedEffect("saved", "private subject"),)) if partial
              else Ok("test.write", 1, {"private": "business body"}))
    def forbidden(*args, **kwargs):
        pytest.fail("unproved source output was published")
    monkeypatch.setattr(store, "publish", forbidden)
    with pytest.raises(DeliveryFailure) as caught:
        session.finish(context, SimpleNamespace(), source, ticket)
    assert caught.value.reason == "disclosure_ended"
    assert session._active_source is None


def test_admission_consent_refusal_is_not_mislabelled_as_storage(command_vault_clone, monkeypatch):
    from dataclasses import replace
    from command_application import context_for
    from _application.consent import ConsentError
    from _application.application import CommandApplication
    from _command_interface.result_delivery import ResultDeliverySession
    from _command_interface.result_store import FileResultStore
    base = context_for(command_vault_clone.vault_root, profile="operator")
    store = FileResultStore(command_vault_clone.vault_root, base.selected_brain.brain_id,
                            base.authorisation.service.identity.principal)
    session = ResultDeliverySession(store, store.issue())
    def denied(*args, **kwargs):
        raise ConsentError("authorisation_required", "Authorisation changed before admission.")
    def forbidden(*args, **kwargs):
        pytest.fail("admission refusal executed the source")
    monkeypatch.setattr(store, "admit", denied)
    monkeypatch.setattr(CommandApplication, "invoke", forbidden)
    adapter = ApplicationAdapter(current_application_catalogue(), current_request_resolver())
    result = adapter.invoke(replace(base, result_delivery=session), "artefact.read", {"reference": "anything"})
    assert result.structured_content["error"]["code"] == "authorisation_required"
    assert "capability_unavailable" not in str(result.structured_content)


def test_concurrent_port_reuse_cannot_replace_old_source_disclosure_proof(command_vault_clone, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from dataclasses import replace
    from threading import Event
    from command_application import context_for
    from brain_application.results import DeliveryFailure
    from _command_interface.result_delivery import ResultDeliverySession
    from _command_interface.result_store import FileResultStore
    root = command_vault_clone.vault_root
    target = "Designs/project~command-fixture/Command Fixture Design.md"
    secret = "old-source-private-output"
    (root / target).write_text((root / target).read_text() + "\n" + secret)
    catalogue = current_application_catalogue()
    original = next(entry for entry in catalogue.entries if entry.command_id == "artefact.read")
    ready, release = Event(), Event()
    calls = []
    def execute(context, request):
        calls.append(context.invocation_id)
        result = original.executor(context, request)
        assert isinstance(result, Ok) and secret in result.result.content
        ready.set()
        assert release.wait(10), "test failed to release source execution"
        return result
    catalogue = replace(catalogue, entries=tuple(replace(entry, executor=execute)
        if entry.command_id == original.command_id else entry for entry in catalogue.entries))
    base = context_for(root, profile="operator", catalogue=catalogue)
    store = FileResultStore(root, base.selected_brain.brain_id, base.authorisation.service.identity.principal)
    session = ResultDeliverySession(store, store.issue())
    adapter = ApplicationAdapter(catalogue, current_request_resolver())
    first = replace(base, invocation_id="source-A", result_delivery=session)
    second = replace(base, invocation_id="source-B", result_delivery=session)
    admitted = []
    original_admit = store.admit
    def admit(owner, command, invocation):
        admitted.append(invocation)
        return original_admit(owner, command, invocation)
    monkeypatch.setattr(store, "admit", admit)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(adapter.invoke, first, "artefact.read", {"reference": target})
        try:
            assert ready.wait(10), "source read did not reach its completion boundary"
            opening = dict(session._before)
            base.authorisation.service.reduce(commands=("artefact.read",))
            rejected = adapter.invoke(second, "command.describe", {"target_command_id": "artefact.read"})
            assert rejected.structured_content["error"]["code"] == "capability_unavailable"
            assert rejected.structured_content["delivery"]["reason"] == "invocation_in_progress"
            assert admitted == ["source-A"] and session._before == opening
            # A nested query refresh cannot complete or replace A's source proof.
            session.source_finished(replace(first, invocation_id="result-refresh-other"), original, None,
                                    Ok(original.command_id, original.command_version, {}))
            assert session._source_completed is False and session._authority is None
            with pytest.raises(DeliveryFailure):
                session.finish(second, SimpleNamespace(), Ok("command.describe", 1, {}), {})
            assert session._before == opening
        finally:
            release.set()
        completed = pending.result(timeout=10)
    assert completed.structured_content["status"] == "ok"
    assert completed.structured_content["delivery"]["reason"] == "disclosure_ended"
    assert secret not in str(completed.structured_content) + completed.concise_text
    assert calls == ["source-A"] and session._active_source is None
    # The completed/refused A has released the bounded guard; sequential use
    # still works, and a new invocation never adopts A's original proof.
    resumed = adapter.invoke(replace(second, invocation_id="source-C"), "command.describe",
                             {"target_command_id": "artefact.read"})
    assert resumed.structured_content["status"] == "ok"
    assert session._active_source is None


@pytest.mark.parametrize("command", ["access.request", "access.reduce", "runtime.status", "runtime.read-environment"])
@pytest.mark.parametrize("job", [False, True])
def test_cli_optional_cache_commands_skip_automatic_client_selection(tmp_path, monkeypatch, command, job):
    import io
    def forbidden(*args, **kwargs):
        pytest.fail("optional cache command attempted client-directory or job-lock selection")
    monkeypatch.setattr(script, "_result_client_directory", forbidden)
    monkeypatch.setattr(script, "_automatic_result_session_path", forbidden)
    monkeypatch.setattr(script, "resolve_direct_vault", lambda *args, **kwargs: tmp_path)
    seen = []
    def factory(**kwargs):
        seen.append(kwargs)
        return SimpleNamespace(result_delivery=None)
    monkeypatch.setattr(ApplicationAdapter, "invoke", lambda *args: project_adapter_result(Ok(command, 1, {})))
    attachment = SimpleNamespace(kind="cli-job", close=lambda: None) if job else None
    out, err = io.StringIO(), io.StringIO()
    noun, verb = command.split(".")
    assert script.run([noun, verb, "--json"], context_factory=factory, owner_attachment=attachment,
                      stdout=out, stderr=err) == 0
    assert "result_session" not in seen[0]
    assert not (tmp_path / ".brain/local/result-clients").exists()


@pytest.mark.parametrize("command,payload", [("result.clear", {}), ("result.read", {"result_id": "a" * 64}),
                                           ("result.search", {"result_id": "a" * 64, "literal": "x"})])
def test_omitted_navigation_selection_never_creates_a_client_or_owner(tmp_path, monkeypatch, command, payload):
    import io
    import json
    from _command_interface.direct import DirectContextComposer, InvalidResultSession
    from _command_interface.result_store import FileResultStore
    root = tmp_path / "Brain"
    (root / ".brain-core").mkdir(parents=True)
    (root / ".brain-core/VERSION").write_text("0.73.0\n")
    def forbidden(*args, **kwargs):
        pytest.fail("unselected result navigation attempted client selection, owner issuance or source invocation")
    monkeypatch.setattr(FileResultStore, "issue", forbidden)
    monkeypatch.setattr(script, "_result_client_directory", forbidden)
    monkeypatch.setattr(script, "_automatic_result_session_path", forbidden)
    monkeypatch.setattr(ApplicationAdapter, "invoke", forbidden)
    composer = DirectContextComposer(vault_root=root)
    try:
        with pytest.raises(InvalidResultSession):
            composer.compose(command_id=command)
        def factory(**kwargs):
            return composer.compose(command_id=kwargs["command_id"])
        result = asyncio.run(_registered(factory).call_tool(command.replace(".", "_"), payload))
        assert result.structured_content["error"]["code"] == "invalid_request"
    finally:
        composer.close()
    out, err = io.StringIO(), io.StringIO()
    noun, verb = command.split(".")
    assert script.run([noun, verb, "--vault", str(root), "--json", "--request-json", json.dumps(payload)],
                      context_factory=forbidden, stdout=out, stderr=err) == 2
    assert "result session" in err.getvalue()
    assert not (root / ".brain/local/result-clients").exists()
    assert not (root / ".brain/local/results").exists()


def test_job_navigation_requires_then_reuses_private_channel_namespace(tmp_path, monkeypatch):
    import io
    import json
    from _bootstrap.consent_owner import ConsentOwner
    from _bootstrap.owner_attachment import OwnerAttachment
    from _command_interface.direct import DirectContextComposer, InvalidResultSession, compose_direct_context
    from _command_interface.result_store import FileResultStore
    root = tmp_path / "Brain"
    (root / ".brain-core").mkdir(parents=True)
    (root / ".brain-core/VERSION").write_text("0.73.0\n")
    owner = ConsentOwner(root)
    composer = DirectContextComposer(vault_root=root, owner_attachment=OwnerAttachment.for_job(owner),
                                     owner_initialisation_allowed=True)
    def forbidden(*args, **kwargs):
        pytest.fail("navigation attempted optional client selection or new namespace issuance")
    try:
        with monkeypatch.context() as scope:
            scope.setattr(FileResultStore, "issue", forbidden)
            with pytest.raises(InvalidResultSession):
                composer.compose(command_id="result.clear")
        opened = composer.compose(command_id="session.start").result_delivery
        ticket = opened.store.admit(opened.owner, "test.read", "job-result")
        result_id = opened.store.publish(opened.owner, ticket, {"job": "retained"}, {})
        monkeypatch.setattr(FileResultStore, "issue", forbidden)
        monkeypatch.setattr(script, "_result_client_directory", forbidden)
        monkeypatch.setattr(script, "_automatic_result_session_path", forbidden)
        selected = composer.compose(command_id="result.clear").result_delivery
        assert selected.owner == opened.owner
        out, err = io.StringIO(), io.StringIO()
        assert script.run(["result", "clear", "--vault", str(root), "--json"],
            owner_attachment=OwnerAttachment.for_job(owner), context_factory=compose_direct_context,
            stdout=out, stderr=err) == 0
        assert json.loads(out.getvalue())["result"]["cleared"] is True
        from _command_interface.result_store import ResultStoreError
        with pytest.raises(ResultStoreError):
            opened.store.load(opened.owner, result_id)
        assert not (root / ".brain/local/result-clients").exists()
    finally:
        composer.close()
        owner.close()


def test_cli_clear_with_valid_private_file_reuses_existing_owner(tmp_path, monkeypatch):
    import io
    import json
    from _command_interface.direct import DirectContextComposer, compose_direct_context
    from _command_interface.result_store import FileResultStore, ResultStoreError
    root = tmp_path / "Brain"
    (root / ".brain-core").mkdir(parents=True)
    (root / ".brain-core/VERSION").write_text("0.73.0\n")
    composer = DirectContextComposer(vault_root=root)
    try:
        opened = composer.compose(command_id="session.start", result_session="new").result_delivery
        path = tmp_path / "private-credential"
        script._write_result_session(path, opened.credential)
        ticket = opened.store.admit(opened.owner, "test.read", "standalone-result")
        result_id = opened.store.publish(opened.owner, ticket, {"retained": True}, {})
        def forbidden(*args, **kwargs):
            pytest.fail("explicit-file cleanup issued a new namespace")
        monkeypatch.setattr(FileResultStore, "issue", forbidden)
        out, err = io.StringIO(), io.StringIO()
        assert script.run(["result", "clear", "--vault", str(root), "--result-session-file", str(path), "--json"],
                          context_factory=compose_direct_context, stdout=out, stderr=err) == 0
        assert json.loads(out.getvalue())["result"]["cleared"] is True
        with pytest.raises(ResultStoreError):
            opened.store.load(opened.owner, result_id)
        assert opened.credential not in out.getvalue() + err.getvalue()
        assert path.read_text() == opened.credential
    finally:
        composer.close()


def test_list_error_retention_is_immutable_not_a_live_query(retained_detail_system, monkeypatch):
    from _application.results import Error, ErrorCode, CommandError
    from _application.result.read import ResultReadRequest
    context, store, session, _ = retained_detail_system
    request = SimpleNamespace(location=SimpleNamespace(value="active"), cursor=None, page_size=25)
    monkeypatch.setattr(session, "_revision", lambda *args: "original-revision")
    ticket = session.begin(context, "artefact.list", request)
    session._authority = {"command_id": "artefact.list", "stamp": None}
    error = Error("artefact.list", 1, CommandError(ErrorCode.CONFLICT, "Original failed list outcome."))
    frame = session.finish(context, request, error, ticket)
    result_id = frame["delivery"]["result_id"]
    assert store.metadata(session.owner, result_id)["query"] is None
    def forbidden(*args, **kwargs):
        pytest.fail("retained Error was refreshed into a future successful query")
    monkeypatch.setattr(session, "_revision", forbidden)
    monkeypatch.setattr(session, "_refresh", forbidden)
    recovered = session.read(context, ResultReadRequest(result_id))
    assert recovered["status"] == "error"
    assert recovered["error"]["message"] == "Original failed list outcome."
    assert recovered["delivery"]["backing"] == "retained"


@pytest.mark.parametrize("tool,payload", [("result_read", {"result_id": "a" * 64}),
                                        ("result_search", {"result_id": "a" * 64, "literal": "x"}),
                                        ("result_clear", {})])
def test_mcp_navigation_schema_and_raw_handler_require_existing_proof(monkeypatch, tool, payload):
    calls = []
    def factory(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(invocation_id="proof-test", derived_snapshots=None)
    def invoke(self, context, command, arguments):
        assert "brain_session" not in arguments
        assert arguments == payload
        return project_adapter_result(Ok(command, 1, {}))
    monkeypatch.setattr(ApplicationAdapter, "invoke", invoke)
    server = _registered(factory)
    registered = server._tool_manager.get_tool(tool)
    assert "brain_session" in registered.parameters["required"]
    assert inspect.signature(registered.fn).parameters["brain_session"].default is inspect.Parameter.empty
    assert "brain_session" not in current_request_resolver().resolve(tool.replace("_", "."), payload).__dataclass_fields__
    refused = asyncio.run(server.call_tool(tool, payload))
    assert refused.structured_content["error"]["code"] == "invalid_request"
    assert calls == []
    refused_new = asyncio.run(server.call_tool(tool, {**payload, "brain_session": "new"}))
    assert refused_new.structured_content["error"]["code"] == "invalid_request"
    assert calls == []
    selected = asyncio.run(server.call_tool(tool, {**payload, "brain_session": "b" * 64}))
    assert selected.structured_content["status"] == "ok"
    assert calls[0]["result_session"] == "b" * 64


def test_default_no_argument_context_composition_still_issues_one_shot_identity(tmp_path):
    from _command_interface.direct import DirectContextComposer
    root = tmp_path / "Brain"
    (root / ".brain-core").mkdir(parents=True)
    (root / ".brain-core/VERSION").write_text("0.73.0\n")
    composer = DirectContextComposer(vault_root=root)
    try:
        first = composer.compose().result_delivery
        second = composer.compose().result_delivery
        assert first.owner != second.owner
    finally:
        composer.close()
