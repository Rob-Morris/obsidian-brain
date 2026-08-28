from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
from typing import Any

from .application import Application, HandlerResult, OperationContext, OperationFailure
from .docker import DockerError
from .fixture_renderers import render_clients
from .model import EffectCertainty, EvidenceCompleteness, require_keys
from .run_state import capture_run_manifest, filesystem_diff


FIXTURE_SCHEMA = "brain-lab.host-fixture/1"
PROBE_SCHEMA = "brain-lab.host-fixture-probe/1"
_SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_ALLOWED_MCP_ENVIRONMENT = {"BRAIN_VAULT_ROOT", "BRAIN_WORKSPACE_DIR", "PYTHONPATH"}
_MAX_SKILL_FILES = 512
_MAX_SKILL_BYTES = 32 * 1024 * 1024
_MAX_SKILLS = 32

def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_text(path: Path, content: str, *, executable: bool = False) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if executable:
        path.chmod(0o755)
    return _sha256_bytes(content.encode("utf-8"))


def _write_json(path: Path, value: Any) -> str:
    content = json.dumps(value, indent=2, sort_keys=True) + "\n"
    return _write_text(path, content)


def _directory_identity(path: Path) -> tuple[int, int]:
    metadata = path.lstat()
    if not stat.S_ISDIR(metadata.st_mode):
        raise OSError(f"fixture output is not a directory: {path}")
    return metadata.st_dev, metadata.st_ino


