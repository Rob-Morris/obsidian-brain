from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
from typing import Any

from .application import Application, HandlerResult, OperationContext, OperationFailure
from .container_contract import CONTAINER_PYTHON
from .docker import DockerError
from .fixture_publication import create_staging_directory, publish_directory_exclusive
from .fixture_renderers import render_clients
from .model import EffectCertainty, EvidenceCompleteness, Outcome, require_keys
from .process import ProcessExecution
from .run_state import (
    RunManifestCaptureError,
    capture_run_manifest,
    filesystem_diff,
    load_run_manifest_helper,
)


FIXTURE_SCHEMA = "brain-lab.host-fixture/1"
PROBE_SCHEMA = "brain-lab.host-fixture-probe/1"
_SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_ALLOWED_MCP_ENVIRONMENT = {"BRAIN_VAULT_ROOT", "BRAIN_WORKSPACE_DIR", "PYTHONPATH"}
_MAX_SKILL_FILES = 512
_MAX_SKILL_BYTES = 32 * 1024 * 1024
_MAX_SKILLS = 32


class ActiveBrainProbeError(RuntimeError):
    def __init__(self, message: str, execution: ProcessExecution | None = None):
        super().__init__(message)
        self.execution = execution


@dataclass(frozen=True)
class VerifiedActiveBrain:
    probe: dict[str, Any]
    evidence_completeness: EvidenceCompleteness


def _execution_outcome(execution: ProcessExecution | None) -> Outcome:
    if execution is not None and execution.timed_out:
        return Outcome.TIMEOUT
    if execution is not None and execution.cancelled:
        return Outcome.CANCELLED
    return Outcome.FAILURE


def _combined_failure_outcome(*executions: ProcessExecution | None) -> Outcome:
    for execution in executions:
        outcome = _execution_outcome(execution)
        if outcome is not Outcome.FAILURE:
            return outcome
    return Outcome.FAILURE


def _execution_evidence_complete(execution: ProcessExecution | None) -> bool:
    return execution is None or execution.evidence_complete


def _evidence_completeness(complete: bool) -> EvidenceCompleteness:
    return EvidenceCompleteness.COMPLETE if complete else EvidenceCompleteness.PARTIAL


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_text(path: Path, content: str, *, executable: bool = False) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if executable:
        path.chmod(0o755)
    return _sha256_bytes(path.read_bytes())


def _write_json(path: Path, value: Any) -> str:
    content = json.dumps(value, indent=2, sort_keys=True) + "\n"
    return _write_text(path, content)


def _validated_output(value: Any) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("fixture output must be a non-empty path string")
    requested = Path(value).expanduser()
    if requested.name in {"", ".", ".."}:
        raise ValueError("fixture output must name a new directory")
    if os.path.lexists(requested):
        raise ValueError(f"fixture output already exists: {requested}")
    parent = requested.parent.resolve()
    if not parent.is_dir():
        raise ValueError(f"fixture output parent does not exist: {parent}")
    output = parent / requested.name
    if os.path.lexists(output):
        raise ValueError(f"fixture output already exists: {output}")
    return output


