"""Managed preparation rejects changed inputs before providers or domain writes."""

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import json

import pytest

from _application.preparation import content_digest
from _application.registry import current_application_catalogue
from _application._managed_preparation import prepare_maintenance
from _application.content._preparation import prepare_ingestion
from _application.content.ingest import ContentIngestRequest, execute as ingest
from _application.content.classify import ContentClassifyMode
from _application._mutation_support import InlineContent, StagedContent
from _application.shaping._render_preparation import prepare_render
from _application.shaping.render import ShapingRenderRequest, PresentationOutput, execute as render
from _application.shaping._start_preparation import prepare_session
from _application.shaping.start import ShapingStartRequest, ShapingMode, execute as start
from _application.retrieval._preparation import prepare_benchmark
from _application.retrieval.evaluate import RetrievalEvaluateRequest, execute as evaluate
from _application.retrieval.rebuild_semantic import RetrievalRebuildSemanticRequest
from _application.retrieval.repair_semantic import RetrievalRepairSemanticRequest
from _application.retrieval.enable import RetrievalEnableRequest
from _application.retrieval.refresh_lexical import RetrievalRefreshLexicalRequest
from _application.runtime.refresh_router import RuntimeRefreshRouterRequest
from _application.runtime.warmup import RuntimeWarmupRequest
from _staging import stage_body, _handle_path
from command_application import application_for
from test_workspace_skill_preparation import MatchingAdmission
from test_shaping_start_owner import DESIGN_REFERENCE


def context_for(root):
    return application_for(root)._context


@pytest.mark.parametrize("command_request", [RetrievalRebuildSemanticRequest(), RetrievalRepairSemanticRequest(),
    RetrievalEnableRequest(), RetrievalRefreshLexicalRequest(), RuntimeRefreshRouterRequest(), RuntimeWarmupRequest()])
def test_maintenance_drift_rejects_before_entry(command_vault_clone, command_request, monkeypatch):
    request = command_request
    root = command_vault_clone.vault_root
    context = context_for(root)
    admission = MatchingAdmission(prepare_maintenance(context, request))
    target = root / "Projects/Command Fixture.md"
    target.write_text(target.read_text() + "\nChanged after preparation.\n")
    entries = {entry.command_id: entry for entry in current_application_catalogue().entries}
    try:
        result = entries[request.COMMAND_ID].executor(replace(context, admission=admission), request)
        assert result.status == "error"
    except ValueError as exc:
        assert "prepared operation changed" in str(exc)
    assert admission.calls == 0


def test_all_managed_families_declare_preparation():
    commands = {"retrieval.rebuild-semantic", "retrieval.repair-semantic", "retrieval.enable",
                "retrieval.refresh-lexical", "retrieval.evaluate", "retrieval.construct-benchmark",
                "runtime.refresh-router", "runtime.warmup", "shaping.start", "shaping.render", "content.ingest"}
    entries = {entry.command_id: entry for entry in current_application_catalogue().entries}
    assert all(entries[command].preparation.owner_guarded for command in commands)


def test_exact_ingestion_append_rejects_changed_target(command_vault_clone):
    root = command_vault_clone.vault_root
    context = context_for(root)
    request = ContentIngestRequest(InlineContent("Prepared addition."), type_key="ideas", title="Command Fixture Candidate")
    admission = MatchingAdmission(prepare_ingestion(context, request))
    target = root / "Ideas/Command Fixture Candidate.md"
    target.write_text(target.read_text() + "\nConcurrent edit.\n")
    before = target.read_bytes()
    result = ingest(replace(context, admission=admission), request)
    assert result.status == "error"
    assert target.read_bytes() == before
    assert admission.calls == 0


def test_ingestion_successful_choice_observation_admits_once(command_vault_clone):
    context = context_for(command_vault_clone.vault_root)
    request = ContentIngestRequest(InlineContent("Undecided content."), mode=ContentClassifyMode.CONTEXT_ASSEMBLY)
    admission = MatchingAdmission(prepare_ingestion(context, request))
    result = ingest(replace(context, admission=admission), request)
    assert result.status == "ok"
    assert result.result.needs_decision
    assert not result.committed_effects
    assert admission.calls == 1


def test_staged_ingestion_uses_private_body_after_global_source_changes(command_vault_clone):
    root = command_vault_clone.vault_root
    context = context_for(root)
    handle = stage_body(str(root), "Approved body.")["handle"]
    request = ContentIngestRequest(StagedContent(handle), type_key="ideas", title="Command Fixture Candidate")
    class Pins:
        frozen_inputs = None
        def __init__(self): self.content = {}
        def retain_content(self, key, content):
            self.content[key] = content
            return {"sha256": content_digest(content), "bytes": len(content)}
        def read_pinned(self, key): return self.content.get(key)
    pins = Pins()
    binding = prepare_ingestion(replace(context, admission=pins), request)
    admission = MatchingAdmission(binding)
    admission.read_pinned = pins.read_pinned
    Path(_handle_path(str(root), handle)).write_text("Unapproved replacement.")
    result = ingest(replace(context, admission=admission), request)
    assert result.status == "ok"
    body = (root / result.result.path).read_text()
    assert "Approved body." in body and "Unapproved replacement." not in body
    assert admission.calls == 1


