from __future__ import annotations

import copy
from pathlib import Path
import json
from types import SimpleNamespace

import pytest

from brain_lab.application import Application, HandlerResult, OperationFailure
from brain_lab.compatibility import CompatibilityManifest
from brain_lab.baselines import register_baseline_handlers
from brain_lab.docker import DockerClient, DockerError
from brain_lab.model import EffectCertainty
from brain_lab.operations import register_operational_handlers
from brain_lab.process import CommandRunner
from brain_lab.resources import _image_payload, _rollback_changed_capture
from brain_lab.runs import register_run_handlers
from brain_lab.scenarios import register_scenario_handlers
from brain_lab.store import StateStore


TOOL_ROOT = Path(__file__).resolve().parents[3] / "tools" / "brain-lab"


def test_image_receipt_records_platform_architecture_and_variant():
    payload = _image_payload(
        {"Id": "sha256:image", "Architecture": "arm64", "Variant": "v8", "Os": "linux"},
        "brain-lab-base:base-a",
        "linux/arm64/v8",
        {},
    )

    assert payload["platform"] == "linux/arm64/v8"
    assert payload["architecture"] == "arm64"
    assert payload["variant"] == "v8"


def test_changed_capture_reports_a_surviving_image_when_rollback_fails(tmp_path: Path):
    class FailingDocker:
        def remove_image(self, image, evidence_directory):
            raise DockerError("simulated cleanup failure")

    context = SimpleNamespace(docker=FailingDocker(), evidence_directory=tmp_path)
    with pytest.raises(OperationFailure) as caught:
        _rollback_changed_capture(
            context,
            resource_kind="source",
            resource_id="source-a",
            tag="brain-lab-source:source-a",
            reason="source changed during capture",
        )

    assert caught.value.effect_certainty.value == "partial"
    assert caught.value.payload["surviving_image_tag"] == "brain-lab-source:source-a"


def _application(tmp_path: Path) -> Application:
    runner = CommandRunner()
    application = Application(
        store=StateStore(tmp_path / "state"),
        runner=runner,
        docker=DockerClient(runner),
        tool_root=TOOL_ROOT,
    )
    return application


class RecreateDocker:
    executions = []

    def __init__(self, *, fail_start=False, create_before_start_failure=False):
        self.fail_start = fail_start
        self.create_before_start_failure = create_before_start_failure
        self.calls = []
        self._limit = 32
        self.containers = {
            "old-container": {
                "Id": "old-container",
                "Name": "/brain-lab-run-run-a",
                "State": {"Running": True},
                "Config": {"Labels": DockerClient.labels("run", "run-a")},
            }
        }

    def begin_operation(self):
        self._limit = 32
        return 0

    @property
    def operation_command_limit(self):
        return self._limit

    def container_inspect(self, reference, evidence_directory):
        self.calls.append(("inspect", reference))
        for container in self.containers.values():
            if reference in {container["Id"], container["Name"].removeprefix("/")}:
                return copy.deepcopy(container)
        raise DockerError(f"unknown container {reference}")

    def verify_resource_labels(self, inspect, kind, resource_id):
        DockerClient.verify_resource_labels(inspect, kind, resource_id)

    def stop(self, container, evidence_directory):
        self.calls.append(("stop", container))
        self.containers[container]["State"]["Running"] = False

    def rename_container(self, container, name, evidence_directory):
        self.calls.append(("rename", container, name))
        self.containers[container]["Name"] = f"/{name}"

    def start_container(self, image, **kwargs):
        self.calls.append(("start-container", image, kwargs["name"]))
        if not self.fail_start or self.create_before_start_failure:
            self.containers["new-container"] = {
                "Id": "new-container",
                "Name": f"/{kwargs['name']}",
                "State": {"Running": True},
                "Config": {"Labels": kwargs["labels"]},
            }
        if self.fail_start:
            raise DockerError("simulated replacement start failure")
        return "new-container", None

    def start_existing(self, container, evidence_directory):
        self.calls.append(("start-existing", container))
        self.containers[container]["State"]["Running"] = True

    def remove_container(self, container, evidence_directory):
        self.calls.append(("remove", container))
        del self.containers[container]


