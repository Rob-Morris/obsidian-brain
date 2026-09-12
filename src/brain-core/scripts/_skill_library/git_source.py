"""Explicit Git acquisition boundary for skill packages."""

from __future__ import annotations

from contextlib import contextmanager
from ipaddress import IPv6Address
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
from typing import Iterator

from _common import validate_portable_relative_path

from .models import SourceCheckout
from .packages import MAX_PACKAGE_BYTES, MAX_PACKAGE_FILES, inspect_package


class GitSourceError(RuntimeError):
    """A configured Git source could not be resolved and staged safely."""


MAX_ARCHIVE_BYTES = MAX_PACKAGE_BYTES + (MAX_PACKAGE_FILES + 4) * 1024
_RESERVED_SCHEME_PREFIXES = frozenset(
    ("ext", "file", "git", "http", "https", "ssh")
)
_SSH_IDENTITY = r"[A-Za-z0-9._][A-Za-z0-9._-]{0,63}"
_HOST = r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?"
_URL_REMOTE = re.compile(
    rf"^(?P<scheme>https|ssh)://(?:(?P<identity>{_SSH_IDENTITY})@)?"
    rf"(?P<host>\[[0-9A-Fa-f:.]+\]|{_HOST})"
    r"(?::(?P<port>[0-9]{1,5}))?(?P<path>/[^?#\\\s]+)$",
    re.IGNORECASE,
)
_SCP_REMOTE = re.compile(
    rf"^(?:(?P<identity>{_SSH_IDENTITY})@)?"
    rf"(?P<host>{_HOST})"
    r":(?P<path>[^\s]+)$"
)
_WINDOWS_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")
_HOST_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9])?$")
_SAFE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,255}$")


class RepositoryCheckout:
    """One fetched repository revision serving isolated skill-package stages."""

    def __init__(
        self,
        *,
        git: str,
        checkout: Path,
        staging_root: Path,
        repository: str,
        configured_ref: str,
        resolved_commit: str,
    ) -> None:
        self._git = git
        self._checkout = checkout
        self._staging_root = staging_root
        self.repository = repository
        self.configured_ref = configured_ref
        self.resolved_commit = resolved_commit

    def checkout_source(
        self,
        *,
        skill_path: str,
        expected_name: str | None = None,
    ) -> SourceCheckout:
        """Stage and validate one package from the fetched revision."""

        normalized_skill_path = _normalise_skill_path(skill_path)
        archive_arguments = [
            "-C",
            str(self._checkout),
            "archive",
            "--format=tar",
            self.resolved_commit,
        ]
        if normalized_skill_path != ".":
            archive_arguments.append(normalized_skill_path)
        archive = _run_archive(self._git, *archive_arguments)
        package_stage = Path(
            tempfile.mkdtemp(prefix="package-", dir=self._staging_root)
        )
        _extract_archive(archive, package_stage)
        package_root = (
            package_stage
            if normalized_skill_path == "."
            else package_stage / normalized_skill_path
        )
        try:
            package = inspect_package(package_root, expected_name=expected_name)
        except ValueError as exc:
            raise GitSourceError(
                f"invalid skill package at {skill_path!r}: {exc}"
            ) from exc
        return SourceCheckout(
            self.repository,
            self.configured_ref,
            self.resolved_commit,
            normalized_skill_path,
            package,
        )


def validate_remote_repository(repository: str) -> str:
    """Return one approved remote Git location or reject local/helper forms."""

    if not isinstance(repository, str) or not repository:
        raise GitSourceError("Git repository must be a non-empty string")
    if repository != repository.strip() or any(
        char.isspace() or ord(char) < 32 for char in repository
    ):
        raise GitSourceError(
            "Git repository contains unsafe whitespace or control characters"
        )
    if _WINDOWS_DRIVE_PREFIX.match(repository):
        raise GitSourceError("Git repository must use an approved https or ssh remote")
    url = _URL_REMOTE.fullmatch(repository)
    if url is not None:
        scheme = url.group("scheme").casefold()
        if scheme == "https" and url.group("identity") is not None:
            raise GitSourceError(
                "Git repository remote contains unsupported credentials or suffixes"
            )
        if not _valid_remote_host(url.group("host")):
            raise GitSourceError("Git repository remote has invalid host syntax")
        port = url.group("port")
        if port is not None and not 1 <= int(port) <= 65535:
            raise GitSourceError("Git repository remote has invalid port syntax")
        return repository

    prefix = repository.partition(":")[0].casefold()
    if "://" in repository or prefix in _RESERVED_SCHEME_PREFIXES:
        raise GitSourceError("Git repository must use an approved https or ssh remote")
    scp = _SCP_REMOTE.fullmatch(repository)
    if scp is not None:
        path = scp.group("path")
        if (
            _valid_remote_host(scp.group("host"))
            and path not in {"/", ".", ".."}
            and not path.startswith(":")
            and not any(marker in path for marker in ("\\", "?", "#"))
        ):
            return repository
    raise GitSourceError("Git repository must use an approved https or ssh remote")


def _valid_remote_host(host: str) -> bool:
    """Accept one closed, ASCII hostname or bracketed IPv6 literal."""

    if host.startswith("[") and host.endswith("]"):
        try:
            IPv6Address(host[1:-1])
        except ValueError:
            return False
        return True
    return len(host) <= 253 and all(
        _HOST_LABEL.fullmatch(label) for label in host.split(".")
    )