def test_render_keeps_prepared_day_and_output_across_midnight(command_vault_clone):
    root = command_vault_clone.vault_root
    context = context_for(root)
    template = root / "_Config/Templates/Temporal/Presentations.md"
    template.parent.mkdir(parents=True, exist_ok=True)
    template.write_text("# PRESENTATION TITLE\n\n[[source-artefact|Source document]]\n")
    request = ShapingRenderRequest("Projects/Command Fixture.md", "approved-deck", PresentationOutput("presentation", False), render=False)
    binding = prepare_render(context, request)
    admission = MatchingAdmission(binding)
    class Later:
        def now(self): return context.clock.now() + timedelta(days=1)
    result = render(replace(context, admission=admission, clock=Later()), request)
    assert result.status == "ok"
    assert result.result.path == binding.review["document"]
    assert admission.calls == 1


def test_render_changed_existing_output_rejects_before_rewrite(command_vault_clone):
    root = command_vault_clone.vault_root
    context = context_for(root)
    template = root / "_Config/Templates/Temporal/Presentations.md"
    template.parent.mkdir(parents=True, exist_ok=True)
    template.write_text("# A template\n")
    request = ShapingRenderRequest("Projects/Command Fixture.md", "reused", PresentationOutput("presentation", False), render=False)
    binding = prepare_render(context, request)
    target = root / binding.review["document"]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("Concurrent output")
    admission = MatchingAdmission(binding)
    result = render(replace(context, admission=admission), request)
    assert result.status == "error"
    assert target.read_text() == "Concurrent output"
    assert admission.calls == 0


def test_shaping_start_keeps_prepared_transcript_day(command_vault_clone):
    root = command_vault_clone.vault_root
    context = context_for(root)
    request = ShapingStartRequest(DESIGN_REFERENCE, ShapingMode.REFINE)
    binding = prepare_session(context, request)
    admission = MatchingAdmission(binding)
    class Later:
        def now(self): return context.clock.now() + timedelta(days=1)
    result = start(replace(context, admission=admission, clock=Later()), request)
    assert result.status == "ok"
    assert result.result.transcript_path == binding.review["transcript"]
    assert admission.calls == 1


def test_benchmark_changed_case_rejects_before_provider(command_vault_clone, monkeypatch):
    import evaluate_search
    root = command_vault_clone.vault_root
    context = context_for(root)
    path = root / ".brain/local/benchmark.json"
    document = {"cases": [{"id": "one", "query": "one", "relevant_paths": ["Ideas/Command Fixture Candidate.md"]}]}
    path.write_text(json.dumps(document))
    request = RetrievalEvaluateRequest(".brain/local/benchmark.json", ("lexical",))
    admission = MatchingAdmission(prepare_benchmark(context, request))
    document["cases"][0]["query"] = "changed"
    path.write_text(json.dumps(document))
    monkeypatch.setattr(evaluate_search, "build_report", lambda *_a, **_kw: pytest.fail("provider entered"))
    result = evaluate(replace(context, admission=admission), request)
    assert result.status == "error"
    assert admission.calls == 0


def test_shared_config_participates_in_maintenance_binding(command_vault_clone):
    root = command_vault_clone.vault_root
    context = context_for(root)
    command = RetrievalRebuildSemanticRequest()
    before = prepare_maintenance(context, command)
    shared = root / ".brain/config.yaml"
    shared.write_text((shared.read_text() if shared.exists() else "") + "\n# Changed shared configuration\n")
    assert prepare_maintenance(context, command).digest != before.digest


def test_ingestion_reloads_definition_after_waiting_for_guard(command_vault_clone, monkeypatch):
    from contextlib import contextmanager
    import _common
    root = command_vault_clone.vault_root
    context = context_for(root)
    request = ContentIngestRequest(InlineContent("Approved append."), type_key="ideas", title="Command Fixture Candidate")
    admission = MatchingAdmission(prepare_ingestion(context, request))
    original = _common.vault_mutation_lock
    @contextmanager
    def intervening(root_, **options):
        with original(root_, **options):
            path = root / ".brain/local/compiled-router.json"
            router = json.loads(path.read_text())
            next(item for item in router["artefacts"] if item["key"] == "ideas")["description"] = "Changed during lock wait"
            path.write_text(json.dumps(router))
            yield
    monkeypatch.setattr(_common, "vault_mutation_lock", intervening)
    target = root / "Ideas/Command Fixture Candidate.md"
    before = target.read_bytes()
    result = ingest(replace(context, admission=admission), request)
    assert result.status == "error"
    assert target.read_bytes() == before
    assert admission.calls == 0


def test_semantic_worker_can_acquire_its_own_vault_guard(command_vault_clone, monkeypatch):
    import subprocess
    import sys
    from _bootstrap import readiness
    root = command_vault_clone.vault_root
    monkeypatch.setattr(readiness, "_spawn_worker", lambda *_args: None)
    status_path = root / ".brain/local/runtime-status.json"
    status_path.unlink(missing_ok=True)
    readiness.ensure_runtime_warmup(root, retry_failed=True)
    run_id = json.loads(status_path.read_text())["run_id"]
    def semantic(root_, _loader, **kwargs):
        child = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[2]); from _common import vault_mutation_lock\nwith vault_mutation_lock(sys.argv[1], timeout=.1): pass", str(root_), str(Path(__file__).resolve().parents[2] / "src/brain-core/scripts")],
                               capture_output=True, text=True, timeout=5)
        assert child.returncode == 0, child.stderr
        return "ready", None
    monkeypatch.setattr(readiness, "_warm_semantic", semantic)
    readiness._run_worker(root, run_id)
    assert readiness.read_runtime_status(root)["state"] == "ready"
