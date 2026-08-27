from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from .manifests import TreeManifest, manifest_tree
from .process import CommandRunner, ProcessExecution


@dataclass(frozen=True)
class WorktreeCapture:
    root: Path
    commit: str
    manifest: TreeManifest
    tracked_paths: tuple[str, ...]
    deleted_tracked_paths: tuple[str, ...]
    included_untracked_paths: tuple[str, ...]
    excluded_untracked_paths: tuple[str, ...]


def _output(execution: ProcessExecution, action: str) -> bytes:
    if not execution.succeeded:
        raise RuntimeError(f"{action} failed with exit code {execution.returncode}")
    if execution.stdout.truncated:
        raise RuntimeError(f"{action} output exceeded its retention bound")
    return Path(execution.stdout.path).read_bytes()


def _git(
    runner: CommandRunner,
    arguments: Sequence[str],
    *,
    evidence_directory: Path,
    timeout_seconds: float = 300,
) -> bytes:
    execution = runner.run(
        ["git", *arguments],
        evidence_directory=evidence_directory,
        timeout_seconds=timeout_seconds,
        environment={"GIT_TERMINAL_PROMPT": "0"},
    )
    return _output(execution, f"git {' '.join(arguments)}")


def _nul_paths(value: bytes) -> tuple[str, ...]:
    return tuple(item.decode("utf-8", errors="surrogateescape") for item in value.split(b"\0") if item)


def _expand_explicit_paths(root: Path, paths: Iterable[str]) -> tuple[str, ...]:
    expanded: set[str] = set()
    for raw in paths:
        candidate = Path(raw)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError(f"explicit capture path must be relative and contained: {raw}")
        full = root / candidate
        if not os.path.lexists(full):
            raise ValueError(f"explicit capture path does not exist: {raw}")
        if full.is_dir() and not full.is_symlink():
            for child in full.rglob("*"):
                if not child.is_dir():
                    expanded.add(child.relative_to(root).as_posix())
        else:
            expanded.add(candidate.as_posix())
    return tuple(sorted(expanded))


def capture_worktree(
    runner: CommandRunner,
    root: Path,
    *,
    include_untracked: Iterable[str] = (),
    evidence_directory: Path,
) -> WorktreeCapture:
    root = root.expanduser().resolve()
    commit = _git(
        runner,
        ["-C", str(root), "rev-parse", "HEAD"],
        evidence_directory=evidence_directory / "rev-parse",
    ).decode().strip()
    tracked_all = _nul_paths(
        _git(
            runner,
            ["-C", str(root), "ls-files", "-z"],
            evidence_directory=evidence_directory / "tracked",
        )
    )
    tracked = tuple(path for path in tracked_all if os.path.lexists(root / path))
    deleted_tracked = tuple(sorted(set(tracked_all) - set(tracked)))
    ordinary_untracked = _nul_paths(
        _git(
            runner,
            ["-C", str(root), "ls-files", "--others", "--exclude-standard", "-z"],
            evidence_directory=evidence_directory / "untracked",
        )
    )
    explicit = _expand_explicit_paths(root, include_untracked)
    selected = tuple(sorted(set(tracked) | set(explicit)))
    before = manifest_tree(root, selected)
    after_commit = _git(
        runner,
        ["-C", str(root), "rev-parse", "HEAD"],
        evidence_directory=evidence_directory / "rev-parse-after",
    ).decode().strip()
    after = manifest_tree(root, selected)
    if after_commit != commit or after.tree_sha256 != before.tree_sha256:
        raise RuntimeError("source worktree changed during capture; no bundle was promoted")
    return WorktreeCapture(
        root=root,
        commit=commit,
        manifest=before,
        tracked_paths=tuple(sorted(tracked)),
        deleted_tracked_paths=deleted_tracked,
        included_untracked_paths=explicit,
        excluded_untracked_paths=tuple(sorted(set(ordinary_untracked) - set(explicit))),
    )


def resolve_remote_ref(
    runner: CommandRunner,
    repository: str,
    ref: str,
    *,
    evidence_directory: Path,
) -> str:
    output = _git(
        runner,
        ["ls-remote", repository, ref, f"{ref}^{{}}"],
        evidence_directory=evidence_directory,
        timeout_seconds=120,
    ).decode()
    rows = [line.split() for line in output.splitlines() if line.strip()]
    if not rows:
        raise ValueError(f"remote ref did not resolve: {repository} {ref}")
    peeled = [row[0] for row in rows if row[1].endswith("^{}")]
    commits = peeled or [row[0] for row in rows]
    if len(set(commits)) != 1:
        raise ValueError(f"remote ref is ambiguous: {repository} {ref}")
    return commits[0]


class RemoteCheckout:
    def __init__(self, runner: CommandRunner, repository: str, ref: str, evidence_directory: Path):
        self.runner = runner
        self.repository = repository
        self.ref = ref
        self.evidence_directory = evidence_directory
        self._temporary: tempfile.TemporaryDirectory | None = None
        self.path: Path | None = None
        self.commit: str | None = None

    def __enter__(self) -> tuple[Path, str]:
        commit = resolve_remote_ref(
            self.runner,
            self.repository,
            self.ref,
            evidence_directory=self.evidence_directory / "resolve",
        )
        self._temporary = tempfile.TemporaryDirectory(prefix="brain-lab-source-")
        self.path = Path(self._temporary.name)
        _git(
            self.runner,
            ["clone", "--no-checkout", "--filter=blob:none", self.repository, str(self.path)],
            evidence_directory=self.evidence_directory / "clone",
            timeout_seconds=900,
        )
        _git(
            self.runner,
            ["-C", str(self.path), "checkout", "--detach", commit],
            evidence_directory=self.evidence_directory / "checkout",
            timeout_seconds=900,
        )
        self.commit = commit
        return self.path, commit

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._temporary is not None:
            self._temporary.cleanup()
