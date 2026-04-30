"""Safe file writes — atomic, symlink-aware, bounds-checked."""

import json
import os
import tempfile
from pathlib import Path


_WRITE_ALLOWED_UNDERSCORE = {"_Temporal", "_Config"}


def resolve_and_check_bounds(path, bounds, *, follow_symlinks=True):
    """Resolve symlinks and verify the target is within *bounds*.

    Returns the resolved real path as a string.
    Raises ValueError if the target resolves outside bounds or if
    *follow_symlinks* is False and the path is a symlink.
    """
    target = str(path)
    if follow_symlinks:
        target = os.path.realpath(target)
    elif os.path.islink(target):
        raise ValueError(f"Refusing to follow symlink: {path}")

    real_bounds = os.path.realpath(str(bounds))
    # Append os.sep so "/home/foo" doesn't match "/home/foobar"
    if target != real_bounds and not target.startswith(real_bounds + os.sep):
        raise ValueError(
            f"Path {target} resolves outside allowed boundary {real_bounds}"
        )
    return target


def check_write_allowed(rel_path):
    """Raise ValueError if rel_path targets a protected folder.

    Rules:
    - Dot-prefixed top-level folders: always blocked.
    - Underscore-prefixed top-level folders: blocked unless in allowlist.
    """
    parts = Path(rel_path).parts
    if not parts:
        raise ValueError("Empty path")
    top = parts[0]
    if top.startswith("."):
        raise ValueError(
            f"Cannot write to dot-prefixed folder '{top}' — system directory."
        )
    if top.startswith("_") and top not in _WRITE_ALLOWED_UNDERSCORE:
        raise ValueError(
            f"Cannot write to '{top}' — protected folder. "
            f"Allowed underscore folders: {sorted(_WRITE_ALLOWED_UNDERSCORE)}"
        )


def check_not_in_brain_core(path, vault_root):
    """Raise ValueError if path resolves inside .brain-core/."""
    real = os.path.realpath(os.path.join(vault_root, path) if not os.path.isabs(path) else path)
    protected = os.path.realpath(os.path.join(vault_root, ".brain-core"))
    if real == protected or real.startswith(protected + os.sep):
        raise ValueError(f"Cannot modify files inside .brain-core/: {path}")


def _resolve_write_target(path, *, bounds=None, follow_symlinks=True):
    """Resolve symlinks and bounds for a write target."""
    if bounds is not None:
        target = resolve_and_check_bounds(path, bounds,
                                          follow_symlinks=follow_symlinks)
    elif follow_symlinks:
        target = os.path.realpath(str(path))
    else:
        target = str(path)
        if os.path.islink(target):
            raise ValueError(f"Refusing to follow symlink: {path}")
    return target


def safe_write_via(path, writer, *, mode="wb", encoding="utf-8", bounds=None,
                   follow_symlinks=True, exclusive=False):
    """Atomic file write for callback-driven serializers.

    Opens a unique sibling tempfile in the target directory, invokes *writer*
    with the writable handle, flushes and fsyncs it, then atomically replaces
    the destination via ``os.replace``.

    **Concurrency note:** each call uses a unique tempfile in the destination
    directory, so same-process concurrent writes to the same target do not
    collide on a shared ``.tmp`` path. Higher-level last-writer-wins races are
    still possible when callers mutate the same file concurrently.
    """
    target = _resolve_write_target(path, bounds=bounds,
                                   follow_symlinks=follow_symlinks)

    if exclusive and os.path.exists(target):
        raise FileExistsError(f"File already exists: {target}")

    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        prefix=os.path.basename(target) + ".",
        suffix=".tmp",
        dir=os.path.dirname(target) or ".",
    )
    try:
        open_kwargs = {}
        if "b" not in mode:
            open_kwargs["encoding"] = encoding
        with os.fdopen(fd, mode, **open_kwargs) as handle:
            writer(handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    return target


def safe_write(path, content, *, encoding="utf-8", bounds=None,
               follow_symlinks=True, exclusive=False):
    """Atomic file write with optional symlink resolution and bounds checking.

    Writes *content* through ``safe_write_via`` and returns the resolved path
    that was actually written to.
    """
    return safe_write_via(
        path,
        lambda handle: handle.write(content),
        mode="w",
        encoding=encoding,
        bounds=bounds,
        follow_symlinks=follow_symlinks,
        exclusive=exclusive,
    )


def safe_write_json(path, data, *, indent=2, bounds=None,
                    follow_symlinks=True):
    """Atomic JSON write using the shared callback-driven write kernel."""
    def _dump_json(handle):
        json.dump(data, handle, indent=indent, ensure_ascii=False)
        handle.write("\n")

    return safe_write_via(
        path,
        _dump_json,
        mode="w",
        bounds=bounds,
        follow_symlinks=follow_symlinks,
    )


def make_temp_path(suffix=".md"):
    """Return a safe, writable temp file path.

    Uses the platform's native temp directory. The returned path is accepted
    by resolve_body_file and will be cleaned up after use.
    """
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    return path


def temp_body_file_cleanup_path(body_file):
    """Return the cleanup path for a staged temp ``body_file``, if any."""
    if not body_file:
        return None

    abs_path = os.path.realpath(body_file)
    tmp_roots = {os.path.realpath(tempfile.gettempdir()), os.path.realpath("/tmp")}
    for root in tmp_roots:
        try:
            resolve_and_check_bounds(abs_path, root)
            return abs_path
        except ValueError:
            pass
    return None


def cleanup_temp_body_file(path):
    """Best-effort removal for a staged temp ``body_file`` path."""
    if not path:
        return
    try:
        os.remove(path)
    except OSError:
        pass


def resolve_body_file(body, body_file, *, vault_root=None, cleanup_path=None):
    """Return body content, reading from body_file if provided.

    Raises ValueError if both are specified, the file cannot be read,
    or (when *vault_root* is set) the path resolves outside both the
    vault and the system temp directory.

    Returns (body, cleanup_path).  *cleanup_path* is set only when the
    file was read from the system temp directory (caller should delete);
    it is None for vault files or when body_file was not used. Callers that
    already know the staged temp cleanup path may pass it via *cleanup_path*
    to avoid recomputing temp-body ownership checks.
    """
    if body_file and body:
        raise ValueError("Cannot specify both 'body' and 'body_file'. Use one or the other.")
    if not body_file:
        return body, None

    abs_path = os.path.realpath(body_file)

    cleanup_path = None
    if vault_root is not None:
        if cleanup_path is None:
            cleanup_path = temp_body_file_cleanup_path(body_file)
        if cleanup_path is None:
            resolve_and_check_bounds(abs_path, vault_root)

    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            return f.read(), cleanup_path
    except FileNotFoundError:
        raise ValueError(f"body_file not found: {body_file}")
    except Exception as e:
        raise ValueError(f"Failed to read body_file: {e}")
