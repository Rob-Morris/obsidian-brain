"""Installed disposable-vault substrate for command-interface tests.

The tracked seed contains authored data only.  This module assembles it through
the supported installer and compiler, then gives effectful tests isolated
writable clones while pure contract tests continue to use in-memory fixtures.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import threading
import time
from typing import Iterator, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
COMMAND_VAULT_FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "command-vault"
COMMAND_VAULT_MANIFEST = COMMAND_VAULT_FIXTURE_ROOT / "manifest.json"
COMMAND_VAULT_OVERLAYS = COMMAND_VAULT_FIXTURE_ROOT / "overlays.json"
COMMAND_VAULT_SEED = COMMAND_VAULT_FIXTURE_ROOT / "seed"
COMMAND_VAULT_TIMEZONE = "Australia/Sydney"
COMMAND_VAULT_NOW = datetime.fromisoformat("2026-08-09T09:30:00+10:00")

_IGNORED_HASH_NAMES = {".DS_Store", ".pytest_cache", "__pycache__"}
_FORBIDDEN_SEED_PARTS = {".brain-core", ".venv"}
_ENVIRONMENT_LOCK = threading.RLock()
_CLONE_DIAGNOSTICS_LOCK = threading.Lock()
_CLONE_DIAGNOSTICS: list[dict[str, object]] = []


@dataclass(frozen=True)
class CommandVaultBaseline:
    """An assembled read-only-by-contract source for disposable clones."""

    vault_root: Path
    cache_key: str
    source_hash: str
    immutable_hash: str
    assembly_seconds: float
    artefact_count: int
    document_count: int
    evidence_path: Path


@dataclass(frozen=True)
class CommandVaultClone:
    """One writable vault plus all machine and effect state owned by its test."""

    vault_root: Path
    strategy: str
    clone_seconds: float
    config_home: Path
    resolution_runtime: Path
    staging_root: Path
    provider_state_root: Path
    outcome_receipt_root: Path

    @property
    def environment(self) -> dict[str, str]:
        """Return explicit process environment overrides for this clone."""
        return {
            "XDG_CONFIG_HOME": str(self.config_home),
            "BRAIN_RESOLUTION_RUNTIME_DIR": str(self.resolution_runtime),
            "BRAIN_STAGING_ROOT": str(self.staging_root),
            "BRAIN_PROVIDER_STATE_DIR": str(self.provider_state_root),
            "BRAIN_OUTCOME_RECEIPT_DIR": str(self.outcome_receipt_root),
            "TZ": COMMAND_VAULT_TIMEZONE,
        }


@dataclass
class CommandVaultOverlay:
    """An applied named fault state, including any held test resources."""

    name: str
    clone: CommandVaultClone
    category: str
    expected: str
    details: dict
    _resources: tuple[object, ...] = ()

    def close(self) -> None:
        """Release held locks or other overlay resources in reverse order."""
        while self._resources:
            resource, *remaining = self._resources
            self._resources = tuple(remaining)
            resource.__exit__(None, None, None)

    def __enter__(self) -> "CommandVaultOverlay":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


def load_command_vault_manifest() -> dict:
    """Load the authored seed contract."""
    return json.loads(COMMAND_VAULT_MANIFEST.read_text(encoding="utf-8"))


def load_command_vault_overlays() -> dict:
    """Load the closed set of named fresh-clone fault states."""
    return json.loads(COMMAND_VAULT_OVERLAYS.read_text(encoding="utf-8"))


def validate_command_vault_seed() -> None:
    """Reject generated/runtime content and incomplete stable seed references."""
    manifest = load_command_vault_manifest()
    if manifest.get("schema") != "brain.command-vault-seed/1":
        raise ValueError("unsupported command-vault seed manifest")
    if manifest.get("clock") != {
        "instant": COMMAND_VAULT_NOW.isoformat(),
        "timezone": COMMAND_VAULT_TIMEZONE,
    }:
        raise ValueError("command-vault clock does not match the pinned test clock")

    for path in sorted(COMMAND_VAULT_SEED.rglob("*")):
        relative = path.relative_to(COMMAND_VAULT_SEED)
        if any(part in _FORBIDDEN_SEED_PARTS for part in relative.parts):
            raise ValueError(f"generated or installed content is forbidden in seed: {relative}")
        if relative.parts[:2] == (".brain", "local"):
            raise ValueError(f"generated local state is forbidden in seed: {relative}")

    stable_paths = {
        item["path"] for item in manifest["stable_artefacts"].values()
    }
    stable_paths.add(manifest["destinations"]["attachment"])
    missing = sorted(path for path in stable_paths if not (COMMAND_VAULT_SEED / path).is_file())
    if missing:
        raise ValueError(f"command-vault seed paths are missing: {missing}")


def _iter_hashed_paths(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root)
        if any(part in _IGNORED_HASH_NAMES for part in relative.parts):
            continue
        if path.is_file() or path.is_symlink():
            yield path


def tree_hash(root: Path) -> str:
    """Return a stable content hash without timestamps or generated test junk."""
    digest = hashlib.sha256()
    for path in _iter_hashed_paths(root):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        if path.is_symlink():
            digest.update(b"link\0")
            digest.update(os.readlink(path).encode("utf-8"))
        else:
            digest.update(b"file\0")
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def command_vault_source_hash(source_root: Path = REPO_ROOT) -> str:
    """Hash every source that determines an assembled baseline."""
    components = {
        "brain_core": tree_hash(source_root / "src" / "brain-core"),
        "seed": tree_hash(COMMAND_VAULT_FIXTURE_ROOT),
        "template_vault": tree_hash(source_root / "template-vault"),
    }
    encoded = json.dumps(components, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@contextmanager
def isolated_command_environment(overrides: Mapping[str, str]) -> Iterator[None]:
    """Temporarily install explicit state roots without leaking process state."""
    with _ENVIRONMENT_LOCK:
        previous = {name: os.environ.get(name) for name in overrides}
        os.environ.update(overrides)
        if hasattr(time, "tzset") and "TZ" in overrides:
            time.tzset()
        try:
            yield
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
            if hasattr(time, "tzset") and "TZ" in overrides:
                time.tzset()


def assemble_command_vault_baseline(
    destination: Path,
    *,
    machine_state_root: Path,
    source_root: Path = REPO_ROOT,
) -> CommandVaultBaseline:
    """Install, seed, compile, index and check one immutable baseline."""
    import check
    import compile_router
    import install
    import _search.index as search_index

    validate_command_vault_seed()
    destination = destination.resolve()
    machine_state_root = machine_state_root.resolve()
    if destination.exists():
        raise FileExistsError(f"baseline destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    machine_state_root.mkdir(parents=True, exist_ok=True)

    source_hash = command_vault_source_hash(source_root)
    cache_key = f"command-vault-{source_hash[:20]}"
    environment = {
        "XDG_CONFIG_HOME": str(machine_state_root / "config"),
        "BRAIN_RESOLUTION_RUNTIME_DIR": str(machine_state_root / "resolution-runtime"),
        "TZ": COMMAND_VAULT_TIMEZONE,
    }
    started = time.perf_counter()
    with isolated_command_environment(environment):
        install_result = install.install_vault_action(
            destination,
            source_root=source_root,
            mcp_scope="skip",
            brain_id=cache_key,
        )
        if install_result.get("status") != "ok":
            raise RuntimeError(f"command-vault install failed: {install_result}")

        shutil.copytree(COMMAND_VAULT_SEED, destination, dirs_exist_ok=True)
        for relative in load_command_vault_manifest()["destinations"].values():
            target = destination / relative
            if target.suffix:
                continue
            target.mkdir(parents=True, exist_ok=True)

        router = compile_router.compile(str(destination))
        compile_router.persist_compiled_router(str(destination), router)
        compile_router.refresh_session_markdown(str(destination), router)
        build_result = search_index.build_index(str(destination))
        search_index.persist_retrieval_index(str(destination), build_result.index)
        check_result = check.run_checks(str(destination))

    if check_result["findings"]:
        raise RuntimeError(f"command-vault baseline failed Brain checks: {check_result['findings']}")

    assembly_seconds = time.perf_counter() - started
    immutable_hash = tree_hash(destination)
    evidence = {
        "schema": "brain.command-vault-baseline-evidence/1",
        "cache_key": cache_key,
        "source_hash": source_hash,
        "immutable_hash": immutable_hash,
        "clock": load_command_vault_manifest()["clock"],
        "assembly_seconds": assembly_seconds,
        "artefact_count": len(router["artefacts"]),
        "document_count": build_result.index["meta"]["document_count"],
        "check_summary": check_result["summary"],
    }
    evidence_path = destination.parent / "assembly-evidence.json"
    evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return CommandVaultBaseline(
        vault_root=destination,
        cache_key=cache_key,
        source_hash=source_hash,
        immutable_hash=immutable_hash,
        assembly_seconds=assembly_seconds,
        artefact_count=len(router["artefacts"]),
        document_count=build_result.index["meta"]["document_count"],
        evidence_path=evidence_path,
    )


def _run_clone_command(arguments: list[str]) -> bool:
    try:
        result = subprocess.run(
            arguments,
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _copy_on_write_clone(source: Path, destination: Path) -> str | None:
    cp = shutil.which("cp")
    if cp is None:
        return None
    if platform.system() == "Darwin":
        arguments = [cp, "-cR", str(source), str(destination)]
        strategy = "darwin-clonefile"
    else:
        arguments = [cp, "--reflink=always", "-a", str(source), str(destination)]
        strategy = "gnu-reflink"
    if _run_clone_command(arguments):
        return strategy
    if destination.exists():
        shutil.rmtree(destination)
    return None


def clone_command_vault(
    baseline: CommandVaultBaseline,
    destination: Path,
) -> CommandVaultClone:
    """Create one isolated writable clone, preferring filesystem CoW support."""
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError(f"clone destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    strategy = _copy_on_write_clone(baseline.vault_root, destination)
    if strategy is None:
        shutil.copytree(baseline.vault_root, destination)
        strategy = "portable-copy"

    state_root = destination.parent / f".{destination.name}-state"
    config_home = state_root / "config"
    resolution_runtime = state_root / "resolution-runtime"
    staging_root = destination / ".brain" / "local" / "command-staging"
    provider_state_root = destination / ".brain" / "local" / "command-providers"
    outcome_receipt_root = destination / ".brain" / "local" / "command-outcomes"
    for path in (
        config_home,
        resolution_runtime,
        staging_root,
        provider_state_root,
        outcome_receipt_root,
    ):
        path.mkdir(parents=True, exist_ok=True)

    clone = CommandVaultClone(
        vault_root=destination,
        strategy=strategy,
        clone_seconds=time.perf_counter() - started,
        config_home=config_home,
        resolution_runtime=resolution_runtime,
        staging_root=staging_root,
        provider_state_root=provider_state_root,
        outcome_receipt_root=outcome_receipt_root,
    )
    with _CLONE_DIAGNOSTICS_LOCK:
        _CLONE_DIAGNOSTICS.append(
            {
                "strategy": clone.strategy,
                "seconds": clone.clone_seconds,
            }
        )
    return clone


def command_vault_clone_diagnostics() -> dict:
    """Return bounded timing evidence without exposing temporary vault paths."""
    with _CLONE_DIAGNOSTICS_LOCK:
        records = [dict(item) for item in _CLONE_DIAGNOSTICS]
    strategy_counts: dict[str, int] = {}
    for record in records:
        strategy = str(record["strategy"])
        strategy_counts[strategy] = strategy_counts.get(strategy, 0) + 1
    durations = sorted(float(record["seconds"]) for record in records)
    return {
        "schema": "brain.command-vault-clone-evidence/1",
        "clone_count": len(records),
        "strategy_counts": strategy_counts,
        "total_seconds": sum(durations),
        "slowest_seconds": durations[-1] if durations else 0.0,
    }


def write_command_vault_clone_evidence(path: Path) -> None:
    """Persist aggregate clone diagnostics beside, never inside, the baseline."""
    path.write_text(
        json.dumps(command_vault_clone_diagnostics(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _replace_text(path: Path, old: str, new: str) -> None:
    content = path.read_text(encoding="utf-8")
    if old not in content:
        raise ValueError(f"overlay target text is absent from {path}: {old!r}")
    path.write_text(content.replace(old, new, 1), encoding="utf-8")


def apply_command_vault_overlay(
    clone: CommandVaultClone,
    name: str,
) -> CommandVaultOverlay:
    """Apply one deterministic fault/state overlay to a fresh writable clone."""
    from _common._file_lock import vault_mutation_lock

    definitions = load_command_vault_overlays()
    if definitions.get("schema") != "brain.command-vault-overlays/1":
        raise ValueError("unsupported command-vault overlays manifest")
    try:
        definition = definitions["overlays"][name]
    except KeyError as exc:
        available = ", ".join(sorted(definitions.get("overlays", {})))
        raise KeyError(f"unknown command-vault overlay {name!r}; expected one of: {available}") from exc

    manifest = load_command_vault_manifest()
    details: dict = {}
    resources: list[object] = []
    project = clone.vault_root / manifest["stable_artefacts"]["project"]["path"]
    plan = clone.vault_root / manifest["stable_artefacts"]["owned_plan"]["path"]
    candidate = clone.vault_root / manifest["stable_artefacts"]["lifecycle_candidate"]["path"]
    attachment = clone.vault_root / manifest["destinations"]["attachment"]
    machine_brain_dir = clone.config_home / "brain"
    applied_state_path = clone.config_home.parent / "applied-overlay.json"
    if applied_state_path.exists():
        raise RuntimeError("command-vault overlays must be applied to a fresh clone")

    if name == "stale-router":
        router_source = clone.vault_root / "_Config" / "router.md"
        router_source.write_text(
            router_source.read_text(encoding="utf-8") + "\n<!-- command overlay: stale router -->\n",
            encoding="utf-8",
        )
        details["changed_path"] = str(router_source)
    elif name == "stale-index":
        plan.write_text(plan.read_text(encoding="utf-8") + "\nUnindexed overlay text.\n", encoding="utf-8")
        details["changed_path"] = str(plan)
    elif name == "malformed-content":
        project.write_bytes(project.read_bytes() + b"\n\xff\xfe\n")
        details["changed_path"] = str(project)
    elif name == "malformed-frontmatter":
        _replace_text(candidate, "tags:\n  - idea", "tags: [idea")
        details["changed_path"] = str(candidate)
    elif name == "missing-file":
        attachment.unlink()
        details["missing_path"] = str(attachment)
    elif name == "missing-runtime":
        shutil.rmtree(clone.resolution_runtime)
        details["missing_path"] = str(clone.resolution_runtime)
    elif name == "missing-provider":
        _write_json(
            clone.provider_state_root / "capabilities.json",
            {"provider": "test-provider", "state": "unavailable", "freshness": "fresh"},
        )
    elif name == "insufficient-authority":
        _write_json(
            clone.provider_state_root / "authority.json",
            {"profile": "reader", "allowed": ["read"], "denied": ["mutation"]},
        )
    elif name == "lock-contention":
        lock = vault_mutation_lock(clone.vault_root, timeout=0.05)
        lock.__enter__()
        resources.append(lock)
        details["lock_path"] = str(clone.vault_root / ".brain" / "local" / "mutation.lock")
    elif name == "registry-blocked":
        _write_json(machine_brain_dir / "brains.json", {"version": 999, "brains": []})
    elif name == "registry-incomplete":
        _write_json(
            machine_brain_dir / "brains.json",
            {"version": 1, "brains": [{"alias": "incomplete"}]},
        )
    elif name == "registry-stale":
        missing = clone.config_home.parent / "missing-brain"
        machine_brain_dir.mkdir(parents=True, exist_ok=True)
        (machine_brain_dir / "vaults").write_text(
            f"# brain registry v2\nstale\tlocal\t{missing}\n",
            encoding="utf-8",
        )
        _write_json(
            machine_brain_dir / "brains.json",
            {"version": 1, "brains": [{"alias": "stale", "path": str(missing)}]},
        )
        details["missing_path"] = str(missing)
    elif name == "lifecycle-state":
        _replace_text(candidate, "status: ready", "status: parked")
    elif name == "upgrade-state":
        version_path = clone.vault_root / ".brain-core" / "VERSION"
        details["current_version"] = version_path.read_text(encoding="utf-8").strip()
        version_path.write_text("0.53.0\n", encoding="utf-8")
        _write_json(
            clone.vault_root / ".brain" / "upgrade-state.json",
            {"from": "0.53.0", "state": "preflight", "target": "command-interface-cutover"},
        )
    elif name in {
        "failure-before-commit",
        "failure-during-commit",
        "failure-after-commit",
        "known-partial-effects",
        "unknown-outcome",
        "rollback-failure",
    }:
        phase = name.removeprefix("failure-") if name.startswith("failure-") else name
        fault = {
            "fault": name,
            "phase": phase,
            "invocation_id": f"overlay-{name}",
        }
        _write_json(clone.provider_state_root / "fault-injection.json", fault)
        if name in {"failure-after-commit", "known-partial-effects", "unknown-outcome", "rollback-failure"}:
            effects = {
                "failure-after-commit": "committed",
                "known-partial-effects": "partial",
                "unknown-outcome": "unknown",
                "rollback-failure": "partial",
            }[name]
            receipt = {
                "schema": "brain.command-outcome-fixture/1",
                "invocation_id": fault["invocation_id"],
                "effects": effects,
                "retryable": effects not in {"committed", "unknown"},
            }
            if name == "known-partial-effects":
                receipt["committed_effects"] = ["artefact-body"]
                receipt["failed_effects"] = ["derived-index"]
            if name == "rollback-failure":
                receipt["recovery_material"] = "retained"
            _write_json(clone.outcome_receipt_root / f"{fault['invocation_id']}.json", receipt)
    else:  # pragma: no cover - manifest/code drift is asserted independently
        raise AssertionError(f"overlay has no implementation: {name}")

    state = {
        "schema": "brain.command-vault-applied-overlay/1",
        "name": name,
        "category": definition["category"],
        "expected": definition["expected"],
        "details": details,
    }
    _write_json(applied_state_path, state)
    return CommandVaultOverlay(
        name=name,
        clone=clone,
        category=definition["category"],
        expected=definition["expected"],
        details=details,
        _resources=tuple(reversed(resources)),
    )