class OrphanDocker:
    executions = []

    def __init__(self, *, imported=False):
        self.imported = imported
        self.removed = []

    def begin_operation(self):
        return 0

    def _labels(self):
        return DockerClient.labels("attempt", "attempt-orphan", imported=self.imported)

    def list_managed(self, evidence_directory):
        labels = ",".join(f"{key}={value}" for key, value in self._labels().items())
        return {
            "containers": [{"ID": "orphan-container", "Labels": labels, "Size": "1kB"}],
            "images": [],
        }

    def container_inspect(self, reference, evidence_directory):
        return {"Id": reference, "Config": {"Labels": self._labels()}}

    def image_inspect(self, reference, evidence_directory):
        return {"Id": reference, "Config": {"Labels": self._labels()}}

    def verify_resource_labels(self, inspect, kind, resource_id):
        DockerClient.verify_resource_labels(inspect, kind, resource_id)

    def remove_container(self, reference, evidence_directory):
        self.removed.append(("container", reference))

    def remove_image(self, reference, evidence_directory):
        self.removed.append(("image", reference))


class CopyInDocker:
    executions = []

    def __init__(self):
        self.calls = []

    def begin_operation(self):
        return 0

    @property
    def operation_command_limit(self):
        return 32

    def container_inspect(self, reference, evidence_directory):
        return {
            "Id": "run-container",
            "Config": {"Labels": DockerClient.labels("run", "run-copy")},
        }

    def verify_resource_labels(self, inspect, kind, resource_id):
        DockerClient.verify_resource_labels(inspect, kind, resource_id)

    def exec(self, container, argv, **kwargs):
        self.calls.append(("exec", list(argv), kwargs.get("user")))
        return SimpleNamespace(
            succeeded=True,
            evidence_complete=True,
            to_dict=lambda: {"returncode": 0},
        )

    def copy_in(self, container, source, destination, evidence_directory):
        self.calls.append(("copy-in", Path(source), destination))
        return SimpleNamespace(to_dict=lambda: {"returncode": 0})


def _recreate_application(tmp_path: Path, docker: RecreateDocker) -> Application:
    application = _application(tmp_path)
    application.docker = docker
    register_run_handlers(application)
    application.store.write(
        "run",
        "run-a",
        {
            "source": {"kind": "baseline", "id": "baseline-a"},
            "spec": {
                "image": "sha256:baseline",
                "platform": "linux/arm64",
                "network": "none",
            },
            "container": {
                "id": "old-container",
                "name": "brain-lab-run-run-a",
                "labels": DockerClient.labels("run", "run-a"),
            },
            "generation": 1,
            "imported": False,
            "status": "running",
        },
    )
    return application


def test_host_copy_in_normalises_only_a_new_destination_to_the_brain_user(tmp_path: Path):
    application = _application(tmp_path)
    docker = CopyInDocker()
    application.docker = docker
    register_run_handlers(application)
    application.store.write(
        "run",
        "run-copy",
        {
            "spec": {"platform": "linux/arm64"},
            "container": {"id": "run-container"},
        },
    )
    source = tmp_path / "agent-skills"
    source.mkdir()

    result = application.dispatch(
        "run.copy-in",
        {
            "id": "run-copy",
            "source": str(source),
            "destination": "/home/brain/agent-skills",
        },
    )

    assert result.ok
    assert ("copy-in", source, "/home/brain/agent-skills") in docker.calls
    assert (
        "exec",
        ["chown", "-hR", "10001:10001", "/home/brain/agent-skills"],
        "root",
    ) in docker.calls


@pytest.mark.parametrize(
    "destination",
    ["/home/brain", "/tmp/agent-skills", "relative/agent-skills", "/home/brain/../tmp"],
)
def test_copy_in_rejects_destinations_outside_a_new_brain_child(
    tmp_path: Path, destination: str
):
    application = _application(tmp_path)
    docker = CopyInDocker()
    application.docker = docker
    register_run_handlers(application)
    application.store.write(
        "run",
        "run-copy",
        {
            "spec": {"platform": "linux/arm64"},
            "container": {"id": "run-container"},
        },
    )
    source = tmp_path / "agent-skills"
    source.mkdir()

    result = application.dispatch(
        "run.copy-in",
        {"id": "run-copy", "source": str(source), "destination": destination},
    )

    assert not result.ok
    assert not any(call[0] == "copy-in" for call in docker.calls)


def test_run_recreate_publishes_replacement_before_removing_previous(tmp_path: Path):
    docker = RecreateDocker()
    application = _recreate_application(tmp_path, docker)

    result = application.dispatch("run.recreate", {"id": "run-a"})

    assert result.ok
    assert result.payload["generation"] == 2
    assert application.store.read("run", "run-a")["container"]["id"] == "new-container"
    start_index = next(i for i, call in enumerate(docker.calls) if call[0] == "start-container")
    remove_index = docker.calls.index(("remove", "old-container"))
    assert start_index < remove_index