def _validated_skills(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("fixture skills must be a non-empty array")
    if len(value) > _MAX_SKILLS:
        raise ValueError(f"fixture skills must contain at most {_MAX_SKILLS} entries")
    if not all(isinstance(item, str) and _SKILL_NAME.fullmatch(item) for item in value):
        raise ValueError("fixture skill names must use lowercase letters, digits, and single hyphens")
    if len(set(value)) != len(value):
        raise ValueError("fixture skill names must be unique")
    return tuple(sorted(value))


def _probe_active_brain(
    context: OperationContext,
    *,
    container_id: str,
    skills: tuple[str, ...],
    probe_source: bytes,
) -> tuple[dict[str, Any], ProcessExecution]:
    argv = [CONTAINER_PYTHON, "-", "--vault", "/home/brain/vault"]
    for skill in skills:
        argv.extend(["--skill", skill])
    try:
        execution = context.docker.exec(
            container_id,
            argv,
            working_directory="/home/brain/vault",
            environment={"PYTHONDONTWRITEBYTECODE": "1"},
            stdin=probe_source,
            evidence_directory=context.evidence_directory / "01-active-brain-probe",
            timeout_seconds=120,
        )
    except DockerError as exc:
        raise ActiveBrainProbeError(str(exc), exc.execution) from exc
    if not execution.succeeded:
        raise ActiveBrainProbeError(
            "selected run does not expose the expected active Brain fixture surface",
            execution,
        )
    if execution.stdout.truncated:
        raise ActiveBrainProbeError(
            "active Brain fixture identity exceeded the evidence retention bound",
            execution,
        )
    try:
        value = json.loads(Path(execution.stdout.path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ActiveBrainProbeError(
            "active Brain fixture probe returned invalid JSON", execution
        ) from exc
    if not isinstance(value, dict) or value.get("schema") != PROBE_SCHEMA:
        raise ActiveBrainProbeError(
            "active Brain fixture probe returned an unsupported result", execution
        )
    return value, execution


def _validate_digest(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"active Brain fixture probe returned an invalid {label}")
    return value


def _validated_core(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("active Brain fixture probe omitted Core identity")
    if not isinstance(value.get("version"), str) or not value["version"]:
        raise ValueError("active Brain fixture probe returned an invalid Core version")
    digest = _validate_digest(value.get("tree_sha256"), label="Core digest")
    if any(
        not isinstance(value.get(field), int) or value[field] < 0
        for field in ("file_count", "total_bytes")
    ):
        raise ValueError("active Brain fixture probe returned invalid Core size metadata")
    return {
        "version": value["version"],
        "tree_sha256": digest,
        "file_count": value["file_count"],
        "total_bytes": value["total_bytes"],
    }


def _validated_mcp(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("active Brain fixture probe omitted MCP identity")
    if value.get("vault_path") != "/home/brain/vault":
        raise ValueError("active Brain fixture probe selected an unexpected vault path")
    command = value.get("command")
    arguments = value.get("args")
    environment = value.get("environment")
    if (
        not isinstance(command, str)
        or not isinstance(arguments, list)
        or not all(isinstance(item, str) for item in arguments)
        or not isinstance(environment, dict)
        or not all(isinstance(key, str) and isinstance(item, str) for key, item in environment.items())
    ):
        raise ValueError("active Brain fixture probe returned an invalid MCP entry")
    unexpected_environment = sorted(set(environment) - _ALLOWED_MCP_ENVIRONMENT)
    if unexpected_environment:
        raise ValueError(
            "active Brain fixture probe returned MCP environment fields that cannot be exported: "
            + ", ".join(unexpected_environment)
        )
    expected_arguments = ["-m", "brain_mcp.proxy", command, "brain_mcp.server"]
    if arguments != expected_arguments:
        raise ValueError("active Brain fixture probe returned an unexpected MCP entry point")
    command_path = PurePosixPath(command)
    if (
        not command_path.is_absolute()
        or ".." in command_path.parts
        or command_path.parts[:3] != ("/", "home", "brain")
    ):
        raise ValueError("active Brain fixture probe returned an unsafe MCP command path")
    if environment.get("PYTHONPATH") != "/home/brain/vault/.brain-core":
        raise ValueError("active Brain fixture probe returned an unexpected MCP PYTHONPATH")
    for key in ("BRAIN_VAULT_ROOT", "BRAIN_WORKSPACE_DIR"):
        if key in environment and environment[key] != "/home/brain/vault":
            raise ValueError(f"active Brain fixture probe returned an unexpected MCP {key}")
    sources = value.get("sources")
    allowed_sources = {".mcp.json", ".codex/config.toml"}
    if (
        not isinstance(sources, list)
        or not sources
        or not all(isinstance(source, str) and source in allowed_sources for source in sources)
        or len(set(sources)) != len(sources)
    ):
        raise ValueError("active Brain fixture probe returned unexpected MCP config sources")
    return {
        "sources": sorted(sources),
        "vault_path": value["vault_path"],
        "command": command,
        "args": list(arguments),
        "environment": dict(sorted(environment.items())),
    }


def _validated_package_file(value: Any, *, skill: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"active Brain fixture probe returned an invalid file for {skill!r}")
    path = value.get("path")
    portable_path = PurePosixPath(path) if isinstance(path, str) else None
    if (
        portable_path is None
        or portable_path.is_absolute()
        or path in {"", ".", ".."}
        or ".." in portable_path.parts
    ):
        raise ValueError(f"active Brain fixture probe returned an unsafe file for {skill!r}")
    digest = _validate_digest(value.get("sha256"), label=f"{skill} file digest")
    size = value.get("size")
    executable = value.get("executable")
    if not isinstance(size, int) or size < 0 or not isinstance(executable, bool):
        raise ValueError(f"active Brain fixture probe returned invalid file metadata for {skill!r}")
    return {"path": path, "sha256": digest, "size": size, "executable": executable}


def _validated_skill(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not isinstance(value.get("name"), str):
        raise ValueError("active Brain fixture probe returned an invalid skill identity")
    name = value["name"]
    adapter = value.get("adapter")
    if not isinstance(adapter, str) or not adapter:
        raise ValueError(f"active Brain fixture probe returned an invalid loader for {name!r}")
    adapter_digest = _validate_digest(value.get("adapter_sha256"), label=f"{name} loader digest")
    if _sha256_bytes(adapter.encode("utf-8")) != adapter_digest:
        raise ValueError(f"active Brain fixture probe returned inconsistent loader content for {name!r}")
    package_digest = _validate_digest(
        value.get("package_tree_sha256"), label=f"{name} package digest"
    )
    source = value.get("source")
    if source not in {"user", "core"}:
        raise ValueError(f"active Brain fixture probe returned an invalid source for {name!r}")
    package_path = value.get("package_path")
    expected_package_path = (
        f"_Config/Skills/{name}" if source == "user" else f".brain-core/skills/{name}"
    )
    if package_path != expected_package_path:
        raise ValueError(f"active Brain fixture probe returned an invalid path for {name!r}")
    raw_files = value.get("package_files")
    if not isinstance(raw_files, list) or not raw_files or len(raw_files) > _MAX_SKILL_FILES:
        raise ValueError(f"active Brain fixture probe returned invalid files for {name!r}")
    files = [_validated_package_file(item, skill=name) for item in raw_files]
    paths = [item["path"] for item in files]
    if len(set(paths)) != len(paths):
        raise ValueError(f"active Brain fixture probe returned duplicate files for {name!r}")
    if sum(item["size"] for item in files) > _MAX_SKILL_BYTES or "SKILL.md" not in paths:
        raise ValueError(f"active Brain fixture probe returned an invalid package for {name!r}")
    return {
        "name": name,
        "source": source,
        "package_path": package_path,
        "package_tree_sha256": package_digest,
        "package_files": files,
        "adapter": adapter,
        "adapter_sha256": adapter_digest,
    }


def _validated_probe(value: dict[str, Any], skills: tuple[str, ...]) -> dict[str, Any]:
    if value.get("vault_path") != "/home/brain/vault":
        raise ValueError("active Brain fixture probe returned an unexpected top-level vault path")
    rows = value.get("skills")
    if not isinstance(rows, list):
        raise ValueError("active Brain fixture probe omitted skill identities")
    by_name = {}
    for raw_row in rows:
        row = _validated_skill(raw_row)
        name = row["name"]
        if name in by_name:
            raise ValueError(f"active Brain fixture probe returned duplicate skill {name!r}")
        by_name[name] = row
    if tuple(sorted(by_name)) != skills:
        raise ValueError("active Brain fixture probe did not return the exact requested skills")
    return {
        "schema": PROBE_SCHEMA,
        "vault_path": value["vault_path"],
        "core": _validated_core(value.get("core")),
        "mcp": _validated_mcp(value.get("mcp")),
        "skills": [by_name[name] for name in skills],
    }


def _bridge_descriptor(
    run_id: str,
    container_id: str,
    mcp: dict[str, Any],
    docker_executable: str,
) -> dict[str, Any]:
    if not docker_executable or docker_executable in {".", ".."}:
        raise ValueError("fixture creation requires a Docker executable")
    if "/" in docker_executable:
        executable_path = Path(docker_executable).expanduser().resolve(strict=True)
        if not executable_path.is_file() or not os.access(executable_path, os.X_OK):
            raise ValueError("fixture creation requires an executable --docker path")
        docker_executable = str(executable_path)
    elif "\\" in docker_executable:
        raise ValueError("fixture creation requires a portable Docker executable name or path")
    return {
        "schema": "brain-lab.host-fixture-bridge/1",
        "run_id": run_id,
        "container_id": container_id,
        "docker_executable": docker_executable,
        "working_directory": mcp["vault_path"],
        "environment": mcp["environment"],
        "command": mcp["command"],
        "args": mcp["args"],
    }


def _materialise_fixture(
    root: Path,
    *,
    output: Path,
    run_id: str,
    receipt: dict[str, Any],
    inspect: dict[str, Any],
    probe: dict[str, Any],
    docker_executable: str,
    bridge_source: str,
) -> dict[str, Any]:
    shared = root / "shared"
    bridge = shared / "brain-mcp-bridge"
    bridge_sha256 = _write_text(
        bridge,
        bridge_source,
        executable=True,
    )
    descriptor = _bridge_descriptor(
        run_id,
        inspect["Id"],
        probe["mcp"],
        docker_executable,
    )
    descriptor_sha256 = _write_json(bridge.with_suffix(".json"), descriptor)

    skill_rows = []
    for skill in probe["skills"]:
        relative = Path("shared") / "skills" / skill["name"] / "SKILL.md"
        loader_sha256 = _write_text(root / relative, skill["adapter"])
        if loader_sha256 != skill["adapter_sha256"]:
            raise RuntimeError(f"fixture loader changed while writing: {skill['name']}")
        skill_rows.append(
            {
                "name": skill["name"],
                "source": skill["source"],
                "package_path": skill["package_path"],
                "package_tree_sha256": skill["package_tree_sha256"],
                "package_files": skill["package_files"],
                "loader_path": relative.as_posix(),
                "loader_sha256": loader_sha256,
            }
        )

    clients = render_clients(root, output)
    container_name = inspect.get("Name")
    if isinstance(container_name, str):
        container_name = container_name.removeprefix("/")
    manifest = {
        "schema": FIXTURE_SCHEMA,
        "output_root": str(output),
        "run": {
            "id": run_id,
            "generation": receipt.get("generation"),
            "container": {
                "id": inspect["Id"],
                "name": container_name,
                "image_id": inspect.get("Image"),
            },
        },
        "brain": {
            "vault_path": probe["mcp"]["vault_path"],
            "core": probe["core"],
            "mcp_config_sources": probe["mcp"]["sources"],
        },
        "bridge": {
            "path": "shared/brain-mcp-bridge",
            "descriptor": "shared/brain-mcp-bridge.json",
            "sha256": bridge_sha256,
            "descriptor_sha256": descriptor_sha256,
        },
        "skills": skill_rows,
        "clients": clients,
    }
    _write_json(root / "manifest.json", manifest)
    return manifest


def _verify_active_brain(
    context: OperationContext,
    *,
    resource: dict[str, str],
    container_id: str,
    skills: tuple[str, ...],
    probe_source: bytes,
) -> VerifiedActiveBrain:
    try:
        manifest_helper = load_run_manifest_helper(context)
        before_capture = capture_run_manifest(
            context,
            container_id,
            "01-before-run-manifest",
            manifest_helper,
        )
    except RunManifestCaptureError as exc:
        raise OperationFailure(
            "fixture creation could not establish the run's pre-probe identity",
            outcome=_execution_outcome(exc.execution),
            effect_certainty=EffectCertainty.NONE,
            evidence_completeness=_evidence_completeness(exc.evidence_complete),
            resource=resource,
            error_type=type(exc).__name__,
        ) from exc

    probe_error: ActiveBrainProbeError | None = None
    probe_execution: ProcessExecution | None = None
    raw_probe: dict[str, Any] | None = None
    try:
        raw_probe, probe_execution = _probe_active_brain(
            context,
            container_id=container_id,
            skills=skills,
            probe_source=probe_source,
        )
    except ActiveBrainProbeError as exc:
        probe_error = exc
    try:
        after_capture = capture_run_manifest(
            context,
            container_id,
            "03-after-run-manifest",
            manifest_helper,
        )
    except RunManifestCaptureError as exc:
        probe_failure_execution = probe_error.execution if probe_error is not None else None
        raise OperationFailure(
            "fixture creation could not prove whether active-Brain inspection changed the run",
            outcome=_combined_failure_outcome(probe_failure_execution, exc.execution),
            effect_certainty=EffectCertainty.UNKNOWN,
            evidence_completeness=_evidence_completeness(
                before_capture.evidence_complete
                and _execution_evidence_complete(
                    probe_error.execution if probe_error is not None else probe_execution
                )
                and exc.evidence_complete
            ),
            resource=resource,
            payload={"before_tree_sha256": before_capture.manifest.get("tree_sha256")},
            error_type=type(exc).__name__,
        ) from exc

    inspection_complete = (
        before_capture.evidence_complete
        and _execution_evidence_complete(
            probe_error.execution if probe_error is not None else probe_execution
        )
        and after_capture.evidence_complete
    )
    evidence_completeness = _evidence_completeness(inspection_complete)
    run_diff = filesystem_diff(before_capture.manifest, after_capture.manifest)
    run_diff["manifest_helper_sha256"] = manifest_helper.sha256
    try:
        (context.evidence_directory / "run-manifest-diff.json").write_text(
            json.dumps(run_diff, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise OperationFailure(
            "fixture creation could not retain run-manifest diff evidence",
            outcome=(
                _execution_outcome(probe_error.execution)
                if probe_error is not None
                else Outcome.FAILURE
            ),
            effect_certainty=(
                EffectCertainty.NONE if run_diff["equal"] else EffectCertainty.PARTIAL
            ),
            evidence_completeness=EvidenceCompleteness.PARTIAL,
            resource=resource,
            payload={"run_manifest_diff": run_diff, "run_modified": not run_diff["equal"]},
            error_type=type(exc).__name__,
        ) from exc
    if not run_diff["equal"]:
        raise OperationFailure(
            "active-Brain inspection changed the retained run; no fixture was published",
            outcome=(
                _execution_outcome(probe_error.execution)
                if probe_error is not None
                else Outcome.FAILURE
            ),
            effect_certainty=EffectCertainty.PARTIAL,
            evidence_completeness=evidence_completeness,
            resource=resource,
            payload={"run_modified": True, "run_manifest_diff": run_diff},
            error_type="RunModified",
        )
    if probe_error is not None:
        raise OperationFailure(
            str(probe_error),
            outcome=_execution_outcome(probe_error.execution),
            effect_certainty=EffectCertainty.NONE,
            evidence_completeness=evidence_completeness,
            resource=resource,
            payload={"run_modified": False},
            error_type=type(probe_error).__name__,
        ) from probe_error
    assert raw_probe is not None
    try:
        probe = _validated_probe(raw_probe, skills)
    except (KeyError, TypeError, ValueError) as exc:
        raise OperationFailure(
            str(exc),
            effect_certainty=EffectCertainty.NONE,
            evidence_completeness=evidence_completeness,
            resource=resource,
            payload={"run_modified": False},
            error_type=type(exc).__name__,
        ) from exc
    return VerifiedActiveBrain(probe, evidence_completeness)


def _publish_fixture(
    context: OperationContext,
    *,
    output: Path,
    resource: dict[str, str],
    run_id: str,
    receipt: dict[str, Any],
    inspect: dict[str, Any],
    verified: VerifiedActiveBrain,
    bridge_source: str,
) -> dict[str, Any]:
    try:
        staging = create_staging_directory(output)
    except OSError as exc:
        raise OperationFailure(
            str(exc),
            effect_certainty=EffectCertainty.NONE,
            evidence_completeness=verified.evidence_completeness,
            resource=resource,
            error_type=type(exc).__name__,
        ) from exc
    try:
        manifest = _materialise_fixture(
            staging,
            output=output,
            run_id=run_id,
            receipt=receipt,
            inspect=inspect,
            probe=verified.probe,
            docker_executable=context.docker.executable,
            bridge_source=bridge_source,
        )
    except Exception as exc:
        raise OperationFailure(
            "fixture materialisation failed; staged partial fixture was retained",
            effect_certainty=EffectCertainty.PARTIAL,
            evidence_completeness=verified.evidence_completeness,
            resource=resource,
            payload={
                "requested_output": str(output),
                "partial_fixture": str(staging),
                "cleanup_attempted": False,
                "materialisation_error": str(exc),
            },
            error_type=type(exc).__name__,
        ) from exc
    try:
        publish_directory_exclusive(staging, output)
    except OSError as exc:
        raise OperationFailure(
            "fixture publication could not claim the requested output; staged fixture was retained",
            effect_certainty=EffectCertainty.PARTIAL,
            evidence_completeness=verified.evidence_completeness,
            resource=resource,
            payload={
                "requested_output": str(output),
                "partial_fixture": str(staging),
                "cleanup_attempted": False,
                "publication_error": str(exc),
            },
            error_type=type(exc).__name__,
        ) from exc
    return manifest


def create_fixture(context: OperationContext, request: dict[str, Any]) -> HandlerResult:
    run_id = request.get("run_id")
    try:
        require_keys(
            request,
            required=frozenset({"run_id", "skills", "output"}),
        )
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("fixture run_id must be a non-empty string")
        output = _validated_output(request["output"])
        skills = _validated_skills(request["skills"])
        receipt = context.store.read("run", run_id)
        inspect = context.docker.container_inspect(
            receipt["container"]["id"], context.evidence_directory / "00-inspect"
        )
        context.docker.verify_resource_labels(inspect, "run", run_id)
        if not bool(inspect.get("State", {}).get("Running")):
            raise ValueError(f"fixture run is not running: {run_id}")
        probe_source = (context.tool_root / "host_fixture" / "active_brain_probe.py").read_bytes()
        bridge_source = (context.tool_root / "host_fixture" / "brain_mcp_bridge.py").read_text(
            encoding="utf-8"
        )
    except (DockerError, KeyError, OSError, TypeError, ValueError) as exc:
        raise OperationFailure(
            str(exc),
            effect_certainty=EffectCertainty.NONE,
            resource=(
                {"kind": "run", "id": run_id}
                if isinstance(run_id, str) and run_id
                else None
            ),
            error_type=type(exc).__name__,
        ) from exc

    resource = {"kind": "run", "id": run_id, "docker_id": inspect["Id"]}
    verified = _verify_active_brain(
        context,
        resource=resource,
        container_id=inspect["Id"],
        skills=skills,
        probe_source=probe_source,
    )
    manifest = _publish_fixture(
        context,
        output=output,
        resource=resource,
        run_id=run_id,
        receipt=receipt,
        inspect=inspect,
        verified=verified,
        bridge_source=bridge_source,
    )

    return HandlerResult(
        resource=resource,
        payload={
            "output": str(output),
            "manifest": manifest,
            "run_retained": True,
            "run_modified": False,
        },
        effect_certainty=EffectCertainty.COMMITTED,
        evidence_completeness=verified.evidence_completeness,
    )


def register_fixture_handlers(application: Application) -> None:
    application.register("fixture.create", create_fixture, mutating=True)