def _remove_owned_output(path: Path, identity: tuple[int, int]) -> str | None:
    try:
        current_identity = _directory_identity(path)
    except OSError as exc:
        return f"output ownership could not be verified; cleanup skipped: {exc}"
    if current_identity != identity:
        return "output ownership changed during publication; cleanup skipped"
    try:
        shutil.rmtree(path)
    except OSError as exc:
        return f"owned output could not be removed: {exc}"
    return None


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
) -> dict[str, Any]:
    probe = (context.tool_root / "host_fixture" / "active_brain_probe.py").read_bytes()
    argv = ["python3.12", "-", "--vault", "/home/brain/vault"]
    for skill in skills:
        argv.extend(["--skill", skill])
    execution = context.docker.exec(
        container_id,
        argv,
        working_directory="/home/brain/vault",
        environment={"PYTHONDONTWRITEBYTECODE": "1"},
        stdin=probe,
        evidence_directory=context.evidence_directory / "01-active-brain-probe",
        timeout_seconds=120,
    )
    if not execution.succeeded:
        raise ValueError("selected run does not expose the expected active Brain fixture surface")
    if execution.stdout.truncated:
        raise ValueError("active Brain fixture identity exceeded the evidence retention bound")
    try:
        value = json.loads(Path(execution.stdout.path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("active Brain fixture probe returned invalid JSON") from exc
    if not isinstance(value, dict) or value.get("schema") != PROBE_SCHEMA:
        raise ValueError("active Brain fixture probe returned an unsupported result")
    return value


def _validate_digest(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"active Brain fixture probe returned an invalid {label}")
    return value


def _validated_probe(value: dict[str, Any], skills: tuple[str, ...]) -> dict[str, Any]:
    core = value.get("core")
    mcp = value.get("mcp")
    rows = value.get("skills")
    if not isinstance(core, dict) or not isinstance(mcp, dict) or not isinstance(rows, list):
        raise ValueError("active Brain fixture probe omitted required identity fields")
    if not isinstance(core.get("version"), str) or not core["version"]:
        raise ValueError("active Brain fixture probe returned an invalid Core version")
    _validate_digest(core.get("tree_sha256"), label="Core digest")
    if any(
        not isinstance(core.get(field), int) or core[field] < 0
        for field in ("file_count", "total_bytes")
    ):
        raise ValueError("active Brain fixture probe returned invalid Core size metadata")
    if mcp.get("vault_path") != "/home/brain/vault":
        raise ValueError("active Brain fixture probe selected an unexpected vault path")
    command = mcp.get("command")
    arguments = mcp.get("args")
    environment = mcp.get("environment")
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
    if mcp.get("source") not in {".mcp.json", ".codex/config.toml"}:
        raise ValueError("active Brain fixture probe returned an unexpected MCP config source")
    by_name = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            raise ValueError("active Brain fixture probe returned an invalid skill identity")
        name = row["name"]
        if name in by_name:
            raise ValueError(f"active Brain fixture probe returned duplicate skill {name!r}")
        adapter = row.get("adapter")
        if not isinstance(adapter, str) or not adapter:
            raise ValueError(f"active Brain fixture probe returned an invalid loader for {name!r}")
        adapter_digest = _validate_digest(row.get("adapter_sha256"), label=f"{name} loader digest")
        if _sha256_bytes(adapter.encode("utf-8")) != adapter_digest:
            raise ValueError(f"active Brain fixture probe returned inconsistent loader content for {name!r}")
        _validate_digest(row.get("package_tree_sha256"), label=f"{name} package digest")
        source = row.get("source")
        if source not in {"user", "core"}:
            raise ValueError(f"active Brain fixture probe returned an invalid source for {name!r}")
        expected_package_path = (
            f"_Config/Skills/{name}" if source == "user" else f".brain-core/skills/{name}"
        )
        if row.get("package_path") != expected_package_path:
            raise ValueError(f"active Brain fixture probe returned an invalid path for {name!r}")
        files = row.get("package_files")
        if not isinstance(files, list) or not files or len(files) > _MAX_SKILL_FILES:
            raise ValueError(f"active Brain fixture probe returned invalid files for {name!r}")
        total_bytes = 0
        seen_paths = set()
        for item in files:
            if not isinstance(item, dict):
                raise ValueError(f"active Brain fixture probe returned an invalid file for {name!r}")
            path = item.get("path")
            portable_path = PurePosixPath(path) if isinstance(path, str) else None
            if (
                portable_path is None
                or portable_path.is_absolute()
                or path in {"", ".", ".."}
                or ".." in portable_path.parts
                or path in seen_paths
            ):
                raise ValueError(f"active Brain fixture probe returned an unsafe file for {name!r}")
            seen_paths.add(path)
            _validate_digest(item.get("sha256"), label=f"{name} file digest")
            size = item.get("size")
            if not isinstance(size, int) or size < 0 or not isinstance(item.get("executable"), bool):
                raise ValueError(f"active Brain fixture probe returned invalid file metadata for {name!r}")
            total_bytes += size
        if total_bytes > _MAX_SKILL_BYTES or "SKILL.md" not in seen_paths:
            raise ValueError(f"active Brain fixture probe returned an invalid package for {name!r}")
        by_name[name] = row
    if tuple(sorted(by_name)) != skills:
        raise ValueError("active Brain fixture probe did not return the exact requested skills")
    value["skills"] = [by_name[name] for name in skills]
    return value


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
    docker_argv = [
        docker_executable,
        "exec",
        "--interactive",
        "--workdir",
        mcp["vault_path"],
    ]
    for key, value in sorted(mcp["environment"].items()):
        docker_argv.extend(["--env", f"{key}={value}"])
    docker_argv.extend([container_id, mcp["command"], *mcp["args"]])
    return {
        "schema": "brain-lab.host-fixture-bridge/1",
        "run_id": run_id,
        "container_id": container_id,
        "docker_executable": docker_executable,
        "argv": docker_argv,
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
                "package_path": skill.get("package_path"),
                "package_tree_sha256": skill["package_tree_sha256"],
                "package_files": skill.get("package_files", []),
                "loader_path": relative.as_posix(),
                "loader_sha256": loader_sha256,
            }
        )

    clients = render_clients(root, output / bridge.relative_to(root))
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
            "mcp_config_source": probe["mcp"].get("source"),
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


def create_fixture(context: OperationContext, request: dict[str, Any]) -> HandlerResult:
    run_id = request.get("run_id") if isinstance(request, dict) else None
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

    before_complete = False
    try:
        before, before_error, before_complete = capture_run_manifest(
            context,
            inspect["Id"],
            "01-before-run-manifest",
        )
        if before is None:
            raise ValueError(before_error or "run manifest inspection failed")
    except (DockerError, OSError, TypeError, ValueError) as exc:
        raise OperationFailure(
            "fixture creation could not establish the run's pre-probe identity",
            effect_certainty=EffectCertainty.NONE,
            evidence_completeness=(
                EvidenceCompleteness.COMPLETE
                if before_complete
                else EvidenceCompleteness.PARTIAL
            ),
            resource={"kind": "run", "id": run_id, "docker_id": inspect["Id"]},
            error_type=type(exc).__name__,
        ) from exc

    probe_error: Exception | None = None
    raw_probe: dict[str, Any] | None = None
    try:
        raw_probe = _probe_active_brain(
            context,
            container_id=inspect["Id"],
            skills=skills,
        )
    except (DockerError, OSError, TypeError, ValueError) as exc:
        probe_error = exc
    after_complete = False
    try:
        after, after_error, after_complete = capture_run_manifest(
            context,
            inspect["Id"],
            "03-after-run-manifest",
        )
        if after is None:
            raise ValueError(after_error or "run manifest inspection failed")
    except (DockerError, OSError, TypeError, ValueError) as exc:
        raise OperationFailure(
            "fixture creation could not prove whether active-Brain inspection changed the run",
            effect_certainty=EffectCertainty.UNKNOWN,
            evidence_completeness=(
                EvidenceCompleteness.COMPLETE
                if after_complete
                else EvidenceCompleteness.PARTIAL
            ),
            resource={"kind": "run", "id": run_id, "docker_id": inspect["Id"]},
            payload={"before_tree_sha256": before.get("tree_sha256")},
            error_type=type(exc).__name__,
        ) from exc

    run_diff = filesystem_diff(before, after)
    (context.evidence_directory / "run-manifest-diff.json").write_text(
        json.dumps(run_diff, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if not run_diff["equal"]:
        raise OperationFailure(
            "active-Brain inspection changed the retained run; no fixture was published",
            effect_certainty=EffectCertainty.PARTIAL,
            resource={"kind": "run", "id": run_id, "docker_id": inspect["Id"]},
            payload={"run_modified": True, "run_manifest_diff": run_diff},
            error_type="RunModified",
        )
    if probe_error is not None:
        raise OperationFailure(
            str(probe_error),
            effect_certainty=EffectCertainty.NONE,
            resource={"kind": "run", "id": run_id, "docker_id": inspect["Id"]},
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
            resource={"kind": "run", "id": run_id, "docker_id": inspect["Id"]},
            payload={"run_modified": False},
            error_type=type(exc).__name__,
        ) from exc

    try:
        bridge_source = (context.tool_root / "host_fixture" / "brain_mcp_bridge.py").read_text(
            encoding="utf-8"
        )
    except OSError as exc:
        raise OperationFailure(
            "fixture bridge asset is unavailable",
            effect_certainty=EffectCertainty.NONE,
            resource={"kind": "run", "id": run_id, "docker_id": inspect["Id"]},
            error_type=type(exc).__name__,
        ) from exc
    try:
        output.mkdir(mode=0o700)
        output_identity = _directory_identity(output)
    except FileExistsError as exc:
        raise OperationFailure(
            f"fixture output already exists: {output}",
            effect_certainty=EffectCertainty.NONE,
            resource={"kind": "run", "id": run_id, "docker_id": inspect["Id"]},
            error_type=type(exc).__name__,
        ) from exc
    try:
        manifest = _materialise_fixture(
            output,
            output=output,
            run_id=run_id,
            receipt=receipt,
            inspect=inspect,
            probe=probe,
            docker_executable=context.docker.executable,
            bridge_source=bridge_source,
        )
    except Exception as exc:
        cleanup_error = _remove_owned_output(output, output_identity)
        if cleanup_error is not None:
            raise OperationFailure(
                "fixture publication failed and its output could not be safely removed",
                effect_certainty=EffectCertainty.PARTIAL,
                resource={"kind": "run", "id": run_id, "docker_id": inspect["Id"]},
                payload={"output": str(output), "cleanup_error": cleanup_error},
                error_type=type(exc).__name__,
            ) from exc
        raise OperationFailure(
            str(exc),
            effect_certainty=EffectCertainty.NONE,
            resource={"kind": "run", "id": run_id, "docker_id": inspect["Id"]},
            error_type=type(exc).__name__,
        ) from exc

    return HandlerResult(
        resource={"kind": "run", "id": run_id, "docker_id": inspect["Id"]},
        payload={
            "output": str(output),
            "manifest": manifest,
            "run_retained": True,
            "run_modified": False,
        },
        effect_certainty=EffectCertainty.COMMITTED,
    )


def register_fixture_handlers(application: Application) -> None:
    application.register("fixture.create", create_fixture, mutating=True)