def test_run_recreate_restores_previous_container_when_replacement_fails(tmp_path: Path):
    docker = RecreateDocker(fail_start=True)
    application = _recreate_application(tmp_path, docker)

    result = application.dispatch("run.recreate", {"id": "run-a"})

    assert result.outcome.value == "failure"
    assert result.effect_certainty.value == "none"
    assert application.store.read("run", "run-a")["container"]["id"] == "old-container"
    assert ("start-existing", "old-container") in docker.calls
    assert ("remove", "old-container") not in docker.calls


def test_run_recreate_removes_ambiguously_created_replacement_before_rollback(tmp_path: Path):
    docker = RecreateDocker(fail_start=True, create_before_start_failure=True)
    application = _recreate_application(tmp_path, docker)

    result = application.dispatch("run.recreate", {"id": "run-a"})

    assert result.outcome.value == "failure"
    assert result.effect_certainty.value == "none"
    assert "new-container" not in docker.containers
    assert docker.containers["old-container"]["Name"] == "/brain-lab-run-run-a"
    assert docker.containers["old-container"]["State"]["Running"] is True


def test_scenario_uses_the_same_registered_primitive_dispatcher(tmp_path: Path):
    application = _application(tmp_path)
    calls = []

    def echo(context, request):
        calls.append(request)
        return HandlerResult(payload={"echo": request}, effect_certainty=EffectCertainty.NONE)

    application.register("test.echo", echo)
    register_run_handlers(application)
    register_scenario_handlers(application)

    direct = application.dispatch("test.echo", {"value": 1})
    scenario = application.dispatch(
        "scenario.run",
        {"steps": [{"operation": "test.echo", "request": {"value": 2}}]},
    )

    assert direct.ok and scenario.ok
    assert calls == [{"value": 1}, {"value": 2}]
    nested = scenario.payload["steps"][0]
    assert nested["schema"] == direct.to_dict()["schema"]


def test_handler_validation_failure_is_an_honest_no_effect_result(tmp_path: Path):
    application = _application(tmp_path)

    def reject(context, request):
        raise ValueError("bad request")

    application.register("test.reject", reject)
    result = application.dispatch("test.reject", {})

    assert result.outcome.value == "failure"
    assert result.effect_certainty.value == "none"
    assert result.errors[0]["message"] == "bad request"


def test_mutating_operation_validation_failure_is_conservatively_unknown(tmp_path: Path):
    application = _application(tmp_path)

    def fail_after_possible_effect(context, request):
        raise ValueError("post-effect validation failed")

    application.register("test.mutate", fail_after_possible_effect, mutating=True)
    result = application.dispatch("test.mutate", {})

    assert result.outcome.value == "failure"
    assert result.effect_certainty.value == "unknown"


def test_interactive_shell_nonzero_exit_is_unknown_effect_failure(tmp_path: Path):
    application = _application(tmp_path)

    class ShellDocker:
        executions = []

        def begin_operation(self):
            return 0

        def container_inspect(self, container, evidence_directory):
            return {"Id": container}

        def verify_resource_labels(self, inspect, kind, resource_id):
            return None

        def shell(self, container, *, shell):
            return 7

    application.docker = ShellDocker()
    application.store.write("run", "run-a", {"container": {"id": "container-a"}})
    register_run_handlers(application)

    result = application.dispatch("run.shell", {"id": "run-a"})

    assert result.outcome.value == "failure"
    assert result.effect_certainty.value == "unknown"
    assert "status 7" in result.errors[0]["message"]


def test_redacted_result_export_omits_raw_evidence_and_host_paths(tmp_path: Path):
    application = _application(tmp_path)
    register_operational_handlers(application)
    application.store.write(
        "result",
        "op-source",
        {"payload": {"host_path": "/Users/example/private-vault"}},
    )
    evidence = application.store.evidence / "op-source"
    evidence.mkdir()
    (evidence / "bundle.json").write_text(
        json.dumps({"schema": "brain-lab.evidence-bundle/1"}), encoding="utf-8"
    )
    (evidence / "raw.log").write_text("private vault content", encoding="utf-8")
    destination = tmp_path / "export"

    result = application.dispatch(
        "results.export",
        {"id": "op-source", "destination": str(destination), "redacted": True},
    )

    assert result.ok
    exported_receipt = json.loads((destination / "receipt.json").read_text())
    exported_bundle = json.loads((destination / "bundle.json").read_text())
    assert exported_receipt["payload"]["host_path"] == "<redacted-host-path>"
    assert exported_bundle["raw_files_exported"] is False
    assert not (destination / "raw.log").exists()


