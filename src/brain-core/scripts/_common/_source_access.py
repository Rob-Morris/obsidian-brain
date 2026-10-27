"""Canonical admission for public document sources, separate from byte decoding.

These path-based checks cover static layouts, not hostile concurrent replacement.
Locators and supplied snapshots never confer source authority.
"""
from pathlib import Path
import stat
import os

PUBLIC_BRAIN_CORE_FILES = frozenset({
    "colours.md", "guide.md", "index.md", "md-bootstrap.md", "session-core.md",
    "plugins.md", "triggers.md",
})
PUBLIC_BRAIN_CORE_TREES = frozenset({
    "artefact-library", "client-adapters", "skills", "standards",
})


class SourceAccessError(ValueError):
    """Content-safe, non-retryable source-policy refusal."""


def _resolve_source(vault_root, path, *, archived):
    try:
        root = Path(vault_root).resolve()
        resolved = (root / path).resolve()
        parts = resolved.relative_to(root).parts
    except (ValueError, OSError, RuntimeError):
        raise SourceAccessError("Source is outside the permitted vault namespace") from None
    if not parts:
        raise SourceAccessError("Source must identify a regular vault file")
    archive = "_Archive" in parts
    if archive != archived:
        raise SourceAccessError(
            "Source is outside the permitted archive namespace" if archived else
            "Source is archived; use the explicit archived operation"
        )
    if archived:
        try:
            requested = Path(path)
            if requested.is_absolute():
                try:
                    requested = requested.relative_to(Path(vault_root).absolute())
                except ValueError:
                    requested = requested.relative_to(root)
            prefix = requested.parts[:requested.parts.index("_Archive") + 1]
            scope = Path(os.path.abspath(root.joinpath(*prefix)))
            scope.relative_to(root)
            resolved.relative_to(scope)
        except (ValueError, OSError, RuntimeError):
            raise SourceAccessError("Source leaves the requested archive scope") from None
    if parts[0] == ".brain-core" and not archived:
        permitted = (len(parts) == 2 and parts[1] in PUBLIC_BRAIN_CORE_FILES) or (
            len(parts) > 2 and parts[1] in PUBLIC_BRAIN_CORE_TREES
        )
        if not permitted or any(part.startswith(".") for part in parts[1:]):
            raise SourceAccessError("Source is outside the public Brain Core namespace")
    elif any(part.startswith(".") for part in parts):
        raise SourceAccessError("Source is outside the public vault namespace")
    try:
        mode = resolved.stat().st_mode
    except FileNotFoundError:
        raise FileNotFoundError("Vault source file not found") from None
    except OSError:
        raise SourceAccessError("Source cannot be admitted as a regular vault file") from None
    if not stat.S_ISREG(mode):
        raise SourceAccessError("Source must identify a regular vault file")
    return resolved


def resolve_public_source(vault_root, path) -> Path:
    """Admit a final canonical, regular, non-private active/public source."""
    return _resolve_source(vault_root, path, archived=False)


def resolve_archive_source(vault_root, path) -> Path:
    """Admit a regular non-private current or legacy canonical archive source."""
    return _resolve_source(vault_root, path, archived=True)


def resolve_content_source(vault_root, path) -> Path:
    """Select explicit archive authority for archive-aware internal owners."""
    # Keep lexical operation selection separate from canonical admission.
    resolver = resolve_archive_source if "_Archive" in Path(path).parts else resolve_public_source
    return resolver(vault_root, path)


def read_public_content(vault_root, path, *, convert_lossless=False):
    """Infer Markdown before admission, retaining the legacy missing result."""
    from ._artefacts import MissingFileResult, read_exact_file_content
    candidate = str(path)
    if not candidate.endswith(".md"):
        markdown = candidate + ".md"
        try:
            if (Path(vault_root) / markdown).exists():
                candidate = markdown
        except (OSError, RuntimeError):
            raise SourceAccessError("Cannot select the requested vault source") from None
    try:
        source = resolve_public_source(vault_root, candidate)
    except FileNotFoundError:
        return MissingFileResult(candidate)
    return read_exact_file_content(source, convert_lossless=convert_lossless)


def resolve_named_destination(vault_root, path) -> Path:
    """Admit a canonical named-document destination, including a missing leaf."""
    from ._filesystem import check_write_allowed
    try:
        root = Path(vault_root).resolve()
        target = (root / path).resolve()
        relative = target.relative_to(root)
    except (ValueError, OSError, RuntimeError):
        raise SourceAccessError("Document destination is outside the permitted vault namespace") from None
    if not relative.parts or any(p.startswith(".") for p in relative.parts) or "_Archive" in relative.parts:
        raise SourceAccessError("Document destination is outside the writable document namespace")
    check_write_allowed(str(relative))
    try:
        mode = target.stat().st_mode
    except FileNotFoundError:
        return target
    except OSError:
        raise SourceAccessError("Cannot admit the requested document destination") from None
    if not stat.S_ISREG(mode):
        raise SourceAccessError("Document destination must identify a regular file")
    return target


def resolve_readme_source(vault_root, reference) -> Path:
    """The unmanaged-file capability is exactly the existing root README.md."""
    if reference != "README.md":
        raise SourceAccessError("File documents support only the existing root README.md")
    target = resolve_public_source(vault_root, reference)
    if target != Path(vault_root).resolve() / "README.md":
        raise SourceAccessError("README.md must remain the exact root file")
    return target
