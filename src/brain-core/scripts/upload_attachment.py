#!/usr/bin/env python3
"""Upload a caller-owned file into the vault attachment namespace."""

from __future__ import annotations

import argparse
import base64
import binascii
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
from dataclasses import dataclass

from _common import (
    MutationLockError,
    find_vault_root,
    normalize_artefact_key,
    public_mutation_error_message,
    resolve_artefact_key_entry,
    resolve_and_check_bounds,
    safe_write_via,
    validate_key,
    vault_mutation_lock,
    validate_windows_portable_filename_segment,
)
from _lifecycle.derived_cache_state import load_fresh_compiled_router


ATTACHMENTS_REL_DIR = Path("_Assets") / "Attachments"
MAX_ATTACHMENT_BYTES = 16 * 1024 * 1024
_OBSIDIAN_UNSAFE_FILENAME_CHARS = frozenset("#[]")
_COMPARE_CHUNK_BYTES = 64 * 1024


@dataclass(frozen=True)
class AttachmentUploadPlan:
    destination: dict
    path: str
    embed: str
    bytes: int
    sha256: str
    would_create: bool
    target: Path
    content: bytes


def attachment_destination_requires_router(value: str) -> bool:
    """Return whether a destination is attempting artefact-key resolution."""
    return isinstance(value, str) and ("/" in value or "~" in value)


def artefact_attachment_folder(artefact_key: str) -> str:
    """Return the scope-form folder for one canonical living artefact key."""
    canonical_key = normalize_artefact_key(artefact_key)
    if canonical_key is None:
        raise ValueError(f"Invalid artefact attachment key: {artefact_key!r}")
    prefix, key = canonical_key.split("/", 1)
    return f"{prefix}~{key}"


def resolve_attachment_destination(router: dict | None, destination_key: str) -> dict:
    """Resolve an artefact key or validate a standalone attachment-folder key."""
    if not isinstance(destination_key, str) or not destination_key:
        raise ValueError("destination_key must be a non-empty key")
    if destination_key != destination_key.strip():
        raise ValueError("destination_key must not start or end with whitespace")

    canonical_key = normalize_artefact_key(destination_key)
    if canonical_key is not None:
        if router is None or "artefact_index" not in router:
            raise ValueError(
                "Compiled artefact index missing; attachment destination lookup "
                "is unavailable"
            )
        if resolve_artefact_key_entry(router, canonical_key) is None:
            raise ValueError(
                "INVALID_ATTACHMENT_DESTINATION: no active living artefact "
                f"matching '{destination_key}'"
            )
        return {
            "kind": "artefact",
            "key": canonical_key,
            "folder": artefact_attachment_folder(canonical_key),
        }

    if attachment_destination_requires_router(destination_key):
        raise ValueError(
            "INVALID_ATTACHMENT_DESTINATION: artefact destination must be a "
            "canonical type/key or type~key"
        )

    folder_key = validate_key(destination_key)
    return {"kind": "folder", "key": folder_key, "folder": folder_key}


def attachment_scope_rel_dir(artefact_key: str) -> Path:
    """Return the attachment directory derived from a living artefact key."""
    return ATTACHMENTS_REL_DIR / artefact_attachment_folder(artefact_key)


def existing_attachment_scope(
    vault_root: str | os.PathLike[str], artefact_key: str
) -> str | None:
    """Return an existing artefact-derived attachment directory, if present."""
    root = Path(vault_root).resolve()
    rel_dir = attachment_scope_rel_dir(artefact_key)
    target = root / rel_dir
    if not target.exists() and not target.is_symlink():
        return None
    _reject_symlinked_attachment_path(root, rel_dir)
    if not target.is_dir():
        raise ValueError(
            f"Attachment scope is not a directory: {rel_dir.as_posix()}"
        )
    return rel_dir.as_posix()