def test_evidence_failure_cannot_replace_primary_result(tmp_path: Path):
    application = _application(tmp_path)

    def succeed_with_broken_evidence_target(context, request):
        (context.evidence_directory / "commands.json").mkdir()
        return HandlerResult(payload={"primary": "preserved"})

    application.register("test.evidence-failure", succeed_with_broken_evidence_target)
    result = application.dispatch("test.evidence-failure", {})

    assert result.ok
    assert result.payload["primary"] == "preserved"
    assert result.evidence_completeness.value == "partial"
    assert result.errors[-1]["type"] == "EvidenceCollectionError"


def test_scenario_can_feed_a_primitive_result_into_a_later_primitive(tmp_path: Path):
    application = _application(tmp_path)

    def produce(context, request):
        return HandlerResult(resource={"kind": "source", "id": "source-123"})

    def consume(context, request):
        return HandlerResult(payload={"received": request["source_id"]})

    application.register("test.produce", produce)
    application.register("test.consume", consume)
    register_run_handlers(application)
    register_scenario_handlers(application)
    result = application.dispatch(
        "scenario.run",
        {
            "steps": [
                {"operation": "test.produce", "request": {}},
                {
                    "operation": "test.consume",
                    "request": {"source_id": "${steps.0.resource.id}"},
                },
            ]
        },
    )

    assert result.ok
    assert result.payload["steps"][1]["payload"]["received"] == "source-123"


def test_failed_scenario_retains_completed_primitive_results(tmp_path: Path):
    application = _application(tmp_path)

    application.register("test.ok", lambda context, request: HandlerResult())

    def fail(context, request):
        raise ValueError("known failure")

    application.register("test.fail", fail)
    register_run_handlers(application)
    register_scenario_handlers(application)
    result = application.dispatch(
        "scenario.run",
        {
            "steps": [
                {"operation": "test.ok", "request": {}},
                {"operation": "test.fail", "request": {}},
                {"operation": "test.ok", "request": {}},
            ]
        },
    )

    assert not result.ok
    assert result.payload["completed"] is False
    assert [step["operation"] for step in result.payload["steps"]] == [
        "test.ok",
        "test.fail",
    ]


def test_scenario_assertions_compare_typed_primitive_results(tmp_path: Path):
    application = _application(tmp_path)

    application.register(
        "test.value",
        lambda context, request: HandlerResult(payload={"value": request["value"]}),
    )
    register_run_handlers(application)
    register_scenario_handlers(application)
    result = application.dispatch(
        "scenario.run",
        {
            "steps": [
                {"operation": "test.value", "request": {"value": "same"}},
                {"operation": "test.value", "request": {"value": "same"}},
            ],
            "assertions": [
                {
                    "left": "${steps.0.payload.value}",
                    "operator": "equal",
                    "right": "${steps.1.payload.value}",
                }
            ],
        },
    )

    assert result.ok
    assert result.payload["assertions"][0]["passed"] is True
    bundle = json.loads(
        (tmp_path / "state" / "evidence" / result.operation_id / "bundle.json").read_text()
    )
    assert bundle["command_count"] == 0
    assert bundle["referenced_operation_ids"] == [
        step["operation_id"] for step in result.payload["steps"]
    ]


@pytest.mark.parametrize(
    ("nested_effect", "expected"),
    [
        (EffectCertainty.PARTIAL, EffectCertainty.PARTIAL),
        (EffectCertainty.UNKNOWN, EffectCertainty.UNKNOWN),
    ],
)
def test_successful_scenario_preserves_nested_effect_truth(
    tmp_path: Path, nested_effect: EffectCertainty, expected: EffectCertainty
):
    application = _application(tmp_path)
    application.register(
        "test.effect",
        lambda context, request: HandlerResult(effect_certainty=nested_effect),
    )
    register_scenario_handlers(application)

    result = application.dispatch(
        "scenario.run",
        {
            "steps": [{"operation": "test.effect", "request": {}}],
            "continue_after_failure": True,
        },
    )

    assert result.outcome.value == "success"
    assert result.effect_certainty is expected


