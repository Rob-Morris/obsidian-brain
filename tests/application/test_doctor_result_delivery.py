"""Doctor consumes actual bounded layouts, including linked nested details."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cli"))

from _application.result_navigation import build_view
from _command_interface.result_cursor import ResultCursor
from _launcher.doctor_results import complete_check_result


class Pages:
    """Transport surrogate; layouts and opaque navigation use their real owners."""

    result_id = "doctor-owned-result"

    def __init__(self, payload, **layout):
        self.envelope = {"schema": "brain.command-result/1", "command": "vault.check",
                         "command_version": 1, "status": "ok", "warnings": [],
                         "committed_effects": [], "result": payload}
        self.view = build_view(self.envelope, **layout)
        self.codec = ResultCursor("doctor-test-session")
        self.calls = []

    def stamp(self, frame):
        frame = deepcopy(frame)
        delivery = frame["delivery"]
        delivery["result_id"] = self.result_id
        if "detail_id" in delivery:
            following = delivery.get("next_detail")
            delivery["has_more"] = following is not None
            delivery["continuation"] = (self.codec.encode(self.result_id, {
                "result_id": self.result_id, "detail": following,
            }) if following is not None else None)
        elif delivery.get("continuation"):
            delivery["continuation"] = self.codec.encode(self.result_id, {
                **delivery["continuation"], "result_id": self.result_id,
            })
        return frame

    def first(self):
        frame = self.stamp(self.view.page())
        frame["result_session_file"] = "/private/doctor-result-session"
        return frame

    def read(self, request):
        self.calls.append(request)
        assert request["result_id"] == self.result_id
        coordinate = (self.codec.decode(self.result_id, request["continuation"])
                      if isinstance(request.get("continuation"), str) else request.get("continuation", request))
        if "detail" in coordinate:
            return self.stamp(self.view.details[coordinate["detail"]])
        return self.stamp(self.view.page(coordinate["page"]))


def payload(count, *, message=None):
    return {"findings": [
        {"check": "example", "severity": "warning", "file": f"Wiki/{i}.md",
         "message": message or f"finding {i}", "fix": None, "repair": None, "code": None}
        for i in range(count)
    ], "errors": 0, "warnings": count, "info": 0}


@pytest.mark.parametrize("count,message,layout", [
    (0, None, {}), (1, None, {}), (120, None, {}),
    (2, "長い finding 雪\n" * 1200, {}),
    (40, None, {"page_size": 1}),
])
def test_complete_check_result_preserves_every_finding_and_scalar(count, message, layout):
    original = payload(count, message=message)
    source = Pages(original, **layout)
    assert complete_check_result(source.first(), source.read) == original
    if count > 2 or message:
        assert source.calls
        assert any("continuation" in call for call in source.calls)
    else:
        assert not source.calls


def test_repeated_detail_is_recovered_once():
    source = Pages(payload(3, message="Repeated message " * 1000))
    assert complete_check_result(source.first(), source.read) == source.envelope["result"]
    details = [call["detail"] for call in source.calls if "detail" in call]
    assert len(details) == len(set(details))


@pytest.mark.parametrize("status", ["unavailable", "out_of_range"])
def test_unavailable_delivery_cannot_become_a_healthy_empty_diagnosis(status):
    source = Pages(payload(0))
    first = source.first()
    first["delivery"]["status"] = status
    with pytest.raises(ValueError, match="recovery is unavailable"):
        complete_check_result(first, source.read)


@pytest.mark.parametrize("fault", ["missing_cursor", "repeated_cursor", "index_gap", "text_gap"])
def test_incomplete_delivery_is_not_accepted_as_a_complete_diagnosis(fault):
    source = Pages(payload(150, message="Long message " * 800 if fault == "text_gap" else None))
    first = source.first()
    saved = {}

    def read(request):
        frame = source.read(request)
        if fault in ("missing_cursor", "repeated_cursor") and frame["delivery"].get("has_more"):
            if fault == "missing_cursor":
                frame["delivery"]["continuation"] = None
            else:
                saved.setdefault("cursor", frame["delivery"]["continuation"])
                frame["delivery"]["continuation"] = saved["cursor"]
        elif fault == "index_gap" and "fields" in frame["result"]:
            frame["result"]["fields"][0]["index"] = 999
        elif fault == "text_gap" and "content_range" in frame["result"]:
            frame["result"]["content_range"]["start"] += 1
        return frame

    with pytest.raises(ValueError, match="vault.check recovery"):
        complete_check_result(first, read)


@pytest.mark.parametrize("nested", [False, True])
def test_doctor_uses_the_same_session_and_never_reexecutes_the_check(tmp_path, monkeypatch, nested):
    from _common import _venv
    from _launcher.doctor import run_vault_check
    from _local_cli import runtime

    root = tmp_path.resolve()
    (root / ".brain-core").mkdir()
    (root / ".brain-core/VERSION").write_text("0.76.1\n")
    source = Pages(payload(80))
    commands = []
    monkeypatch.setattr(runtime, "command_python", lambda *_: Path(sys.executable))

    def run(argv, **kwargs):
        command = ".".join(argv[2:4])
        commands.append(command)
        request = json.loads(argv[argv.index("--request-json") + 1])
        if command == "vault.check":
            frame = source.first()
            assert request == {"actionable": False, "severity": "warning"}
        else:
            assert command == "result.read"
            assert argv[argv.index("--result-session-file") + 1] == "/private/doctor-result-session"
            frame = source.read(request)
            if nested:
                delivery = frame.pop("delivery")
                if delivery.get("continuation"):
                    delivery["continuation"] = source.codec.decode(source.result_id, delivery["continuation"])
                frame = {"schema": "brain.command-result/1", "command": "result.read", "command_version": 1,
                         "status": "ok", "result": {"envelope": frame}, "delivery": delivery}
        assert 0 < kwargs["timeout"] <= 120
        return subprocess.CompletedProcess(argv, 0, json.dumps(frame), "")

    monkeypatch.setattr(_venv, "run_managed", run)
    result = run_vault_check(root, actionable=False, severity="warning")
    assert commands.count("vault.check") == 1
    assert len(result["findings"]) == 80
    assert result["findings"][-1]["message"] == "finding 79"
    assert result["summary"] == {"errors": 0, "warnings": 80, "info": 0}


@pytest.mark.parametrize("fault", ["null_descriptor", "restricted", "contradictory", "missing_totals", "unavailable_page", "late_page", "compact_items"])
def test_bad_delivery_is_unavailable_through_the_collector(tmp_path, monkeypatch, fault):
    import doctor
    from _common import _venv
    from _launcher.doctor import run_vault_check
    from _local_cli import runtime

    root = tmp_path.resolve()
    (root / ".brain-core").mkdir()
    (root / ".brain-core/VERSION").write_text("0.76.1\n")
    source = Pages(payload(80 if fault == "unavailable_page" else 0))
    monkeypatch.setattr(runtime, "command_python", lambda *_: Path(sys.executable))

    def run(argv, **kwargs):
        request = json.loads(argv[argv.index("--request-json") + 1])
        if argv[2:4] == ["vault", "check"]:
            frame = source.first()
            if fault == "null_descriptor":
                frame["delivery"] = None
            elif fault == "restricted":
                frame["delivery"]["has_more_outside_selection"] = True
            elif fault == "contradictory":
                frame["delivery"].update(total_pages=10, remaining_pages=9, has_more=False)
            elif fault == "missing_totals":
                frame["delivery"].pop("total_pages")
            elif fault == "late_page":
                frame["delivery"].update(page=2, total_pages=2, remaining_pages=0, has_more=False)
            elif fault == "compact_items":
                frame["delivery"]["total_items"] += 1
        else:
            frame = source.read(request)
            if "continuation" in request:
                frame["delivery"]["status"] = "unavailable"
        return subprocess.CompletedProcess(argv, 0, json.dumps(frame), "")

    monkeypatch.setattr(_venv, "run_managed", run)
    result = doctor.collect_vault_diagnosis(current_vault=str(root), launcher_python=sys.executable,
        actionable=False, severity=None, vault_check_runner=run_vault_check)
    assert not result["available"]
    assert result["exit_code"] != 0
    assert result["result"] is None


def test_released_fragment_details_reconstruct_unicode_without_duplicating_content():
    original = payload(1, message="雪" * 5000)
    source = Pages(original)

    def read(request):
        frame = source.read(request)
        if "content" in frame["result"]:
            text, span = frame["result"]["content"], frame["result"]["content_range"]
            frame["result"] = {"fragments": [{
                "line": 1, "start": span["start"], "end": span["end"], "text": text,
                "line_ending": "", "line_complete": span["end"] == span["total"],
            }], "content": text}
            frame["delivery"].update(unit="lines", total_items=frame["delivery"]["total_pages"])
        return frame

    assert complete_check_result(source.first(), read) == original


@pytest.mark.parametrize("fault", ["page_total", "text_total", "cycle"])
def test_recovery_rejects_changed_totals_and_cyclic_details(fault):
    source = Pages(payload(80, message="long text 雪 " * 1000 if fault == "text_total" else None))

    def read(request):
        frame = source.read(request)
        if fault == "page_total" and "continuation" in request:
            frame["delivery"]["total_items"] += 1
        elif fault == "text_total" and "content_range" in frame["result"] and frame["result"]["content_range"]["start"]:
            frame["result"]["content_range"]["total"] += 1
        elif fault == "cycle" and "detail" in request:
            frame["result"] = {"again": {"action": "result.read", "detail_reference": request["detail"]}}
            frame["delivery"].update(has_more=False, total_pages=1, remaining_pages=0, continuation=None)
        return frame

    with pytest.raises(ValueError, match="vault.check recovery"):
        complete_check_result(source.first(), read)


def test_failed_result_recovery_reports_unavailable_not_healthy(tmp_path, monkeypatch):
    import doctor
    from _common import _venv
    from _launcher.doctor import run_vault_check
    from _local_cli import runtime

    root = tmp_path.resolve()
    (root / ".brain-core").mkdir()
    (root / ".brain-core/VERSION").write_text("0.76.1\n")
    source = Pages(payload(80))
    monkeypatch.setattr(runtime, "command_python", lambda *_: Path(sys.executable))

    def run(argv, **kwargs):
        if argv[2:4] == ["vault", "check"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps(source.first()), "")
        frame = {"schema": "brain.command-result/1", "command": "result.read", "command_version": 2,
                 "status": "error", "warnings": [], "result": None, "committed_effects": [],
                 "error": {"code": "not_found", "message": "expired", "effects": "none", "retryable": False}}
        return subprocess.CompletedProcess(argv, 2, json.dumps(frame), "")

    monkeypatch.setattr(_venv, "run_managed", run)
    result = doctor.collect_vault_diagnosis(current_vault=str(root), launcher_python=sys.executable,
        actionable=False, severity=None, vault_check_runner=run_vault_check)
    assert not result["available"]
    assert result["exit_code"] != 0
    assert result["result"] is None
    assert "run brain vault check --json" in result["message"]


@pytest.mark.parametrize("fault", ["identity", "version", "session", "deadline", "wrapped_identity"])
def test_recovery_rejects_context_changes_and_a_spent_total_deadline(tmp_path, monkeypatch, fault):
    import time
    from _common import _venv
    from _launcher.doctor import run_vault_check
    from _local_cli import runtime

    root = tmp_path.resolve()
    (root / ".brain-core").mkdir()
    (root / ".brain-core/VERSION").write_text("0.76.1\n")
    source = Pages(payload(80))
    monkeypatch.setattr(runtime, "command_python", lambda *_: Path(sys.executable))
    calls = []
    if fault == "deadline":
        clock = iter((0, 1, 121))
        monkeypatch.setattr(time, "monotonic", lambda: next(clock))

    def run(argv, **kwargs):
        calls.append(argv[2:4])
        if argv[2:4] == ["vault", "check"]:
            frame = source.first()
            if fault == "session":
                frame.pop("result_session_file")
        else:
            request = json.loads(argv[argv.index("--request-json") + 1])
            frame = source.read(request)
            if fault == "identity":
                frame["command"] = "artefact.read"
            elif fault == "version":
                frame["command_version"] += 1
            elif fault == "wrapped_identity":
                delivery = frame.pop("delivery")
                delivery["result_id"] = "other-retained-result"
                frame = {"schema": "brain.command-result/1", "command": "result.read", "command_version": 1,
                         "status": "ok", "result": {"envelope": frame}, "delivery": delivery}
        return subprocess.CompletedProcess(argv, 0, json.dumps(frame), "")

    monkeypatch.setattr(_venv, "run_managed", run)
    with pytest.raises((RuntimeError, ValueError), match="vault.check"):
        run_vault_check(root, actionable=False, severity=None)
    assert calls.count(["vault", "check"]) == 1
    if fault in ("session", "deadline"):
        assert len(calls) == 1


def test_doctor_real_command_round_trip_recovers_bounded_findings(command_vault_clone, tmp_path, monkeypatch):
    import os
    from _common import _venv
    from _launcher.doctor import run_vault_check
    from _local_cli import runtime

    clone = command_vault_clone
    root = clone.vault_root
    home = tmp_path / "home"
    home.mkdir()
    for name in tuple(os.environ):
        if name.startswith("BRAIN_"):
            monkeypatch.delenv(name)
    for name, value in clone.environment.items():
        monkeypatch.setenv(name, value)
    for name in ("HOME", "USERPROFILE", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(home))
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent))
    monkeypatch.setattr(runtime, "command_python", lambda *_: Path(sys.executable))
    for index in range(120):
        (root / f"Loose-{index:03}.md").write_text("Content outside an artefact folder.\n")

    real_run = _venv.run_managed
    observed = []

    def run(argv, **kwargs):
        completed = real_run(argv, **kwargs)
        observed.append((argv[2:4], json.loads(completed.stdout)))
        return completed

    monkeypatch.setattr(_venv, "run_managed", run)
    result = run_vault_check(root, actionable=True, severity="error")
    roots = [finding for finding in result["findings"] if finding["check"] == "root_files"]
    assert len(roots) == 120
    assert {finding["file"] for finding in roots} == {f"Loose-{index:03}.md" for index in range(120)}
    assert observed[0][0] == ["vault", "check"]
    assert "detail_reference" in observed[0][1]["result"]["findings"]
    assert len(observed) > 2
    assert all(command == ["result", "read"] for command, _ in observed[1:])