def plan_attachment_scope_moves(
    vault_root: str | os.PathLike[str], old_key: str | None, new_key: str | None
) -> list[dict[str, str]]:
    """Plan file moves for a retained living identity whose key changed."""
    if not old_key or not new_key:
        return []
    old_canonical = normalize_artefact_key(old_key)
    new_canonical = normalize_artefact_key(new_key)
    if old_canonical is None:
        raise ValueError(f"Invalid old artefact attachment key: {old_key!r}")
    if new_canonical is None:
        raise ValueError(f"Invalid new artefact attachment key: {new_key!r}")
    if old_canonical == new_canonical:
        return []

    root = Path(vault_root).resolve()
    old_rel_dir = attachment_scope_rel_dir(old_canonical)
    new_rel_dir = attachment_scope_rel_dir(new_canonical)
    old_dir = root / old_rel_dir
    new_dir = root / new_rel_dir
    if not old_dir.exists() and not old_dir.is_symlink():
        return []
    _reject_symlinked_attachment_path(root, old_rel_dir)
    _reject_symlinked_attachment_path(root, new_rel_dir)
    if not old_dir.is_dir():
        raise ValueError(
            f"Attachment scope is not a directory: {old_rel_dir.as_posix()}"
        )
    if new_dir.exists() or new_dir.is_symlink():
        raise FileExistsError(
            "Attachment scope destination already exists: "
            f"{new_rel_dir.as_posix()}"
        )

    def raise_walk_error(exc: OSError) -> None:
        raise OSError(
            f"Cannot enumerate attachment scope {old_rel_dir.as_posix()}: {exc}"
        ) from exc

    moves: list[dict[str, str]] = []
    for dirpath, dirnames, filenames in os.walk(
        old_dir, followlinks=False, onerror=raise_walk_error
    ):
        current = Path(dirpath)
        for dirname in dirnames:
            child = current / dirname
            if child.is_symlink():
                raise ValueError(
                    "Attachment namespace must not contain symlinks: "
                    f"{child.relative_to(root).as_posix()}"
                )
        for filename in filenames:
            source = current / filename
            if source.is_symlink() or not source.is_file():
                raise ValueError(
                    "Attachment scope may contain regular files only: "
                    f"{source.relative_to(root).as_posix()}"
                )
            suffix = source.relative_to(old_dir)
            moves.append({
                "source": source.relative_to(root).as_posix(),
                "dest": (new_dir / suffix).relative_to(root).as_posix(),
            })
    return moves


def plan_attachment_scope_transition(
    vault_root: str | os.PathLike[str], old_key: str | None, new_key: str | None
) -> dict:
    """Plan lifecycle moves and preservation metadata for one living identity."""
    moves = plan_attachment_scope_moves(vault_root, old_key, new_key)
    moved = None
    if moves:
        moved = {
            "from": attachment_scope_rel_dir(old_key).as_posix(),
            "to": attachment_scope_rel_dir(new_key).as_posix(),
        }

    orphaned_scopes = []
    if old_key and not new_key:
        orphaned_scope = existing_attachment_scope(vault_root, old_key)
        if orphaned_scope:
            orphaned_scopes.append(orphaned_scope)

    return {
        "moves": moves,
        "moved": moved,
        "orphaned_scopes": orphaned_scopes,
    }


def prune_vacated_attachment_scope(
    vault_root: str | os.PathLike[str], artefact_key: str | None
) -> None:
    """Remove empty directories left after a successful attachment-scope move."""
    if not artefact_key:
        return
    root = Path(vault_root).resolve()
    rel_dir = attachment_scope_rel_dir(artefact_key)
    scope_dir = root / rel_dir
    if not scope_dir.exists():
        return
    _reject_symlinked_attachment_path(root, rel_dir)
    for dirpath, _dirnames, _filenames in os.walk(scope_dir, topdown=False):
        try:
            Path(dirpath).rmdir()
        except OSError as exc:
            if exc.errno != errno.ENOENT:
                raise