def test_unexpected_handler_error_is_preserved_as_a_result_receipt(tmp_path: Path):
    application = _application(tmp_path)

    def broken(context, request):
        raise KeyError("programming mistake")

    application.register("test.broken", broken, mutating=True)

    result = application.dispatch("test.broken", {})

    assert result.outcome.value == "failure"
    assert result.effect_certainty is EffectCertainty.UNKNOWN
    assert result.errors[0]["type"] == "KeyError"
    assert application.store.read("result", result.operation_id)["outcome"] == "failure"


def test_cleanup_never_selects_imported_seed_derivatives(tmp_path: Path):
    application = _application(tmp_path)
    application.docker = OrphanDocker()
    register_operational_handlers(application)
    old = "2020-01-01T00:00:00+00:00"
    application.store.write(
        "seed",
        "seed-imported",
        {"created_at": old, "spec": {"imported": True}},
    )
    application.store.write(
        "baseline",
        "baseline-imported",
        {
            "created_at": old,
            "imported": True,
            "recipe": {"seed_id": "seed-imported"},
        },
    )
    application.store.write(
        "run",
        "run-imported",
        {
            "created_at": old,
            "imported": True,
            "source": {"kind": "baseline", "id": "baseline-imported"},
        },
    )
    application.store.write(
        "attempt",
        "attempt-imported-legacy",
        {
            "created_at": old,
            "image": {
                "labels": DockerClient.labels(
                    "attempt", "attempt-imported-legacy", imported=True
                )
            },
        },
    )

    result = application.dispatch("cleanup.preview", {"older_than_days": 0})
    retained = {item["id"]: item["reasons"] for item in result.payload["retained"]}

    assert "imported-vault-requires-explicit-deletion" in retained["seed-imported"]
    assert "imported-vault-requires-explicit-deletion" in retained["baseline-imported"]
    assert "imported-vault-requires-explicit-deletion" in retained["run-imported"]
    assert "imported-vault-requires-explicit-deletion" in retained["attempt-imported-legacy"]


def test_cleanup_previews_unrecorded_labelled_docker_resources(tmp_path: Path):
    application = _application(tmp_path)
    application.docker = OrphanDocker(imported=True)
    register_operational_handlers(application)

    result = application.dispatch("cleanup.preview", {"older_than_days": 0})

    assert result.ok
    assert result.payload["docker_orphans"] == [
        {
            "docker_kind": "container",
            "docker_id": "orphan-container",
            "resource_kind": "attempt",
            "resource_id": "attempt-orphan",
            "imported": True,
            "size": "1kB",
            "destroy_request": {
                "docker_kind": "container",
                "docker_id": "orphan-container",
                "confirm_imported": True,
            },
        }
    ]


def test_cleanup_treats_a_different_docker_id_as_orphan_even_when_label_receipt_exists(
    tmp_path: Path,
):
    application = _application(tmp_path)
    application.docker = OrphanDocker()
    register_operational_handlers(application)
    application.store.write(
        "attempt",
        "attempt-orphan",
        {"container": {"id": "recorded-other-container"}},
    )

    result = application.dispatch("cleanup.preview", {"older_than_days": 0})

    assert [item["docker_id"] for item in result.payload["docker_orphans"]] == [
        "orphan-container"
    ]


def test_orphan_destroy_accepts_exact_unrecorded_docker_id_behind_reused_labels(
    tmp_path: Path,
):
    application = _application(tmp_path)
    docker = OrphanDocker()
    application.docker = docker
    register_operational_handlers(application)
    application.store.write(
        "attempt",
        "attempt-orphan",
        {"container": {"id": "recorded-other-container"}},
    )

    result = application.dispatch(
        "orphan.destroy", {"docker_kind": "container", "docker_id": "orphan-container"}
    )

    assert result.ok
    assert docker.removed == [("container", "orphan-container")]


def test_orphan_destroy_refuses_the_exact_docker_id_owned_by_a_receipt(tmp_path: Path):
    application = _application(tmp_path)
    docker = OrphanDocker()
    application.docker = docker
    register_operational_handlers(application)
    application.store.write(
        "attempt",
        "attempt-orphan",
        {"container": {"id": "orphan-container"}},
    )

    result = application.dispatch(
        "orphan.destroy", {"docker_kind": "container", "docker_id": "orphan-container"}
    )

    assert not result.ok
    assert docker.removed == []


