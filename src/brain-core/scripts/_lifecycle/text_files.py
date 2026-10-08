"""Filesystem-owned scanned set shared by vault text checks and repair."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
import stat

from _common import BOOTSTRAP_VARIANTS, LOCAL_OVERRIDE_VARIANTS, is_system_dir
from _skill_library.tracking import load_tracking, TrackingError, TRACKING_REL


ROOT_BOOTSTRAP_VARIANTS = {
    variant
    for variants in (
        *BOOTSTRAP_VARIANTS.values(),
        *LOCAL_OVERRIDE_VARIANTS.values(),
    )
    for variant in variants
}

_TEXT_SYSTEM_ROOTS = {"_Temporal", "_Archive", "_Config", "_Plugins"}


def _mode(path: Path, on_error) -> int:
    try:
        return path.lstat().st_mode
    except FileNotFoundError:
        return 0
    except OSError as exc:
        if on_error is None:
            raise
        on_error(path, exc)
        return 0


def _children(path: Path, on_error) -> list[Path]:
    try:
        return sorted(path.iterdir())
    except FileNotFoundError:
        return []
    except OSError as exc:
        if on_error is None:
            raise
        on_error(path, exc)
        return []


def iter_vault_text_files(vault_root: str | Path, *, on_error=None) -> Iterator[str]:
    """Yield vault-relative text paths without a router or following symlinks.

    Scan artefact, config and plugin Markdown and recognised root bootstrap
    variants. Source-managed skill packages are excluded in their entirety.
    Disappearance is harmless. A diagnostic caller may collect scan errors;
    without that callback an incomplete inventory refuses repair planning.
    """
    root = Path(vault_root)
    if not stat.S_ISDIR(_mode(root, on_error)):
        return
    try:
        managed = load_tracking(root)["managed"]
    except (TrackingError, UnicodeDecodeError) as exc:
        if on_error is None:
            raise
        on_error(root / TRACKING_REL, exc)
        managed_roots = {root / "_Config" / "Skills"}
    else:
        managed_roots = {
            root / "_Config" / "Skills" / name
            for name in managed
        }

    def markdown_under(directory: Path) -> Iterator[str]:
        for path in _children(directory, on_error):
            if path.name.startswith(".") or path in managed_roots:
                continue
            mode = _mode(path, on_error)
            if stat.S_ISDIR(mode):
                yield from markdown_under(path)
            elif stat.S_ISREG(mode) and path.name.endswith(".md"):
                yield path.relative_to(root).as_posix()

    for path in _children(root, on_error):
        if path.name.startswith("."):
            continue
        mode = _mode(path, on_error)
        if stat.S_ISREG(mode) and path.name in ROOT_BOOTSTRAP_VARIANTS:
            yield path.name
        elif stat.S_ISDIR(mode) and (
            not is_system_dir(path.name) or path.name in _TEXT_SYSTEM_ROOTS
        ):
            yield from markdown_under(path)