def validate_attachment_name(name: str) -> str:
    """Return a portable attachment filename or raise ``ValueError``."""
    if not isinstance(name, str) or not name:
        raise ValueError("Attachment name must be a non-empty filename")
    if name != name.strip():
        raise ValueError("Attachment name must not start or end with whitespace")
    if name in {".", ".."} or "/" in name or "\\" in name:
        raise ValueError("Attachment name must be a filename, not a path")
    if name.startswith("."):
        raise ValueError("Attachment name must not be dot-prefixed")
    try:
        validate_windows_portable_filename_segment(name)
    except ValueError as exc:
        raise ValueError(f"Attachment name is not portable: {exc}") from exc
    unsafe = sorted(set(name) & _OBSIDIAN_UNSAFE_FILENAME_CHARS)
    if unsafe:
        raise ValueError(
            "Attachment name contains characters that are unsafe in Obsidian embeds: "
            + "".join(unsafe)
        )
    if name.casefold().endswith(".md"):
        raise ValueError("Markdown files are artefacts, not attachments")
    if len(name.encode("utf-8")) > 255:
        raise ValueError("Attachment name exceeds the portable 255-byte filename limit")
    return name


def decode_attachment_base64(content_base64: str) -> bytes:
    """Decode strict base64 content within the public attachment size bound."""
    if not isinstance(content_base64, str):
        raise ValueError("content_base64 must be a base64 string")
    max_encoded_chars = 4 * ((MAX_ATTACHMENT_BYTES + 2) // 3)
    if len(content_base64) > max_encoded_chars:
        raise ValueError(
            f"Attachment base64 exceeds the {MAX_ATTACHMENT_BYTES}-byte decoded maximum"
        )
    try:
        encoded = content_base64.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("content_base64 must contain ASCII base64 data only") from exc
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("content_base64 is not valid base64") from exc
    return validate_attachment_content(content)


def validate_attachment_content(content: bytes) -> bytes:
    """Return byte content after enforcing its public size contract."""
    if not isinstance(content, bytes):
        raise ValueError("Attachment content must be bytes")
    if len(content) > MAX_ATTACHMENT_BYTES:
        raise ValueError(
            f"Attachment is {len(content)} bytes; maximum is {MAX_ATTACHMENT_BYTES}"
        )
    return content


def read_attachment_file(source: str | os.PathLike[str]) -> bytes:
    """Read a caller-owned source file without granting it vault-path semantics."""
    source_path = Path(source)
    try:
        source_stat = source_path.stat()
    except OSError as exc:
        raise ValueError(f"Cannot read attachment source: {exc}") from exc
    if not stat.S_ISREG(source_stat.st_mode):
        raise ValueError("Attachment source must be a regular file")
    if source_stat.st_size > MAX_ATTACHMENT_BYTES:
        raise ValueError(
            f"Attachment is {source_stat.st_size} bytes; maximum is {MAX_ATTACHMENT_BYTES}"
        )
    try:
        with source_path.open("rb") as handle:
            content = handle.read(MAX_ATTACHMENT_BYTES + 1)
    except OSError as exc:
        raise ValueError(f"Cannot read attachment source: {exc}") from exc
    return validate_attachment_content(content)


def _reject_symlinked_attachment_path(root: Path, rel_path: Path) -> None:
    current = root
    for component in rel_path.parts:
        current /= component
        if current.is_symlink():
            raise ValueError(
                "Attachment namespace must not contain symlinks: "
                f"{current.relative_to(root).as_posix()}"
            )


def _existing_attachment_matches(path: Path, payload: bytes) -> bool:
    try:
        if path.stat().st_size != len(payload):
            return False
        with path.open("rb") as handle:
            offset = 0
            payload_view = memoryview(payload)
            while offset < len(payload):
                chunk = handle.read(
                    min(_COMPARE_CHUNK_BYTES, len(payload) - offset)
                )
                if not chunk or chunk != payload_view[offset:offset + len(chunk)]:
                    return False
                offset += len(chunk)
            return handle.read(1) == b""
    except OSError as exc:
        raise ValueError(f"Cannot read existing attachment: {exc}") from exc


def upload_attachment(
    vault_root: str | os.PathLike[str],
    router: dict | None,
    *,
    destination_key: str,
    name: str,
    content: bytes,
) -> dict:
    """Create one retry-safe file beneath a derived attachment scope."""
    plan = plan_attachment_upload(
        vault_root,
        router,
        destination_key=destination_key,
        name=name,
        content=content,
    )
    if plan.would_create:
        safe_write_via(
            plan.target,
            lambda handle: handle.write(plan.content),
            mode="wb",
            bounds=Path(vault_root).resolve(),
            follow_symlinks=False,
            exclusive=True,
        )
    return {
        "destination": plan.destination,
        "path": plan.path,
        "embed": plan.embed,
        "bytes": plan.bytes,
        "sha256": plan.sha256,
        "created": plan.would_create,
    }


def plan_attachment_upload(
    vault_root: str | os.PathLike[str],
    router: dict | None,
    *,
    destination_key: str,
    name: str,
    content: bytes,
) -> AttachmentUploadPlan:
    """Validate one upload and report its path/idempotency without writing."""
    destination = resolve_attachment_destination(router, destination_key)
    filename = validate_attachment_name(name)
    payload = validate_attachment_content(content)
    root = Path(vault_root).resolve()
    rel_path = ATTACHMENTS_REL_DIR / destination["folder"] / filename
    target = root / rel_path
    _reject_symlinked_attachment_path(root, rel_path)
    resolved_target = Path(
        resolve_and_check_bounds(target, root, follow_symlinks=False)
    )
    digest = hashlib.sha256(payload).hexdigest()

    would_create = not resolved_target.exists()
    if not would_create:
        if not resolved_target.is_file():
            raise ValueError(f"Attachment target is not a file: {rel_path.as_posix()}")
        if not _existing_attachment_matches(resolved_target, payload):
            raise FileExistsError(
                f"Attachment already exists with different content: {rel_path.as_posix()}"
            )

    path = rel_path.as_posix()
    return AttachmentUploadPlan(
        destination=destination,
        path=path,
        embed=f"![[{path}]]",
        bytes=len(payload),
        sha256=digest,
        would_create=would_create,
        target=target,
        content=payload,
    )


def upload_attachment_base64(
    vault_root: str | os.PathLike[str],
    router: dict | None,
    *,
    destination_key: str,
    name: str,
    content_base64: str,
) -> dict:
    """Decode and upload one attachment for transport-oriented callers."""
    return upload_attachment(
        vault_root,
        router,
        destination_key=destination_key,
        name=name,
        content=decode_attachment_base64(content_base64),
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Upload a file into _Assets/Attachments without direct vault access."
    )
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--file", help="Caller-owned source file to upload")
    source_group.add_argument("--content-base64", help="Base64-encoded attachment bytes")
    parser.add_argument(
        "--destination-key",
        required=True,
        help="Living artefact key (type/key or type~key) or standalone folder key",
    )
    parser.add_argument(
        "--name",
        help="Destination filename; defaults to the --file basename and is required for base64",
    )
    parser.add_argument("--vault")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    using_base64 = args.content_base64 is not None
    if using_base64 and args.name is None:
        parser.error("--name is required with --content-base64")
    name = args.name if args.name is not None else Path(args.file).name

    try:
        vault_root = str(find_vault_root(args.vault))
        router = None
        if attachment_destination_requires_router(args.destination_key):
            router = load_fresh_compiled_router(vault_root)
            if "error" in router:
                raise ValueError(router["error"])
        resolve_attachment_destination(router, args.destination_key)
        validate_attachment_name(name)
        content = (
            decode_attachment_base64(args.content_base64)
            if using_base64
            else read_attachment_file(args.file)
        )
        with vault_mutation_lock(vault_root):
            result = upload_attachment(
                vault_root,
                router,
                destination_key=args.destination_key,
                name=name,
                content=content,
            )
    except (FileExistsError, MutationLockError, OSError, ValueError) as exc:
        parser.error(public_mutation_error_message(exc))

    if args.json:
        print(json.dumps(result))
    else:
        print(result["path"])


if __name__ == "__main__":
    main()