def test_orphan_destroy_requires_imported_confirmation_and_exact_labels(tmp_path: Path):
    application = _application(tmp_path)
    docker = OrphanDocker(imported=True)
    application.docker = docker
    register_operational_handlers(application)

    rejected = application.dispatch(
        "orphan.destroy", {"docker_kind": "container", "docker_id": "orphan-container"}
    )
    destroyed = application.dispatch(
        "orphan.destroy",
        {
            "docker_kind": "container",
            "docker_id": "orphan-container",
            "confirm_imported": True,
        },
    )

    assert rejected.outcome.value == "failure"
    assert "confirm_imported=true" in rejected.errors[0]["message"]
    assert destroyed.ok
    assert docker.removed == [("container", "orphan-container")]


class AttemptDocker:
    executions = []

    def __init__(self, *, imported=False, fail_image_remove=False):
        self.imported = imported
        self.fail_image_remove = fail_image_remove
        self.removed = []

    def begin_operation(self):
        return 0

    def _inspect(self, resource_id, docker_id):
        return {
            "Id": docker_id,
            "Config": {
                "Labels": DockerClient.labels(
                    "attempt", resource_id, imported=self.imported
                )
            },
        }

    def container_inspect(self, reference, evidence_directory):
        return self._inspect("attempt-a", reference)

    def image_inspect(self, reference, evidence_directory):
        return self._inspect("attempt-a", reference)

    def verify_resource_labels(self, inspect, kind, resource_id):
        DockerClient.verify_resource_labels(inspect, kind, resource_id)

    def remove_container(self, reference, evidence_directory):
        self.removed.append(("container", reference))

    def remove_image(self, reference, evidence_directory):
        if self.fail_image_remove:
            raise DockerError("simulated image cleanup failure")
        self.removed.append(("image", reference))


def _failed_attempt(application):
    application.store.write(
        "attempt",
        "attempt-a",
        {
            "status": "failed-preparation",
            "container": {"id": "container-a"},
            "retained_container_id": "container-a",
            "image": {"id": "image-a", "tag": "brain-lab-attempt:attempt-a"},
        },
    )


def test_attempt_destroy_removes_exact_labelled_resources_and_receipt(tmp_path: Path):
    application = _application(tmp_path)
    docker = AttemptDocker()
    application.docker = docker
    _failed_attempt(application)
    register_baseline_handlers(application)

    result = application.dispatch("attempt.destroy", {"id": "attempt-a"})

    assert result.ok
    assert docker.removed == [("container", "container-a"), ("image", "image-a")]
    assert not application.store.receipt_path("attempt", "attempt-a").exists()


def test_imported_attempt_destroy_requires_confirmation_from_docker_label(tmp_path: Path):
    application = _application(tmp_path)
    docker = AttemptDocker(imported=True)
    application.docker = docker
    _failed_attempt(application)
    register_baseline_handlers(application)

    rejected = application.dispatch("attempt.destroy", {"id": "attempt-a"})
    accepted = application.dispatch(
        "attempt.destroy", {"id": "attempt-a", "confirm_imported": True}
    )

    assert not rejected.ok
    assert "confirm_imported=true" in rejected.errors[0]["message"]
    assert accepted.ok
    assert docker.removed == [("container", "container-a"), ("image", "image-a")]


def test_attempt_destroy_records_exact_survivor_after_partial_cleanup(tmp_path: Path):
    application = _application(tmp_path)
    docker = AttemptDocker(fail_image_remove=True)
    application.docker = docker
    _failed_attempt(application)
    register_baseline_handlers(application)

    result = application.dispatch("attempt.destroy", {"id": "attempt-a"})

    assert not result.ok
    assert result.effect_certainty.value == "partial"
    assert result.payload["survivors"] == [{"kind": "image", "id": "image-a"}]
    receipt = application.store.read("attempt", "attempt-a")
    assert "container" not in receipt
    assert receipt["cleanup"]["survivors"] == result.payload["survivors"]


def test_baseline_receipt_keeps_its_successful_attempt_referenced(tmp_path: Path):
    application = _application(tmp_path)
    application.store.write("attempt", "attempt-a", {"status": "succeeded"})
    application.store.write(
        "baseline",
        "baseline-a",
        {"attempt_id": "attempt-a", "recipe": {}},
    )

    assert application.store.references_to("attempt", "attempt-a") == [
        {"kind": "baseline", "id": "baseline-a"}
    ]