def validate_configured_ref(configured_ref: str) -> str:
    """Return a fetch-safe branch, tag, symbolic ref, or commit identity."""

    if not isinstance(configured_ref, str) or not configured_ref:
        raise GitSourceError("Git ref must be a non-empty string")
    if (
        configured_ref != configured_ref.strip()
        or not _SAFE_REF.fullmatch(configured_ref)
        or ".." in configured_ref
        or "//" in configured_ref
        or "@{" in configured_ref
        or configured_ref.endswith(("/", ".", ".lock"))
    ):
        raise GitSourceError("Git ref contains unsafe or unsupported syntax")
    return configured_ref


@contextmanager
def checkout_repository(
    repository: str,
    *,
    configured_ref: str = "HEAD",
) -> Iterator[RepositoryCheckout]:
    """Fetch one bounded repository revision for request-scoped package reads."""

    repository = validate_remote_repository(repository)
    configured_ref = validate_configured_ref(configured_ref)
    git = shutil.which("git")
    if git is None:
        raise GitSourceError("Git is required to refresh or update a skill source")

    with tempfile.TemporaryDirectory(prefix="brain-skill-source-") as temporary:
        temporary_root = Path(temporary)
        checkout = temporary_root / "repository"
        staging_root = temporary_root / "packages"
        staging_root.mkdir()
        _run(git, "init", "--quiet", str(checkout))
        _run(git, "-C", str(checkout), "remote", "add", "origin", repository)
        _run(
            git,
            "-C",
            str(checkout),
            "fetch",
            "--quiet",
            "--depth=1",
            "origin",
            configured_ref,
        )
        commit = _run(
            git, "-C", str(checkout), "rev-parse", "--verify", "FETCH_HEAD"
        ).strip()
        yield RepositoryCheckout(
            git=git,
            checkout=checkout,
            staging_root=staging_root,
            repository=repository,
            configured_ref=configured_ref,
            resolved_commit=commit,
        )


@contextmanager
def checkout_source(
    repository: str,
    *,
    skill_path: str,
    configured_ref: str = "HEAD",
    expected_name: str | None = None,
) -> Iterator[SourceCheckout]:
    """Fetch, bound, validate, and temporarily expose one Git skill package."""

    with checkout_repository(
        repository,
        configured_ref=configured_ref,
    ) as checkout:
        yield checkout.checkout_source(
            skill_path=skill_path,
            expected_name=expected_name,
        )


def _normalise_skill_path(skill_path: str) -> str:
    if skill_path == ".":
        return "."
    try:
        return validate_portable_relative_path(skill_path)
    except ValueError as exc:
        raise GitSourceError(str(exc)) from exc


def _run(program: str, *arguments: str) -> str:
    return _run_bytes(program, *arguments).decode("utf-8")


def _run_bytes(program: str, *arguments: str) -> bytes:
    try:
        completed = subprocess.run(
            (program, *arguments),
            capture_output=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitSourceError(f"Git source command failed: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).decode(
            "utf-8", errors="replace"
        ).strip()
        if len(detail) > 500:
            detail = detail[:497] + "..."
        raise GitSourceError(detail or "Git source command failed")
    return completed.stdout


def _run_archive(program: str, *arguments: str) -> bytes:
    """Capture a Git archive with an acquisition-time memory bound."""

    with tempfile.TemporaryFile() as error_output:
        try:
            process = subprocess.Popen(
                (program, *arguments),
                stdout=subprocess.PIPE,
                stderr=error_output,
            )
        except OSError as exc:
            raise GitSourceError(f"Git source command failed: {exc}") from exc
        assert process.stdout is not None
        timed_out = threading.Event()

        def terminate_for_timeout() -> None:
            if process.poll() is None:
                timed_out.set()
                try:
                    process.kill()
                except OSError:
                    pass

        timer = threading.Timer(120, terminate_for_timeout)
        timer.start()
        chunks: list[bytes] = []
        total = 0
        try:
            while chunk := process.stdout.read(64 * 1024):
                total += len(chunk)
                if total > MAX_ARCHIVE_BYTES:
                    process.kill()
                    process.wait()
                    raise GitSourceError(
                        "Git skill archive exceeds safe staging limits"
                    )
                chunks.append(chunk)
            returncode = process.wait()
        finally:
            timer.cancel()
            process.stdout.close()
        if timed_out.is_set():
            raise GitSourceError("Git source command timed out")
        if returncode != 0:
            error_output.seek(0)
            detail = error_output.read(501).decode("utf-8", errors="replace").strip()
            if len(detail) > 500:
                detail = detail[:497] + "..."
            raise GitSourceError(detail or "Git source command failed")
        return b"".join(chunks)


def _extract_archive(archive: bytes, destination: Path) -> None:
    import io

    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
            members = bundle.getmembers()
            file_count = 0
            total_bytes = 0
            for member in members:
                name = member.name.rstrip("/")
                if name:
                    try:
                        validate_portable_relative_path(name)
                    except ValueError as exc:
                        raise GitSourceError(
                            f"Git archive contains an unsafe path: {member.name!r}"
                        ) from exc
                if member.isdir():
                    continue
                if not member.isfile():
                    raise GitSourceError(
                        f"Git archive contains an unsupported entry: {member.name!r}"
                    )
                file_count += 1
                total_bytes += member.size
                if file_count > MAX_PACKAGE_FILES or total_bytes > MAX_PACKAGE_BYTES:
                    raise GitSourceError("Git skill package exceeds safe staging limits")
            bundle.extractall(destination, filter="data")
    except (OSError, tarfile.TarError) as exc:
        raise GitSourceError(f"cannot stage Git skill archive: {exc}") from exc
